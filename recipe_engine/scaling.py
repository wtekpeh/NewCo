from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional, Set

import pandas as pd


@dataclass(frozen=True)
class RecipeSchema:
    """
    Expected columns in recipe DataFrame.

    Required columns:
      - ingredient (str)
      - q10_g (float)  baseline grams for 10 people
      - b (float)      scaling exponent
      - c_g (float)    floor in grams
    Optional columns (kept if present):
      - group (str)
    """
    ingredient: str = "ingredient"
    q10_g: str = "q10_g"
    b: str = "b"
    c_g: str = "c_g"
    group: str = "group"


def _normalize_upper(s: str) -> str:
    return (s or "").upper().strip()


def validate_recipe_df(df_recipe: pd.DataFrame, schema: RecipeSchema = RecipeSchema()) -> None:
    """
    Validate that df_recipe contains required columns for prediction.
    """
    required = {schema.ingredient, schema.q10_g, schema.b, schema.c_g}
    missing = [c for c in required if c not in df_recipe.columns]
    if missing:
        raise KeyError(f"Recipe DataFrame missing required columns: {missing}")

    # Basic type coercion checks (won't modify in-place)
    _ = pd.to_numeric(df_recipe[schema.q10_g], errors="raise")
    _ = pd.to_numeric(df_recipe[schema.b], errors="raise")
    _ = pd.to_numeric(df_recipe[schema.c_g], errors="raise")


def predict_ingredients(
    df_recipe: pd.DataFrame,
    n_people: float,
    protein_type: Optional[str] = None,
    protein_set: Optional[Set[str]] = None,
    schema: RecipeSchema = RecipeSchema(),
) -> pd.DataFrame:
    """
    Predict ingredient quantities (grams) for n_people using:

        q_i(N) = q_i,10 * (N/10)^b_i + c_i

    Protein selection:
      - If protein_type and protein_set are provided:
          include only the selected protein; set others to 0.

    Returns a new DataFrame with:
      - pred_g, pred_kg
      - and original recipe columns (safe copy)
    """
    validate_recipe_df(df_recipe, schema=schema)

    if n_people <= 0:
        raise ValueError("n_people must be > 0")

    out = df_recipe.copy()

    # Core scaling
    out["pred_g"] = (
        out[schema.q10_g].astype(float) * (float(n_people) / 10.0) ** out[schema.b].astype(float)
        + out[schema.c_g].astype(float)
    )

    # Protein selection (optional)
    if protein_type is not None and protein_set:
        chosen = _normalize_upper(protein_type)
        normalized_proteins = {_normalize_upper(p) for p in protein_set}
        if chosen not in normalized_proteins:
            raise ValueError(f"protein_type must be one of {sorted(normalized_proteins)}")

        ing_upper = out[schema.ingredient].astype(str).map(_normalize_upper)
        is_protein = ing_upper.isin(normalized_proteins)
        out.loc[is_protein & (ing_upper != chosen), "pred_g"] = 0.0

    out["pred_g"] = out["pred_g"].astype(float)
    out["pred_kg"] = out["pred_g"] / 1000.0
    return out


def predict_with_scales(
    df_recipe: pd.DataFrame,
    n_people: float,
    df_scales: pd.DataFrame,
    protein_type: Optional[str] = None,
    protein_set: Optional[Set[str]] = None,
    schema: RecipeSchema = RecipeSchema(),
    scale_col: str = "s",
) -> pd.DataFrame:
    """
    Predict using learned per-ingredient scale factors s_i:

        q_i_new(N) = s_i * [q_i,10 * (N/10)^b_i] + c_i

    Notes:
      - Scales are applied to the "scalable part" only (excluding floor c).
      - Any ingredient missing a scale gets s=1.0.

    df_scales expected columns:
      - ingredient
      - s  (or scale_col)

    Returns DataFrame with:
      - s, pred_g_new, pred_kg_new
    """
    validate_recipe_df(df_recipe, schema=schema)

    if scale_col not in df_scales.columns:
        raise KeyError(f"df_scales must include '{scale_col}' column.")

    if schema.ingredient not in df_scales.columns:
        raise KeyError(f"df_scales must include '{schema.ingredient}' column.")

    if n_people <= 0:
        raise ValueError("n_people must be > 0")

    out = df_recipe.copy()

    # Merge scale factors
    tmp = df_scales[[schema.ingredient, scale_col]].copy()
    tmp[schema.ingredient] = tmp[schema.ingredient].astype(str)
    out[schema.ingredient] = out[schema.ingredient].astype(str)

    out = out.merge(tmp, on=schema.ingredient, how="left")
    out[scale_col] = out[scale_col].fillna(1.0).astype(float)

    scalable = out[schema.q10_g].astype(float) * (float(n_people) / 10.0) ** out[schema.b].astype(float)
    out["pred_g_new"] = out[scale_col] * scalable + out[schema.c_g].astype(float)

    # Protein selection (optional)
    if protein_type is not None and protein_set:
        chosen = _normalize_upper(protein_type)
        normalized_proteins = {_normalize_upper(p) for p in protein_set}
        if chosen not in normalized_proteins:
            raise ValueError(f"protein_type must be one of {sorted(normalized_proteins)}")

        ing_upper = out[schema.ingredient].astype(str).map(_normalize_upper)
        is_protein = ing_upper.isin(normalized_proteins)
        out.loc[is_protein & (ing_upper != chosen), "pred_g_new"] = 0.0

    out["pred_g_new"] = out["pred_g_new"].astype(float)
    out["pred_kg_new"] = out["pred_g_new"] / 1000.0
    return out



def sum_prediction_frames(
    pred_frames: Iterable[pd.DataFrame],
    schema: RecipeSchema = RecipeSchema(),
) -> pd.DataFrame:
    """
    Sum multiple prediction outputs (e.g., per-protein slice predictions)
    into one DataFrame by ingredient (+ group if present).

    Each frame should contain:
      - schema.ingredient (default: "ingredient")
      - pred_g
      - optionally schema.group (default: "group")

    Output contains:
      - ingredient
      - group (if present in any frame)
      - pred_g (summed)
      - pred_kg (recomputed)
    """
    frames = [df.copy() for df in pred_frames if df is not None]
    if not frames:
        raise ValueError("pred_frames is empty")

    # Determine whether to keep group
    keep_group = any(schema.group in f.columns for f in frames)

    cols = [schema.ingredient, "pred_g"]
    if keep_group:
        cols.insert(1, schema.group)

    # Normalize minimal columns + types
    norm = []
    for f in frames:
        missing = [c for c in cols if c not in f.columns]
        if missing:
            raise KeyError(f"Prediction frame missing required columns: {missing}")

        tmp = f[cols].copy()
        tmp[schema.ingredient] = tmp[schema.ingredient].astype(str)
        if keep_group:
            tmp[schema.group] = tmp[schema.group].astype(str)
        tmp["pred_g"] = pd.to_numeric(tmp["pred_g"], errors="raise").astype(float)
        norm.append(tmp)

    all_df = pd.concat(norm, ignore_index=True)

    group_cols = [schema.ingredient] + ([schema.group] if keep_group else [])
    out = all_df.groupby(group_cols, as_index=False)["pred_g"].sum()
    out["pred_kg"] = out["pred_g"] / 1000.0
    return out
