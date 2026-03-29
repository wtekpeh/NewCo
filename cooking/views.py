import pandas as pd

from django.db import transaction
from django.utils import timezone

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response

from recipes.models import Recipe, RecipeIngredient
from recipe_engine.scaling import predict_ingredients, sum_prediction_frames
from recipe_engine.scaling import predict_with_scales
from recipe_engine.services.scale_store import load_scales_df

from .models import CookBatch, CookBatchItem
from .serializers import (
    CookBatchSerializer,
    CookBatchCreateRequestSerializer,
    CookBatchActualsUpdateRequestSerializer,
)

from drf_spectacular.utils import extend_schema, OpenApiResponse

from django.shortcuts import get_object_or_404
from accounts.models import Branch
from accounts.permissions import (
    can_create_batch,
    can_view_batch,
    can_update_batch,
    has_global_access,
)

from recipe_engine.services.scale_store import recalibrate_and_store


@extend_schema(
    methods=["POST"],
    request=CookBatchCreateRequestSerializer,
    responses={
        201: CookBatchSerializer,
        400: OpenApiResponse(description="Bad Request"),
        404: OpenApiResponse(description="Recipe not found / no ingredients"),
    },
)
@api_view(["POST"])
@permission_classes([IsAuthenticated])
def create_cook_batch(request):
    """
    POST /api/cooking/batches/

    Atomic guarantee:
      - Either CookBatch + ALL items are saved
      - Or nothing is saved
    """

    # ----------------------------
    # PHASE 1 (NO TRANSACTION): validate + compute everything
    # ----------------------------

    req = CookBatchCreateRequestSerializer(data=request.data)
    if not req.is_valid():
        return Response(req.errors, status=status.HTTP_400_BAD_REQUEST)

    recipe_id = req.validated_data["recipe_id"]
    branch_id = req.validated_data["branch_id"]
    n_people = int(req.validated_data["n_people"])
    options = req.validated_data.get("options") or {}
    notes = req.validated_data.get("notes", "")

    protein_type = (options.get("protein") or "").strip()

    # New multi-protein spec (optional)
    proteins_spec = options.get("proteins", None)
    multi_proteins = []

    if isinstance(proteins_spec, list):
        for p in proteins_spec:
            if not isinstance(p, dict):
                continue
            name = (p.get("protein") or "").strip()
            cnt = p.get("n_people", None)
            multi_proteins.append({"protein": name, "n_people": cnt})

    # Fetch recipe (outside atomic)
    try:
        recipe = Recipe.objects.get(pk=recipe_id, is_active=True)
    except Recipe.DoesNotExist:
        return Response(
            {"detail": "Recipe not found."}, status=status.HTTP_404_NOT_FOUND
        )

    branch = get_object_or_404(Branch, pk=branch_id, is_active=True)

    if not can_create_batch(request.user, branch):
        return Response(
            {"detail": "You do not have permission to create a batch for this branch."},
            status=status.HTTP_403_FORBIDDEN,
        )

    # Fetch ingredients (outside atomic)
    ingredients_qs = RecipeIngredient.objects.filter(
        recipe=recipe, is_active=True
    ).order_by("item_no", "id")
    if not ingredients_qs.exists():
        return Response(
            {"detail": "Recipe has no active ingredients."},
            status=status.HTTP_404_NOT_FOUND,
        )

    # Build dataframe for engine (outside atomic)
    df = pd.DataFrame(
        list(
            ingredients_qs.values(
                "name",
                "q10_g",
                "b",
                "c_g",
                "min_per_person_g",
                "max_per_person_g",
                "group",
                "option_group",
                "option_value",
            )
        )
    ).rename(columns={"name": "ingredient"})

    # Determine protein set (outside atomic)
    protein_rows = df[
        (df["group"] == "protein")
        | (df["option_group"].fillna("").str.lower() == "protein")
    ]
    protein_set = set(protein_rows["ingredient"].dropna().astype(str).tolist())

    # Validate protein option if needed (outside atomic)
    if protein_set:
        if multi_proteins:
            # Remove blanks
            multi_proteins = [p for p in multi_proteins if p["protein"]]

            if not multi_proteins:
                return Response(
                    {
                        "detail": "protein option is required for this recipe.",
                        "protein_choices": sorted(protein_set),
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # No duplicates
            seen = set()
            for p in multi_proteins:
                key = p["protein"].strip().upper()
                if key in seen:
                    return Response(
                        {
                            "detail": f"Duplicate protein in options.proteins: {p['protein']}"
                        },
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                seen.add(key)

            # Validate counts and sum
            total = 0
            for p in multi_proteins:
                if p["n_people"] is None:
                    return Response(
                        {"detail": f"Missing n_people for protein {p['protein']}"},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                try:
                    n = int(p["n_people"])
                except Exception:
                    return Response(
                        {"detail": f"Invalid n_people for protein {p['protein']}"},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                if n <= 0:
                    return Response(
                        {"detail": f"n_people must be > 0 for protein {p['protein']}"},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                p["n_people"] = n
                total += n

            if total != n_people:
                return Response(
                    {
                        "detail": f"Sum of options.proteins n_people must equal {n_people}.",
                        "expected": n_people,
                        "got": total,
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            normalized_set = {str(x).strip().upper() for x in protein_set}
            for p in multi_proteins:
                if p["protein"].strip().upper() not in normalized_set:
                    return Response(
                        {
                            "detail": f"protein_type must be one of {sorted(normalized_set)}",
                        },
                        status=status.HTTP_400_BAD_REQUEST,
                    )

        else:
            # Single protein mode
            if not protein_type:
                return Response(
                    {
                        "detail": "protein option is required for this recipe.",
                        "protein_choices": sorted(protein_set),
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            normalized_set = {str(x).strip().upper() for x in protein_set}
            chosen = protein_type.strip().upper()

            if chosen not in normalized_set:
                return Response(
                    {
                        "detail": "Invalid protein option.",
                        "protein_choices": sorted(protein_set),
                        "provided": protein_type,
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

    # Predict raw (outside atomic)
    if protein_set and multi_proteins:
        pred_frames = []

        scales_df = load_scales_df()
        use_scales = scales_df is not None and not scales_df.empty

        if use_scales:
            df_pred = predict_with_scales(
                df_recipe=df,
                n_people=p["n_people"],
                df_scales=scales_df,
                protein_type=p["protein"],
                protein_set=protein_set,
            )

            df_pred["pred_g"] = df_pred["pred_g_new"]
            df_pred["pred_kg"] = df_pred["pred_kg_new"]
        else:
            df_pred = predict_ingredients(
                df_recipe=df,
                n_people=p["n_people"],
                protein_type=p["protein"],
                protein_set=protein_set,
            )

            pred_frames.append(df_pred)

        pred = sum_prediction_frames(pred_frames)

        # Bring back recipe params (q10_g, b, c_g, bounds, option_group/value, etc.)
        # sum_prediction_frames returns only ingredient/group + pred_g/pred_kg
        pred_sum = pred

        merge_keys = ["ingredient"]
        if "group" in df.columns and "group" in pred_sum.columns:
            merge_keys = ["ingredient", "group"]

        pred = df.merge(pred_sum, on=merge_keys, how="left")

        # Safety: if something didn't match, fill with 0 (shouldn't happen if ingredient names match)
        pred["pred_g"] = pred["pred_g"].fillna(0.0)
        pred["pred_kg"] = pred["pred_kg"].fillna(0.0)

        # Store a readable label (no schema change)
        protein_type = " + ".join([p["protein"] for p in multi_proteins])
        options["protein"] = protein_type
    else:
        scales_df = load_scales_df()
        use_scales = scales_df is not None and not scales_df.empty

        if use_scales:
            pred = predict_with_scales(
                df_recipe=df,
                n_people=n_people,
                df_scales=scales_df,
                protein_type=protein_type if protein_set else None,
                protein_set=protein_set if protein_set else None,
            )

            # normalize columns
            pred["pred_g"] = pred["pred_g_new"]
            pred["pred_kg"] = pred["pred_kg_new"]
        else:
            pred = predict_ingredients(
                df_recipe=df,
                n_people=n_people,
                protein_type=protein_type if protein_set else None,
                protein_set=protein_set if protein_set else None,
            )

    # Clamp with bounds (outside atomic) - DO NOT re-inflate excluded proteins
    pred["final_g"] = pred["pred_g"].astype(float)
    pred["was_clamped"] = False

    for idx, row in pred.iterrows():
        min_pp = row.get("min_per_person_g")
        max_pp = row.get("max_per_person_g")

        # Keep excluded proteins at 0 and skip clamping
        is_protein_row = (
            str(row.get("group", "")).lower() == "protein"
            or str(row.get("option_group", "")).lower() == "protein"
        )
        if is_protein_row and protein_set and not multi_proteins:
            chosen = (protein_type or "").strip().upper()
            current = str(row.get("ingredient", "")).strip().upper()
            if current != chosen:
                pred.at[idx, "final_g"] = 0.0
                pred.at[idx, "was_clamped"] = False
                continue

        min_total = float(n_people) * float(min_pp) if pd.notna(min_pp) else None
        max_total = float(n_people) * float(max_pp) if pd.notna(max_pp) else None

        val = float(row["pred_g"])
        if min_total is not None and val < min_total:
            val = min_total
        if max_total is not None and val > max_total:
            val = max_total

        pred.at[idx, "final_g"] = val
        pred.at[idx, "was_clamped"] = val != float(row["pred_g"])

    pred["final_kg"] = pred["final_g"].astype(float) / 1000.0

    # Prepare item objects (outside atomic)
    item_objs = []
    for _, r in pred.iterrows():
        item_objs.append(
            CookBatchItem(
                ingredient=str(r["ingredient"]),
                group=str(r.get("group") or ""),
                q10_g=float(r["q10_g"]),
                b=float(r["b"]),
                c_g=float(r["c_g"]),
                min_per_person_g=(
                    float(r["min_per_person_g"])
                    if pd.notna(r.get("min_per_person_g"))
                    else None
                ),
                max_per_person_g=(
                    float(r["max_per_person_g"])
                    if pd.notna(r.get("max_per_person_g"))
                    else None
                ),
                pred_g=float(r["pred_g"]),
                pred_kg=float(r["pred_kg"]),
                final_g=float(r["final_g"]),
                final_kg=float(r["final_kg"]),
                was_clamped=bool(r["was_clamped"]),
            )
        )

    # ----------------------------
    # PHASE 2 (TRANSACTION): write everything or nothing
    # ----------------------------
    with transaction.atomic():
        batch = CookBatch.objects.create(
            recipe=recipe,
            branch=branch,
            created_by=request.user,
            n_people=n_people,
            options=options,
            protein_type=protein_type,
            status="draft",
            notes=notes,
            created_at=timezone.now(),
        )

        for obj in item_objs:
            obj.batch = batch

        CookBatchItem.objects.bulk_create(item_objs)

    # Return AFTER atomic finishes
    batch.refresh_from_db()
    return Response(CookBatchSerializer(batch).data, status=status.HTTP_201_CREATED)


@extend_schema(
    methods=["PATCH"],
    request=CookBatchActualsUpdateRequestSerializer,
    responses={
        200: CookBatchSerializer,
        400: OpenApiResponse(description="Bad Request"),
        404: OpenApiResponse(description="Batch not found / item not in batch"),
    },
)
@api_view(["PATCH"])
@permission_classes([IsAuthenticated])
def update_cook_batch_actuals(request, batch_id: int):
    """
    PATCH /api/cooking/batches/{batch_id}/actuals/

    Atomic guarantee:
      - Either all provided item updates are saved, or none are saved.
    """
    req = CookBatchActualsUpdateRequestSerializer(data=request.data)
    if not req.is_valid():
        return Response(req.errors, status=status.HTTP_400_BAD_REQUEST)

    items_payload = req.validated_data["items"]
    finalize = bool(req.validated_data.get("finalize", False))

    # Ensure batch exists
    try:
        batch = CookBatch.objects.get(pk=batch_id)
    except CookBatch.DoesNotExist:
        return Response(
            {"detail": "Batch not found."}, status=status.HTTP_404_NOT_FOUND
        )

    if not can_update_batch(request.user, batch.branch):
        return Response(
            {"detail": "You do not have permission to update this batch."},
            status=status.HTTP_403_FORBIDDEN,
        )

    # Validate item IDs are unique in payload
    payload_ids = [x["id"] for x in items_payload]
    if len(payload_ids) != len(set(payload_ids)):
        return Response(
            {"detail": "Duplicate item id found in payload."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Atomic write
    with transaction.atomic():
        # Lock batch items we intend to update
        qs = CookBatchItem.objects.select_for_update().filter(
            batch=batch, id__in=payload_ids
        )

        found_ids = set(qs.values_list("id", flat=True))
        missing = [i for i in payload_ids if i not in found_ids]
        if missing:
            return Response(
                {
                    "detail": "Some items do not belong to this batch.",
                    "missing_item_ids": missing,
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        # Apply updates
        payload_map = {x["id"]: x for x in items_payload}

        to_update = []
        for obj in qs:
            p = payload_map[obj.id]
            obj.actual_g = float(p["actual_g"])
            obj.actual_kg = float(p["actual_g"]) / 1000.0
            if "notes" in p:
                obj.notes = p.get("notes", "")
            to_update.append(obj)

        CookBatchItem.objects.bulk_update(to_update, ["actual_g", "actual_kg", "notes"])

        if finalize:
            batch.status = "final"
            batch.save(update_fields=["status"])

    batch.refresh_from_db()
    return Response(CookBatchSerializer(batch).data, status=status.HTTP_200_OK)


@extend_schema(
    methods=["GET"],
    responses={200: CookBatchSerializer(many=True)},
)
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def list_cook_batches(request):
    user = request.user

    if has_global_access(user):
        qs = CookBatch.objects.all().order_by("-created_at")
    else:
        qs = (
            CookBatch.objects.filter(
                branch__staff_roles__staff_profile=user,
                branch__staff_roles__is_active=True,
                branch__staff_roles__branch__is_active=True,
                created_by__is_active=True,
            )
            .distinct()
            .order_by("-created_at")
        )

    return Response(CookBatchSerializer(qs, many=True).data, status=status.HTTP_200_OK)


@extend_schema(
    methods=["GET"],
    responses={200: CookBatchSerializer},
)
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def retrieve_cook_batch(request, batch_id: int):
    batch = get_object_or_404(CookBatch, pk=batch_id)

    if not can_view_batch(request.user, batch.branch):
        return Response(
            {"detail": "You do not have permission to view this batch."},
            status=status.HTTP_403_FORBIDDEN,
        )

    return Response(CookBatchSerializer(batch).data, status=status.HTTP_200_OK)


@extend_schema(
    methods=["POST"],
    request=None,
    responses={
        200: OpenApiResponse(description="Recalibration completed"),
        400: OpenApiResponse(description="Bad Request"),
        403: OpenApiResponse(description="Forbidden"),
    },
)
@api_view(["POST"])
@permission_classes([IsAuthenticated])
def recalibrate_ingredient_scales(request):
    """
    POST /api/cooking/recalibrate/

    Body (all optional):
      {
        "tau_days": 14,
        "branch_id": 3,
        "recipe_id": 2
      }

    Permissions:
      - boss
      - managing_director
    """
    if not has_global_access(request.user):
        return Response(
            {"detail": "You do not have permission to run recalibration."},
            status=status.HTTP_403_FORBIDDEN,
        )

    tau_days = request.data.get("tau_days", 14)
    branch_id = request.data.get("branch_id")
    recipe_id = request.data.get("recipe_id")

    try:
        tau_days = float(tau_days)
    except (TypeError, ValueError):
        return Response(
            {"detail": "tau_days must be a number."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if tau_days <= 0:
        return Response(
            {"detail": "tau_days must be > 0."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if branch_id in ("", None):
        branch_id = None
    else:
        try:
            branch_id = int(branch_id)
        except (TypeError, ValueError):
            return Response(
                {"detail": "branch_id must be an integer."},
                status=status.HTTP_400_BAD_REQUEST,
            )

    if recipe_id in ("", None):
        recipe_id = None
    else:
        try:
            recipe_id = int(recipe_id)
        except (TypeError, ValueError):
            return Response(
                {"detail": "recipe_id must be an integer."},
                status=status.HTTP_400_BAD_REQUEST,
            )

    try:
        saved_df = recalibrate_and_store(
            tau_days=tau_days,
            branch_id=branch_id,
            recipe_id=recipe_id,
        )
    except Exception as exc:
        return Response(
            {"detail": f"Recalibration failed: {exc}"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if saved_df.empty:
        return Response(
            {
                "message": "No scales were generated.",
                "tau_days": tau_days,
                "branch_id": branch_id,
                "recipe_id": recipe_id,
                "scales_updated": 0,
                "items": [],
            },
            status=status.HTTP_200_OK,
        )

    items = []
    for _, row in saved_df.iterrows():
        items.append(
            {
                "ingredient": row["ingredient"],
                "s": float(row["s"]),
                "tau_days": float(row["tau_days"]),
                "sample_count": int(row["sample_count"]),
                "computed_at": str(row["computed_at"]),
            }
        )

    return Response(
        {
            "message": "Recalibration completed successfully.",
            "tau_days": tau_days,
            "branch_id": branch_id,
            "recipe_id": recipe_id,
            "scales_updated": len(items),
            "items": items,
        },
        status=status.HTTP_200_OK,
    )
