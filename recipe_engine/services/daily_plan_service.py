from __future__ import annotations

from typing import Any, Dict, List, Optional, Set

import pandas as pd

from django.db import transaction
from django.utils import timezone

from accounts.models import Branch
from cooking.models import (
    CookBatch,
    CookBatchItem,
    DailyConsumptionPlan,
    DailyConsumptionPlanRecipe,
    DailyConsumptionPlanIngredientSummary,
    DailyPlanIngredientScale,
)
from recipes.models import Recipe, RecipeIngredient
from recipe_engine.scaling import (
    predict_ingredients,
    predict_with_scales,
    sum_prediction_frames,
)
from recipe_engine.services.scale_store import load_best_scales_df

DEFAULT_SHARED_FACTORS = {
    "oil": 0.90,
    "salt": 0.95,
    "seasoning": 0.95,
    "spice": 0.95,
    "spices": 0.95,
    "water": 0.90,
}


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _normalize_upper(value: Any) -> str:
    return _normalize_text(value).upper()


def _is_shared_adjustable(ingredient: str, group: str = "") -> bool:
    """
    Initial rule-based shared ingredient detection.

    Later this can be replaced or enhanced with a DB field such as:
      shared_adjustable = true
    on RecipeIngredient or IngredientCategory.
    """
    name = ingredient.lower().strip()
    group_value = group.lower().strip()

    if group_value in {"seasoning", "aromatic"}:
        return True

    return any(key in name for key in DEFAULT_SHARED_FACTORS.keys())


def _default_factor_for_ingredient(ingredient: str, group: str = "") -> float:
    name = ingredient.lower().strip()

    for key, factor in DEFAULT_SHARED_FACTORS.items():
        if key in name:
            return float(factor)

    if group.lower().strip() in {"seasoning", "aromatic"}:
        return 0.95

    return 1.0


def _unit_display_for_ingredient(ingredient: str) -> str:
    """
    Keeps display consistent with the current reporting convention:
      - oil => ml
      - kenkey => pc
      - otherwise => g
    """
    name = ingredient.lower().strip()

    if "kenkey" in name:
        return "pc"

    if "oil" in name:
        return "ml"

    return "g"


def _load_daily_factor(
    ingredient: str,
    branch_id: Optional[int],
    group: str = "",
) -> tuple[float, str, bool]:
    """
    Returns:
      factor, reason, is_shared_adjusted

    Priority:
      1. branch-specific learned factor
      2. global learned factor
      3. default rule-based factor
      4. 1.0 for non-shared ingredients
    """
    is_shared = _is_shared_adjustable(ingredient, group)

    if not is_shared:
        return 1.0, "No daily shared adjustment applied.", False

    qs = DailyPlanIngredientScale.objects.filter(ingredient=ingredient)

    scale = None

    if branch_id is not None:
        scale = qs.filter(branch_id=branch_id).first()

    if scale is None:
        scale = qs.filter(branch_id__isnull=True).first()

    if scale is not None and scale.sample_count > 0:
        factor = max(
            float(scale.min_factor),
            min(float(scale.learned_factor), float(scale.max_factor)),
        )
        return (
            factor,
            f"Applied learned daily shared factor from {scale.sample_count} completed daily plan sample(s).",
            True,
        )

    factor = _default_factor_for_ingredient(ingredient, group)

    if factor != 1.0:
        return (
            factor,
            "Applied initial rule-based shared ingredient adjustment. Learned factor will improve after completed daily plans are recorded.",
            True,
        )

    return 1.0, "No daily shared adjustment applied.", False


def _build_recipe_dataframe(recipe: Recipe) -> pd.DataFrame:
    ingredients_qs = RecipeIngredient.objects.filter(
        recipe=recipe,
        is_active=True,
    ).order_by("item_no", "id")

    if not ingredients_qs.exists():
        raise ValueError(f"Recipe '{recipe.name}' has no active ingredients.")

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

    return df


def _get_protein_set(df: pd.DataFrame) -> Set[str]:
    protein_rows = df[
        (df["group"] == "protein")
        | (df["option_group"].fillna("").str.lower() == "protein")
    ]
    return set(protein_rows["ingredient"].dropna().astype(str).tolist())


