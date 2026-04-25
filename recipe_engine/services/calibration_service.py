from __future__ import annotations

from typing import Optional

import pandas as pd

from recipe_engine.calibration import fit_scale_factors
from recipe_engine.services.calibration_logs import (
    build_calibration_logs_df,
    build_recent_calibration_logs_df,
)


def run_calibration(
    tau_days: float = 14.0,
    branch_id: Optional[int] = None,
    recipe_id: Optional[int] = None,
    window_batches: int = 30,
    min_batches: int = 20,
) -> pd.DataFrame:
    """
    Run calibration from historical CookBatch / CookBatchItem logs.

    Returns a DataFrame with:
      - ingredient
      - s

    Optional filters allow future branch-specific or recipe-specific learning.
    """
    logs_df = build_recent_calibration_logs_df(
        branch_id=branch_id,
        recipe_id=recipe_id,
        window_batches=window_batches,
    )

    if logs_df.empty:
        return pd.DataFrame(columns=["ingredient", "s"])

    # 🔥 SAFEGUARD: ensure enough unique batches

    unique_batches = logs_df["batch_id"].nunique()

    if unique_batches < min_batches:
        # Not enough data to trust recalibration
        return pd.DataFrame(columns=["ingredient", "s"])

    scales_df = fit_scale_factors(
        logs=logs_df,
        tau_days=tau_days,
        ingredient_col="ingredient",
        day_col="day",
        actual_col="actual_g",
        q10_col="q10_g",
        n_people_col="n_people",
        b_col="b",
    )

    if scales_df.empty:
        return pd.DataFrame(columns=["ingredient", "s"])

    scales_df["ingredient"] = scales_df["ingredient"].astype(str)
    scales_df["s"] = pd.to_numeric(scales_df["s"], errors="coerce")

    scales_df = (
        scales_df.dropna(subset=["ingredient", "s"])
        .sort_values("ingredient")
        .reset_index(drop=True)
    )

    return scales_df


def run_calibration_with_logs(
    tau_days: float = 14.0,
    branch_id: Optional[int] = None,
    recipe_id: Optional[int] = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Convenience helper for debugging / admin inspection.

    Returns:
      logs_df, scales_df
    """
    logs_df = build_calibration_logs_df(
        branch_id=branch_id,
        recipe_id=recipe_id,
    )

    if logs_df.empty:
        return logs_df, pd.DataFrame(columns=["ingredient", "s"])

    scales_df = fit_scale_factors(
        logs=logs_df,
        tau_days=tau_days,
        ingredient_col="ingredient",
        day_col="day",
        actual_col="actual_g",
        q10_col="q10_g",
        n_people_col="n_people",
        b_col="b",
    )

    if scales_df.empty:
        return logs_df, pd.DataFrame(columns=["ingredient", "s"])

    scales_df["ingredient"] = scales_df["ingredient"].astype(str)
    scales_df["s"] = pd.to_numeric(scales_df["s"], errors="coerce")

    scales_df = (
        scales_df.dropna(subset=["ingredient", "s"])
        .sort_values("ingredient")
        .reset_index(drop=True)
    )

    return logs_df, scales_df
