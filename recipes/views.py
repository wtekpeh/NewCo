import pandas as pd

from django.db import transaction

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.decorators import parser_classes


from .models import Recipe, RecipeIngredient, IngredientCategory
from .serializers import (
    RecipeSerializer,
    RecipePredictRequestSerializer,
    RecipePredictResponseSerializer,
    RecipeWriteSerializer,
    RecipeIngredientSerializer,
    RecipeIngredientWriteSerializer,
    RecipeCSVRowSerializer,
    ProteinChoiceListResponseSerializer,
    IngredientCategorySerializer,
)

from recipe_engine.scaling import predict_ingredients
from recipe_engine.scaling import predict_with_scales
from recipe_engine.services.scale_store import load_best_scales_df

from drf_spectacular.utils import extend_schema
from django.shortcuts import get_object_or_404
from accounts.permissions import can_manage_recipes

RECIPE_CSV_REQUIRED_COLUMNS = [
    "recipe_name",
    "recipe_description",
    "recipe_is_active",
    "item_no",
    "ingredient_name",
    "group",
    "q10_g",
    "b",
    "c_g",
    "min_per_person_g",
    "max_per_person_g",
    "option_group",
    "option_value",
    "ingredient_is_active",
]


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


@extend_schema(responses=ProteinChoiceListResponseSerializer)
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def list_protein_choices(request):
    recipe_id = request.query_params.get("recipe_id")

    qs = RecipeIngredient.objects.filter(
        recipe__is_active=True,
        is_active=True,
    )

    if recipe_id not in (None, ""):
        try:
            recipe_id = int(recipe_id)
        except (TypeError, ValueError):
            return Response(
                {"detail": "recipe_id must be an integer."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        qs = qs.filter(recipe_id=recipe_id)

    protein_option_values = list(
        qs.filter(option_group__iexact="protein")
        .exclude(option_value__isnull=True)
        .exclude(option_value__exact="")
        .values_list("option_value", flat=True)
    )

    protein_group_names = list(
        qs.filter(group="protein")
        .exclude(name__isnull=True)
        .exclude(name__exact="")
        .values_list("name", flat=True)
    )

    normalized_map = {}

    for value in protein_option_values + protein_group_names:
        cleaned = str(value).strip()
        if not cleaned:
            continue

        key = cleaned.upper()
        if key not in normalized_map:
            normalized_map[key] = cleaned

    results = sorted(normalized_map.values(), key=lambda x: x.upper())

    return Response(
        {
            "count": len(results),
            "results": results,
        },
        status=status.HTTP_200_OK,
    )


@extend_schema(
    methods=["GET", "POST"],
    request=IngredientCategorySerializer,
    responses=IngredientCategorySerializer(many=True),
)
@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
@transaction.atomic
def ingredient_category_list_create(request):
    if request.method == "GET":
        qs = IngredientCategory.objects.filter(is_active=True).order_by("name")
        return Response(
            IngredientCategorySerializer(qs, many=True).data,
            status=status.HTTP_200_OK,
        )

    if not can_manage_recipes(request.user):
        return Response(
            {"detail": "You do not have permission to manage ingredient categories."},
            status=status.HTTP_403_FORBIDDEN,
        )

    serializer = IngredientCategorySerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    category = serializer.save()

    return Response(
        IngredientCategorySerializer(category).data,
        status=status.HTTP_201_CREATED,
    )


@extend_schema(
    methods=["PATCH", "DELETE"],
    request=IngredientCategorySerializer,
    responses=IngredientCategorySerializer,
)
@api_view(["PATCH", "DELETE"])
@permission_classes([IsAuthenticated])
@transaction.atomic
def ingredient_category_detail(request, pk: int):
    if not can_manage_recipes(request.user):
        return Response(
            {"detail": "You do not have permission to manage ingredient categories."},
            status=status.HTTP_403_FORBIDDEN,
        )

    category = get_object_or_404(IngredientCategory, pk=pk)

    if request.method == "PATCH":
        serializer = IngredientCategorySerializer(
            category,
            data=request.data,
            partial=True,
        )
        serializer.is_valid(raise_exception=True)
        category = serializer.save()

        return Response(
            IngredientCategorySerializer(category).data,
            status=status.HTTP_200_OK,
        )

    if request.method == "DELETE":
        # Check if any active ingredient is using this category
        if RecipeIngredient.objects.filter(
            category=category,
            is_active=True,
        ).exists():
            return Response(
                {
                    "detail": "Cannot delete category because some ingredients are assigned to it."
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        category.is_active = False
        category.save(update_fields=["is_active"])

        return Response(status=status.HTTP_204_NO_CONTENT)


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

    scales_df = load_best_scales_df(
        branch_id=None,
        recipe_id=pk,
    )

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
    scales_df = load_best_scales_df(
        branch_id=None,
        recipe_id=pk,
    )
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


@extend_schema(
    request=RecipeWriteSerializer,
    responses=RecipeSerializer,
)
@api_view(["POST"])
@permission_classes([IsAuthenticated])
@transaction.atomic
def create_recipe(request):
    if not can_manage_recipes(request.user):
        return Response(
            {"detail": "You do not have permission to manage recipes."},
            status=status.HTTP_403_FORBIDDEN,
        )

    serializer = RecipeWriteSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    recipe = serializer.save()

    return Response(
        RecipeSerializer(recipe).data,
        status=status.HTTP_201_CREATED,
    )


@extend_schema(
    responses=RecipeSerializer,
)
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def recipe_detail(request, pk: int):
    recipe = get_object_or_404(Recipe, pk=pk)

    if request.method == "GET":
        return Response(RecipeSerializer(recipe).data, status=status.HTTP_200_OK)


@extend_schema(
    request=RecipeWriteSerializer,
    responses=RecipeSerializer,
)
@api_view(["PUT", "PATCH", "DELETE"])
@permission_classes([IsAuthenticated])
@transaction.atomic
def recipe_manage_detail(request, pk: int):
    if not can_manage_recipes(request.user):
        return Response(
            {"detail": "You do not have permission to manage recipes."},
            status=status.HTTP_403_FORBIDDEN,
        )

    recipe = get_object_or_404(Recipe, pk=pk)

    if request.method in ["PUT", "PATCH"]:
        partial = request.method == "PATCH"
        serializer = RecipeWriteSerializer(recipe, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        recipe = serializer.save()
        return Response(RecipeSerializer(recipe).data, status=status.HTTP_200_OK)

    if request.method == "DELETE":
        recipe.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(
    responses=RecipeIngredientSerializer(many=True),
)
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def list_recipe_ingredients(request, recipe_id: int):
    recipe = get_object_or_404(Recipe, pk=recipe_id)
    ingredients = recipe.ingredients.all().order_by("item_no", "id")
    return Response(
        RecipeIngredientSerializer(ingredients, many=True).data,
        status=status.HTTP_200_OK,
    )


@extend_schema(
    request=RecipeIngredientWriteSerializer,
    responses=RecipeIngredientSerializer,
)
@api_view(["POST"])
@permission_classes([IsAuthenticated])
@transaction.atomic
def create_recipe_ingredient(request, recipe_id: int):
    if not can_manage_recipes(request.user):
        return Response(
            {"detail": "You do not have permission to manage recipes."},
            status=status.HTTP_403_FORBIDDEN,
        )

    recipe = get_object_or_404(Recipe, pk=recipe_id)

    payload = request.data.copy()
    payload["recipe"] = recipe.id

    serializer = RecipeIngredientWriteSerializer(data=payload)
    serializer.is_valid(raise_exception=True)
    ingredient = serializer.save()

    return Response(
        RecipeIngredientSerializer(ingredient).data,
        status=status.HTTP_201_CREATED,
    )


@extend_schema(
    responses=RecipeIngredientSerializer,
)
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def recipe_ingredient_detail(request, pk: int):
    ingredient = get_object_or_404(RecipeIngredient, pk=pk)
    return Response(
        RecipeIngredientSerializer(ingredient).data,
        status=status.HTTP_200_OK,
    )


@extend_schema(
    request=RecipeIngredientWriteSerializer,
    responses=RecipeIngredientSerializer,
)
@api_view(["PUT", "PATCH", "DELETE"])
@permission_classes([IsAuthenticated])
@transaction.atomic
def recipe_ingredient_manage_detail(request, pk: int):
    if not can_manage_recipes(request.user):
        return Response(
            {"detail": "You do not have permission to manage recipes."},
            status=status.HTTP_403_FORBIDDEN,
        )

    ingredient = get_object_or_404(RecipeIngredient, pk=pk)

    if request.method in ["PUT", "PATCH"]:
        partial = request.method == "PATCH"

        payload = request.data.copy()
        if "recipe" not in payload:
            payload["recipe"] = ingredient.recipe_id

        serializer = RecipeIngredientWriteSerializer(
            ingredient,
            data=payload,
            partial=partial,
        )
        serializer.is_valid(raise_exception=True)
        ingredient = serializer.save()

        return Response(
            RecipeIngredientSerializer(ingredient).data,
            status=status.HTTP_200_OK,
        )

    if request.method == "DELETE":
        ingredient.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


@api_view(["PATCH"])
@permission_classes([IsAuthenticated])
@transaction.atomic
def assign_category_to_recipe_ingredient(request, pk: int):
    """
    PATCH /api/recipe-ingredients/{id}/assign-category/

    Body:
    {
        "category_id": 1
    }
    """

    if not can_manage_recipes(request.user):
        return Response(
            {"detail": "You do not have permission to assign categories."},
            status=status.HTTP_403_FORBIDDEN,
        )

    ingredient = get_object_or_404(RecipeIngredient, pk=pk)

    category_id = request.data.get("category_id")

    if category_id is None:
        return Response(
            {"detail": "category_id is required."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if category_id == "":
        ingredient.category = None
        ingredient.save(update_fields=["category"])
        return Response(
            RecipeIngredientSerializer(ingredient).data,
            status=status.HTTP_200_OK,
        )

    try:
        category = IngredientCategory.objects.get(pk=category_id, is_active=True)
    except IngredientCategory.DoesNotExist:
        return Response(
            {"detail": "Category not found or inactive."},
            status=status.HTTP_404_NOT_FOUND,
        )

    ingredient.category = category
    ingredient.save(update_fields=["category"])

    return Response(
        RecipeIngredientSerializer(ingredient).data,
        status=status.HTTP_200_OK,
    )


@extend_schema(
    request=None,
    responses={201: dict, 400: dict, 403: dict},
)
@api_view(["POST"])
@permission_classes([IsAuthenticated])
@parser_classes([MultiPartParser, FormParser])
@transaction.atomic
def import_recipes_csv(request):
    if not can_manage_recipes(request.user):
        return Response(
            {"detail": "You do not have permission to manage recipes."},
            status=status.HTTP_403_FORBIDDEN,
        )

    csv_file = request.FILES.get("file")
    if not csv_file:
        return Response(
            {"detail": "CSV file is required under form field 'file'."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        df = pd.read_csv(csv_file)
    except Exception as exc:
        return Response(
            {"detail": f"Unable to read CSV file: {str(exc)}"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    uploaded_columns = list(df.columns)
    missing_columns = [
        column
        for column in RECIPE_CSV_REQUIRED_COLUMNS
        if column not in uploaded_columns
    ]
    if missing_columns:
        return Response(
            {
                "detail": "CSV is missing required columns.",
                "missing_columns": missing_columns,
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    if df.empty:
        return Response(
            {"detail": "CSV file is empty."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    def clean_optional_text(value):
        if pd.isna(value):
            return ""
        return str(value).strip()

    def clean_optional_bool(value, default=True):
        if pd.isna(value):
            return default
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in {"true", "1", "yes"}:
            return True
        if text in {"false", "0", "no"}:
            return False
        return default

    row_errors = []
    validated_rows = []

    for index, row in df.iterrows():
        row_number = int(index) + 2  # header is row 1

        payload = {
            "recipe_name": clean_optional_text(row.get("recipe_name")),
            "recipe_description": clean_optional_text(row.get("recipe_description")),
            "recipe_is_active": clean_optional_bool(
                row.get("recipe_is_active"), default=True
            ),
            "item_no": None if pd.isna(row.get("item_no")) else int(row.get("item_no")),
            "ingredient_name": clean_optional_text(row.get("ingredient_name")),
            "group": clean_optional_text(row.get("group")).lower(),
            "q10_g": None if pd.isna(row.get("q10_g")) else float(row.get("q10_g")),
            "b": None if pd.isna(row.get("b")) else float(row.get("b")),
            "c_g": None if pd.isna(row.get("c_g")) else float(row.get("c_g")),
            "min_per_person_g": (
                None
                if pd.isna(row.get("min_per_person_g"))
                else float(row.get("min_per_person_g"))
            ),
            "max_per_person_g": (
                None
                if pd.isna(row.get("max_per_person_g"))
                else float(row.get("max_per_person_g"))
            ),
            "option_group": clean_optional_text(row.get("option_group")).lower(),
            "option_value": clean_optional_text(row.get("option_value")),
            "ingredient_is_active": clean_optional_bool(
                row.get("ingredient_is_active"),
                default=True,
            ),
        }

        serializer = RecipeCSVRowSerializer(data=payload)
        if not serializer.is_valid():
            row_errors.append(
                {
                    "row": row_number,
                    "errors": serializer.errors,
                }
            )
            continue

        validated_rows.append(
            {
                "row_number": row_number,
                "data": serializer.validated_data,
            }
        )

    if row_errors:
        return Response(
            {
                "detail": "CSV validation failed.",
                "row_errors": row_errors,
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    # =========================
    # Recipe-level conflict detection
    # =========================
    recipe_metadata_map = {}
    recipe_metadata_conflicts = []

    for entry in validated_rows:
        row_number = entry["row_number"]
        row = entry["data"]

        recipe_key = row["recipe_name"].strip().lower()

        current_meta = {
            "recipe_description": row.get("recipe_description", ""),
            "recipe_is_active": row.get("recipe_is_active", True),
        }

        if recipe_key not in recipe_metadata_map:
            recipe_metadata_map[recipe_key] = current_meta
            continue

        existing_meta = recipe_metadata_map[recipe_key]

        if existing_meta != current_meta:
            recipe_metadata_conflicts.append(
                {
                    "row": row_number,
                    "recipe_name": row["recipe_name"],
                    "detail": "Conflicting recipe-level values for same recipe.",
                }
            )

    if recipe_metadata_conflicts:
        return Response(
            {
                "detail": "CSV contains conflicting recipe-level values.",
                "row_errors": recipe_metadata_conflicts,
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    # =========================
    # Duplicate detection
    # =========================
    seen_keys = {}
    duplicate_rows = []

    for entry in validated_rows:
        row_number = entry["row_number"]
        row = entry["data"]

        key = (
            row["recipe_name"].strip().lower(),
            row["ingredient_name"].strip().lower(),
            (row.get("option_group") or "").strip().lower(),
            (row.get("option_value") or "").strip().lower(),
        )

        if key in seen_keys:
            duplicate_rows.append(
                {
                    "row": row_number,
                    "first_seen_row": seen_keys[key],
                    "detail": "Duplicate ingredient row.",
                }
            )
        else:
            seen_keys[key] = row_number

    if duplicate_rows:
        return Response(
            {
                "detail": "CSV contains duplicate ingredient rows.",
                "row_errors": duplicate_rows,
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    recipes_created = 0
    recipes_updated = 0
    ingredients_created = 0
    ingredients_updated = 0

    recipe_cache = {}

    for entry in validated_rows:
        row = entry["data"]
        recipe_name = row["recipe_name"]
        recipe_description = row.get("recipe_description", "")
        recipe_is_active = row.get("recipe_is_active", True)

        recipe_key = recipe_name.strip().lower()

        if recipe_key in recipe_cache:
            recipe = recipe_cache[recipe_key]
        else:
            recipe, created = Recipe.objects.get_or_create(
                name=recipe_name,
                defaults={
                    "description": recipe_description,
                    "is_active": recipe_is_active,
                },
            )

            if created:
                recipes_created += 1
            else:
                changed = False
                if recipe.description != recipe_description:
                    recipe.description = recipe_description
                    changed = True
                if recipe.is_active != recipe_is_active:
                    recipe.is_active = recipe_is_active
                    changed = True
                if changed:
                    recipe.save(
                        update_fields=["description", "is_active", "updated_at"]
                    )
                    recipes_updated += 1

            recipe_cache[recipe_key] = recipe

        ingredient_defaults = {
            "item_no": row.get("item_no"),
            "group": row["group"],
            "q10_g": row["q10_g"],
            "b": row["b"],
            "c_g": row["c_g"],
            "min_per_person_g": row.get("min_per_person_g"),
            "max_per_person_g": row.get("max_per_person_g"),
            "is_active": row.get("ingredient_is_active", True),
        }

        ingredient, created = RecipeIngredient.objects.update_or_create(
            recipe=recipe,
            name=row["ingredient_name"],
            option_group=row.get("option_group", ""),
            option_value=row.get("option_value", ""),
            defaults=ingredient_defaults,
        )

        if created:
            ingredients_created += 1
        else:
            ingredients_updated += 1

    return Response(
        {
            "detail": "Recipe CSV imported successfully.",
            "summary": {
                "recipes_created": recipes_created,
                "recipes_updated": recipes_updated,
                "ingredients_created": ingredients_created,
                "ingredients_updated": ingredients_updated,
                "rows_processed": len(validated_rows),
                "unique_recipes_processed": len(recipe_cache),
            },
        },
        status=status.HTTP_201_CREATED,
    )


from django.utils import timezone
from accounts.permissions import has_global_access


@api_view(["PATCH"])
@permission_classes([IsAuthenticated])
def lock_recipe_actuals(request, recipe_id: int):
    """
    PATCH /api/recipes/{id}/lock-actuals/

    Only boss / managing director
    Locks actual editing for ALL batches of this recipe
    """

    try:
        recipe = Recipe.objects.get(pk=recipe_id)
    except Recipe.DoesNotExist:
        return Response(
            {"detail": "Recipe not found."},
            status=status.HTTP_404_NOT_FOUND,
        )

    if not has_global_access(request.user):
        return Response(
            {"detail": "You do not have permission to lock this recipe."},
            status=status.HTTP_403_FORBIDDEN,
        )

    if recipe.actuals_locked:
        return Response(
            {"detail": "Recipe is already locked."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    recipe.actuals_locked = True
    recipe.actuals_locked_by = request.user
    recipe.actuals_locked_at = timezone.now()
    recipe.save(
        update_fields=["actuals_locked", "actuals_locked_by", "actuals_locked_at"]
    )

    return Response(
        {"detail": "Recipe actuals locked successfully."},
        status=status.HTTP_200_OK,
    )


@api_view(["PATCH"])
@permission_classes([IsAuthenticated])
def unlock_recipe_actuals(request, recipe_id: int):
    """
    PATCH /api/recipes/{id}/unlock-actuals/

    Only boss / managing director
    Unlocks actual editing for ALL batches of this recipe
    """

    try:
        recipe = Recipe.objects.get(pk=recipe_id)
    except Recipe.DoesNotExist:
        return Response(
            {"detail": "Recipe not found."},
            status=status.HTTP_404_NOT_FOUND,
        )

    if not has_global_access(request.user):
        return Response(
            {"detail": "You do not have permission to unlock this recipe."},
            status=status.HTTP_403_FORBIDDEN,
        )

    if not recipe.actuals_locked:
        return Response(
            {"detail": "Recipe is already unlocked."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    recipe.actuals_locked = False
    recipe.actuals_locked_by = None
    recipe.actuals_locked_at = None
    recipe.save(
        update_fields=["actuals_locked", "actuals_locked_by", "actuals_locked_at"]
    )

    return Response(
        {"detail": "Recipe actuals unlocked successfully."},
        status=status.HTTP_200_OK,
    )


@extend_schema(
    responses=RecipeIngredientSerializer(many=True),
)
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def list_all_recipe_ingredients(request):
    """
    GET /api/recipe-ingredients/

    Lists all recipe ingredients across all recipes.
    Used by Ingredient Category Management screen.
    """

    ingredients = (
        RecipeIngredient.objects.select_related("recipe", "category")
        .filter(is_active=True, recipe__is_active=True)
        .order_by("recipe__name", "item_no", "id")
    )

    return Response(
        RecipeIngredientSerializer(ingredients, many=True).data,
        status=status.HTTP_200_OK,
    )