def _validate_and_normalize_protein_options(
    *,
    df: pd.DataFrame,
    n_people: int,
    options: Dict[str, Any],
    recipe_name: str,
) -> tuple[str, List[Dict[str, Any]], Set[str]]:
    protein_set = _get_protein_set(df)

    protein_type = _normalize_text((options or {}).get("protein"))
    proteins_spec = (options or {}).get("proteins", None)
    multi_proteins: List[Dict[str, Any]] = []

    if isinstance(proteins_spec, list):
        for p in proteins_spec:
            if not isinstance(p, dict):
                continue

            name = _normalize_text(p.get("protein"))
            cnt = p.get("n_people", None)

            if name:
                multi_proteins.append({"protein": name, "n_people": cnt})

    if not protein_set:
        return protein_type, multi_proteins, protein_set

    normalized_set = {_normalize_upper(x) for x in protein_set}

    if multi_proteins:
        seen = set()
        total = 0

        for p in multi_proteins:
            key = _normalize_upper(p["protein"])

            if key in seen:
                raise ValueError(
                    f"Duplicate protein '{p['protein']}' found for recipe '{recipe_name}'."
                )
            seen.add(key)

            if key not in normalized_set:
                raise ValueError(
                    f"Invalid protein '{p['protein']}' for recipe '{recipe_name}'. "
                    f"Choices are: {sorted(protein_set)}"
                )

            try:
                protein_people = int(p["n_people"])
            except Exception:
                raise ValueError(
                    f"Invalid n_people for protein '{p['protein']}' in recipe '{recipe_name}'."
                )

            if protein_people <= 0:
                raise ValueError(
                    f"n_people must be greater than 0 for protein '{p['protein']}' in recipe '{recipe_name}'."
                )

            p["n_people"] = protein_people
            total += protein_people

        if total != n_people:
            raise ValueError(
                f"Protein split for recipe '{recipe_name}' must total {n_people}. Got {total}."
            )

        protein_type = " + ".join([p["protein"] for p in multi_proteins])
        options["protein"] = protein_type

        return protein_type, multi_proteins, protein_set

    if not protein_type:
        raise ValueError(
            f"Protein option is required for recipe '{recipe_name}'. "
            f"Choices are: {sorted(protein_set)}"
        )

    if _normalize_upper(protein_type) not in normalized_set:
        raise ValueError(
            f"Invalid protein '{protein_type}' for recipe '{recipe_name}'. "
            f"Choices are: {sorted(protein_set)}"
        )

    return protein_type, multi_proteins, protein_set


