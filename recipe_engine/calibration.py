from __future__ import annotations

from typing import Optional, Set

import numpy as np
import pandas as pd


def fit_scale_factors(
    logs: pd.DataFrame,
    tau_days: float = 14.0,
    ingredient_col: str = "ingredient",
    day_col: str = "day",
    actual_col: str = "actual_g",
    q10_col: str = "q10_g",
    n_people_col: str = "n_people",
    b_col: str = "b",
) -> pd.DataFrame:
    """
    Fit per-ingredient scale factors s_i using exponential decay weights:

        weight = exp(-(age_days)/tau_days)

    We fit scale on the "scalable part" only:

        pred_scalable = q10 * (N/10)^b
        ratio = actual / pred_scalable
        s_i = weighted_mean(ratio)

    Expected logs columns:
      - ingredient, day, n_people, actual_g, q10_g, b

    Returns:
      DataFrame: [ingredient, s]
    """
    required = {ingredient_col, day_col, n_people_col, actual_col, q10_col, b_col}
    missing = [c for c in required if c not in logs.columns]
    if missing:
        raise KeyError(f"logs missing required columns: {missing}")

    if tau_days <= 0:
        raise ValueError("tau_days must be > 0")

    df = logs.copy()

    # Event-based calibration:
    # all selected recent batches get equal weight.
    df["w"] = 1.0

    # Scalable part (exclude floors)
    df["pred_scalable"] = df[q10_col].astype(float) * (
        df[n_people_col].astype(float) / 10.0
    ) ** df[b_col].astype(float)

    df = df[df["pred_scalable"] > 0].copy()
    df["ratio"] = df[actual_col].astype(float) / df["pred_scalable"].astype(float)

    scales = df.groupby(ingredient_col)["ratio"].mean().reset_index(name="s")
    scales["s"] = scales["s"].astype(float)
    return scales


def apply_scales_to_logs(
    logs: pd.DataFrame,
    scales: pd.DataFrame,
    protein_set: Optional[Set[str]] = None,
    ingredient_col: str = "ingredient",
    protein_type_col: str = "protein_type",
    actual_col: str = "actual_g",
    pred_col: str = "pred_g",
    q10_col: str = "q10_g",
    n_people_col: str = "n_people",
    b_col: str = "b",
    c_col: str = "c_g",
) -> pd.DataFrame:
    """
    Utility for evaluation:
    Adds pred_g_new to logs by applying scales:

        pred_g_new = s * (q10*(N/10)^b) + c

    If protein_set provided, applies per-row protein selection:
      - keep only chosen protein; others set to 0

    Returns copy of logs with:
      - s, pred_scalable, pred_g_new
    """
    if "s" not in scales.columns:
        raise KeyError("scales must include column 's'")
    if ingredient_col not in scales.columns:
        raise KeyError(f"scales must include column '{ingredient_col}'")

    required = {ingredient_col, q10_col, n_people_col, b_col, c_col, protein_type_col}
    missing = [c for c in required if c not in logs.columns]
    if missing:
        raise KeyError(f"logs missing required columns: {missing}")

    df = logs.copy().merge(scales[[ingredient_col, "s"]], on=ingredient_col, how="left")
    df["s"] = df["s"].fillna(1.0).astype(float)

    df["pred_scalable"] = df[q10_col].astype(float) * (
        df[n_people_col].astype(float) / 10.0
    ) ** df[b_col].astype(float)
    df["pred_g_new"] = df["s"] * df["pred_scalable"] + df[c_col].astype(float)

    # Protein selection per row (optional)
    if protein_set:
        normalized_proteins = {str(p).upper().strip() for p in protein_set}
        ing_upper = df[ingredient_col].astype(str).str.upper().str.strip()
        chosen = df[protein_type_col].astype(str).str.upper().str.strip()
        is_protein = ing_upper.isin(normalized_proteins)
        df.loc[is_protein & (ing_upper != chosen), "pred_g_new"] = 0.0

    return df
