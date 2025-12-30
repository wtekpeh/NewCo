import pandas as pd

from django.db import transaction

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from django.views.decorators.csrf import csrf_exempt


from .models import Recipe, RecipeIngredient
from .serializers import RecipeSerializer

from recipe_engine.scaling import predict_ingredients

from drf_spectacular.utils import extend_schema, OpenApiParameter
from .serializers import RecipePredictRequestSerializer, RecipePredictResponseSerializer



@extend_schema(responses=RecipeSerializer(many=True))
@api_view(["GET"])
@permission_classes([AllowAny])
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

@csrf_exempt
@api_view(["POST"])
@permission_classes([AllowAny])
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
        return Response({"detail": "n_people must be a number."}, status=status.HTTP_400_BAD_REQUEST)

    if n_people <= 0:
        return Response({"detail": "n_people must be > 0."}, status=status.HTTP_400_BAD_REQUEST)

    options = request.data.get("options") or {}
    protein_type = (options.get("protein") or "").strip()

    # 2) Read recipe + ingredients atomically (consistent snapshot)
    with transaction.atomic():
        try:
            recipe = Recipe.objects.get(pk=pk, is_active=True)
        except Recipe.DoesNotExist:
            return Response({"detail": "Recipe not found."}, status=status.HTTP_404_NOT_FOUND)

        ingredients_qs = (
            RecipeIngredient.objects
            .filter(recipe=recipe, is_active=True)
            .order_by("item_no", "id")
        )

        if not ingredients_qs.exists():
            return Response({"detail": "Recipe has no active ingredients."}, status=status.HTTP_404_NOT_FOUND)

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

    # 5) Predict (pure python)
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
        pred.at[idx, "was_clamped"] = (val != float(row["pred_g"]))

    pred["final_kg"] = pred["final_g"].astype(float) / 1000.0


    # 6) JSON response
    items = []
    for _, row in pred.iterrows():
        items.append(
            {
                "ingredient": row["ingredient"],
                "group": row.get("group"),
                "q10_g": float(row["q10_g"]),
                "b": float(row["b"]),
                "c_g": float(row["c_g"]),
                "pred_g": float(row["pred_g"]),
                "pred_kg": float(row["pred_kg"]),
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