def _predict_recipe(
    *,
    recipe: Recipe,
    branch_id: int,
    n_people: int,
    options: Dict[str, Any],
) -> tuple[pd.DataFrame, str]:
    df = _build_recipe_dataframe(recipe)

    protein_type, multi_proteins, protein_set = _validate_and_normalize_protein_options(
        df=df,
        n_people=n_people,
        options=options,
        recipe_name=recipe.name,
    )

    if protein_set and multi_proteins:
        pred_frames = []

        scales_df = load_best_scales_df(
            branch_id=branch_id,
            recipe_id=recipe.id,
        )
        use_scales = scales_df is not None and not scales_df.empty

        for p in multi_proteins:
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

        pred_sum = sum_prediction_frames(pred_frames)

        merge_keys = ["ingredient"]
        if "group" in df.columns and "group" in pred_sum.columns:
            merge_keys = ["ingredient", "group"]

        pred = df.merge(pred_sum, on=merge_keys, how="left")
        pred["pred_g"] = pred["pred_g"].fillna(0.0)
        pred["pred_kg"] = pred["pred_kg"].fillna(0.0)

    else:
        scales_df = load_best_scales_df(
            branch_id=branch_id,
            recipe_id=recipe.id,
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
            pred["pred_g"] = pred["pred_g_new"]
            pred["pred_kg"] = pred["pred_kg_new"]
        else:
            pred = predict_ingredients(
                df_recipe=df,
                n_people=n_people,
                protein_type=protein_type if protein_set else None,
                protein_set=protein_set if protein_set else None,
            )

    pred["final_g"] = pred["pred_g"].astype(float)
    pred["was_clamped"] = False

    for idx, row in pred.iterrows():
        min_pp = row.get("min_per_person_g")
        max_pp = row.get("max_per_person_g")

        is_protein_row = (
            str(row.get("group", "")).lower() == "protein"
            or str(row.get("option_group", "")).lower() == "protein"
        )

        if is_protein_row and protein_set and not multi_proteins:
            chosen = _normalize_upper(protein_type)
            current = _normalize_upper(row.get("ingredient"))

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

    return pred, protein_type


def _create_batch_items_from_prediction(
    *,
    batch: CookBatch,
    pred: pd.DataFrame,
) -> None:
    item_objs = []

    for _, r in pred.iterrows():
        item_objs.append(
            CookBatchItem(
                batch=batch,
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

    CookBatchItem.objects.bulk_create(item_objs)


def _build_and_save_ingredient_summaries(plan: DailyConsumptionPlan) -> None:
    items = CookBatchItem.objects.filter(
        batch__daily_plan_recipe__plan=plan,
    ).values(
        "ingredient",
        "group",
        "final_g",
    )

    df = pd.DataFrame(list(items))

    if df.empty:
        return

    df["ingredient"] = df["ingredient"].astype(str)
    df["group"] = df["group"].fillna("").astype(str)
    df["final_g"] = pd.to_numeric(df["final_g"], errors="coerce").fillna(0.0)

    grouped = df.groupby(["ingredient", "group"], as_index=False).agg(
        raw_total_g=("final_g", "sum")
    )

    summary_objs = []

    for _, row in grouped.iterrows():
        ingredient = str(row["ingredient"])
        group = str(row.get("group") or "")
        raw_total_g = float(row["raw_total_g"])

        factor, reason, is_shared_adjusted = _load_daily_factor(
            ingredient=ingredient,
            branch_id=plan.branch_id,
            group=group,
        )

        adjusted_total_g = raw_total_g * float(factor)
        adjustment_g = adjusted_total_g - raw_total_g

        summary_objs.append(
            DailyConsumptionPlanIngredientSummary(
                plan=plan,
                ingredient=ingredient,
                group=group,
                raw_total_g=raw_total_g,
                raw_total_kg=raw_total_g / 1000.0,
                adjusted_total_g=adjusted_total_g,
                adjusted_total_kg=adjusted_total_g / 1000.0,
                daily_factor=float(factor),
                adjustment_g=adjustment_g,
                unit_display=_unit_display_for_ingredient(ingredient),
                adjustment_reason=reason,
                is_shared_adjusted=bool(is_shared_adjusted),
            )
        )

    DailyConsumptionPlanIngredientSummary.objects.bulk_create(summary_objs)


@transaction.atomic
def create_daily_consumption_plan(
    *,
    user,
    branch_id: int,
    used_date,
    recipes_payload: List[Dict[str, Any]],
    notes: str = "",
) -> DailyConsumptionPlan:
    """
    Create a DailyConsumptionPlan and its child CookBatches atomically.

    This does not replace CookBatch.
    It creates normal CookBatches underneath the daily plan.
    """

    if not recipes_payload:
        raise ValueError("At least one recipe is required.")

    if len(recipes_payload) < 2:
        raise ValueError(
            "Daily Consumption Plan requires at least two recipes. Use Single Consumption for one recipe."
        )

    branch = Branch.objects.get(pk=branch_id, is_active=True)

    plan = DailyConsumptionPlan.objects.create(
        branch=branch,
        created_by=user,
        used_date=used_date,
        status="draft",
        notes=notes or "",
        created_at=timezone.now(),
    )

    for recipe_payload in recipes_payload:
        recipe_id = int(recipe_payload["recipe_id"])
        n_people = int(recipe_payload["n_people"])
        options = dict(recipe_payload.get("options") or {})
        recipe_notes = recipe_payload.get("notes", "") or ""

        recipe = Recipe.objects.get(pk=recipe_id, is_active=True)

        pred, protein_type = _predict_recipe(
            recipe=recipe,
            branch_id=branch.id,
            n_people=n_people,
            options=options,
        )

        batch = CookBatch.objects.create(
            recipe=recipe,
            branch=branch,
            created_by=user,
            n_people=n_people,
            used_date=used_date,
            options=options,
            protein_type=protein_type,
            status="draft",
            notes=recipe_notes,
            created_at=timezone.now(),
        )

        _create_batch_items_from_prediction(batch=batch, pred=pred)

        DailyConsumptionPlanRecipe.objects.create(
            plan=plan,
            cook_batch=batch,
            recipe=recipe,
            n_people=n_people,
            options=options,
            protein_type=protein_type,
        )

    _build_and_save_ingredient_summaries(plan)

    return plan
