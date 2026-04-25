from __future__ import annotations

from typing import Optional

import pandas as pd
from django.db import transaction
from django.utils import timezone

from cooking.models import IngredientScale
from recipe_engine.services.calibration_logs import build_recent_calibration_logs_df


def save_scales(
    scales_df: pd.DataFrame,
    tau_days: float = 14.0,
    branch_id: Optional[int] = None,
    recipe_id: Optional[int] = None,
    sample_count: Optional[int] = None,
) -> int:
    """
    Save learned scales to the database for a given scope.

    Scope:
      - global: branch_id=None, recipe_id=None
      - branch-specific: branch_id set
      - recipe-specific: recipe_id set
      - branch+recipe-specific: both set

    Existing scales for the same scope are replaced.

    Returns:
      number of rows saved
    """
    if scales_df is None or scales_df.empty:
        return 0

    required = {"ingredient", "s"}
    missing = [col for col in required if col not in scales_df.columns]
    if missing:
        raise KeyError(f"scales_df missing required columns: {missing}")

    df = scales_df.copy()
    df["ingredient"] = df["ingredient"].astype(str).str.strip()
    df["s"] = pd.to_numeric(df["s"], errors="coerce")
    df = df.dropna(subset=["ingredient", "s"])

    if df.empty:
        return 0

    if sample_count is None:
        logs_df = build_calibration_logs_df(branch_id=branch_id, recipe_id=recipe_id)
        sample_count = int(len(logs_df))

    now = timezone.now()

    with transaction.atomic():
        IngredientScale.objects.filter(
            branch_id=branch_id,
            recipe_id=recipe_id,
        ).delete()

        objs = [
            IngredientScale(
                ingredient=row["ingredient"],
                branch_id=branch_id,
                recipe_id=recipe_id,
                s=float(row["s"]),
                tau_days=float(tau_days),
                sample_count=int(sample_count),
                computed_at=now,
            )
            for _, row in df.iterrows()
        ]

        IngredientScale.objects.bulk_create(objs)

    return len(objs)


def load_scales_df(
    branch_id: Optional[int] = None,
    recipe_id: Optional[int] = None,
) -> pd.DataFrame:
    """
    Load saved scales from the database for a given scope.

    Returns DataFrame with:
      - ingredient
      - s
      - tau_days
      - sample_count
      - computed_at
    """
    qs = IngredientScale.objects.filter(
        branch_id=branch_id,
        recipe_id=recipe_id,
    ).order_by("ingredient")

    df = pd.DataFrame(
        list(
            qs.values(
                "ingredient",
                "s",
                "tau_days",
                "sample_count",
                "computed_at",
            )
        )
    )

    if df.empty:
        return pd.DataFrame(
            columns=["ingredient", "s", "tau_days", "sample_count", "computed_at"]
        )

    df["ingredient"] = df["ingredient"].astype(str)
    df["s"] = pd.to_numeric(df["s"], errors="coerce")
    df["tau_days"] = pd.to_numeric(df["tau_days"], errors="coerce")
    df["sample_count"] = pd.to_numeric(df["sample_count"], errors="coerce")
    df["computed_at"] = pd.to_datetime(df["computed_at"], errors="coerce")

    return df.reset_index(drop=True)


def load_best_scales_df(
    branch_id: Optional[int] = None,
    recipe_id: Optional[int] = None,
) -> pd.DataFrame:
    """
    Load best available scales using fallback hierarchy:
      1. branch + recipe
      2. recipe only
      3. global
    """

    if branch_id is not None and recipe_id is not None:
        df = load_scales_df(branch_id=branch_id, recipe_id=recipe_id)
        if not df.empty:
            return df

    if recipe_id is not None:
        df = load_scales_df(branch_id=None, recipe_id=recipe_id)
        if not df.empty:
            return df

    return load_scales_df(branch_id=None, recipe_id=None)


def recalibrate_and_store(
    tau_days: float = 14.0,
    branch_id: Optional[int] = None,
    recipe_id: Optional[int] = None,
    window_batches: int = 30,
    min_batches: int = 20,
) -> pd.DataFrame:
    """
    Convenience helper:
      1. build logs
      2. run calibration
      3. save scales
      4. return saved-scale-shaped DataFrame

    This is useful for management commands or admin-only API endpoints later.
    """
    from recipe_engine.services.calibration_service import run_calibration

    logs_df = build_recent_calibration_logs_df(
        branch_id=branch_id,
        recipe_id=recipe_id,
        window_batches=window_batches,
    )

    if logs_df.empty:
        return pd.DataFrame(
            columns=["ingredient", "s", "tau_days", "sample_count", "computed_at"]
        )

    unique_batches = logs_df["batch_id"].nunique()

    if unique_batches < min_batches:
        return pd.DataFrame(
            columns=["ingredient", "s", "tau_days", "sample_count", "computed_at"]
        )

    scales_df = run_calibration(
        tau_days=tau_days,
        branch_id=branch_id,
        recipe_id=recipe_id,
        window_batches=window_batches,
        min_batches=min_batches,
    )

    if scales_df.empty:
        return pd.DataFrame(
            columns=["ingredient", "s", "tau_days", "sample_count", "computed_at"]
        )

    save_scales(
        scales_df=scales_df,
        tau_days=tau_days,
        branch_id=branch_id,
        recipe_id=recipe_id,
        sample_count=int(len(logs_df)),
    )

    return load_scales_df(branch_id=branch_id, recipe_id=recipe_id)
