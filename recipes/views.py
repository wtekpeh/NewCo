import pandas as pd

from django.db import transaction

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response


from .models import Recipe, RecipeIngredient
from .serializers import RecipeSerializer

from recipe_engine.scaling import predict_ingredients
from recipe_engine.scaling import predict_with_scales
from recipe_engine.services.scale_store import load_scales_df

from drf_spectacular.utils import extend_schema
from .serializers import RecipePredictRequestSerializer, RecipePredictResponseSerializer


@extend_schema(responses=RecipeSerializer(many=True))
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def list_recipes(request):
    """
    GET /api/recipes/
    Read-only endpoint. Returns active recipes (with ingredients).
    """
    qs = Recipe.objects.filter(is_active=True).order_by("name")
    return Response(RecipeSerializer(qs, many=True).data, status=status.HTTP_200_OK)


@extend_schema(
    request=RecipePredictRequestSerializer,
    responses=RecipePredictResponseSerializer,
)
@api_view(["POST"])
@permission_classes([IsAuthenticated])
def predict_recipe(request, pk: int):
    """
    POST /api/recipes/<id>/predict/

    Body:
      {
        "n_people": 25,
        "options": {
          "protein": "FRESH CHICKEN"
        }
      }

    Atomicity:
      - We wrap all DB reads in a transaction.atomic() block
        so recipe + ingredient rows are read consistently.
      - Later, when we add "log prediction / cook batch" writes,
        we can keep them in this same atomic block.
    """
    # 1) Parse inputs
    try:
        n_people = float(request.data.get("n_people"))
    except (TypeError, ValueError):
        return Response(
            {"detail": "n_people must be a number."}, status=status.HTTP_400_BAD_REQUEST
        )

    if n_people <= 0:
        return Response(
            {"detail": "n_people must be > 0."}, status=status.HTTP_400_BAD_REQUEST
        )

    options = request.data.get("options") or {}
    protein_type = (options.get("protein") or "").strip()

    # 2) Read recipe + ingredients atomically (consistent snapshot)
    with transaction.atomic():
        try:
            recipe = Recipe.objects.get(pk=pk, is_active=True)
        except Recipe.DoesNotExist:
            return Response(
                {"detail": "Recipe not found."}, status=status.HTTP_404_NOT_FOUND
            )

        ingredients_qs = RecipeIngredient.objects.filter(
            recipe=recipe, is_active=True
        ).order_by("item_no", "id")

        if not ingredients_qs.exists():
            return Response(
                {"detail": "Recipe has no active ingredients."},
                status=status.HTTP_404_NOT_FOUND,
            )

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

    # 3) Detect protein choices
    # We support BOTH styles:
    # - group == "protein"
    # - option_group == "protein"
    protein_rows = df[
        (df["group"] == "protein")
        | (df["option_group"].fillna("").str.lower() == "protein")
    ]
    protein_set = set(protein_rows["ingredient"].dropna().astype(str).tolist())

    # 4) Enforce protein selection if proteins exist
    if protein_set and not protein_type:
        return Response(
            {
                "detail": "protein option is required for this recipe.",
                "protein_choices": sorted(protein_set),
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    if protein_set and protein_type:
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

    # 5) Predict (adaptive if scales exist, otherwise fallback)

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

        # unify column names so rest of code remains unchanged
        pred["pred_g"] = pred["pred_g_new"]
        pred["pred_kg"] = pred["pred_kg_new"]
    else:
        pred = predict_ingredients(
            df_recipe=df,
            n_people=n_people,
            protein_type=protein_type if protein_set else None,
            protein_set=protein_set if protein_set else None,
        )

    # 5b) Safety clamp using per-person bounds (if provided)
    # final_g = clamp(pred_g, n_people*min_pp, n_people*max_pp)
    pred["final_g"] = pred["pred_g"].astype(float)
    pred["was_clamped"] = False

    for idx, row in pred.iterrows():
        min_pp = row.get("min_per_person_g")
        max_pp = row.get("max_per_person_g")

        # --- IMPORTANT: don't clamp excluded proteins back up ---
        # If this ingredient is a protein and it's not the chosen protein, keep it at 0 and skip bounds.
        is_protein_row = (
            str(row.get("group", "")).lower() == "protein"
            or str(row.get("option_group", "")).lower() == "protein"
        )

        if is_protein_row and protein_set:
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

    # 6) JSON response
    # Load scales once (same logic already used above)
    scales_df = load_scales_df()
    scale_map = {}

    if scales_df is not None and not scales_df.empty:
        scale_map = dict(zip(scales_df["ingredient"], scales_df["s"]))

    items = []

    for _, row in pred.iterrows():
        ingredient = row["ingredient"]

        # Base prediction (recompute safely)
        base_pred_g = float(row["q10_g"]) * (float(n_people) / 10.0) ** float(
            row["b"]
        ) + float(row["c_g"])
        base_pred_kg = base_pred_g / 1000.0

        scale_used = float(scale_map.get(ingredient, 1.0))
        used_calibration = ingredient in scale_map

        items.append(
            {
                "ingredient": ingredient,
                "group": row.get("group"),
                "q10_g": float(row["q10_g"]),
                "b": float(row["b"]),
                "c_g": float(row["c_g"]),
                # Explainability
                "base_pred_g": base_pred_g,
                "base_pred_kg": base_pred_kg,
                "scale_used": scale_used,
                "used_calibration": used_calibration,
                # Final values
                "pred_g": float(row["pred_g"]),
                "pred_kg": float(row["pred_kg"]),
                # Keep existing fields
                "final_g": float(row["final_g"]),
                "final_kg": float(row["final_kg"]),
                "was_clamped": bool(row["was_clamped"]),
            }
        )

    return Response(
        {
            "recipe": {"id": recipe.id, "name": recipe.name},
            "n_people": n_people,
            "options": options,
            "items": items,
        },
        status=status.HTTP_200_OK,
    )
