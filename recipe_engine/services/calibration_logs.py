from __future__ import annotations

from typing import Optional

import pandas as pd
from django.db.models import QuerySet

from cooking.models import CookBatchItem


def build_calibration_logs_qs() -> QuerySet[CookBatchItem]:
    """
    Base queryset for calibration logs.

    Keeps the query modular so other functions can reuse it later
    (for filtering by branch, recipe, date range, etc.).
    """
    return (
        CookBatchItem.objects.select_related("batch")
        .filter(actual_g__isnull=False)
        .exclude(actual_g__lte=0)
        .order_by("batch__created_at", "id")
    )


def build_calibration_logs_df(
    branch_id: Optional[int] = None,
    recipe_id: Optional[int] = None,
) -> pd.DataFrame:
    """
    Build a calibration-ready DataFrame from CookBatch + CookBatchItem.

    Current output includes the minimum fields needed by fit_scale_factors(...)
    plus extra metadata for future filtering, debugging, and explainability.

    Required by fit_scale_factors:
      - ingredient
      - day
      - actual_g
      - q10_g
      - n_people
      - b

    Extra fields included now for future use:
      - batch_id
      - recipe_id
      - branch_id
      - protein_type
      - created_at
      - pred_g
      - pred_kg
      - final_g
      - final_kg
      - actual_kg
      - c_g
      - group
      - notes
    """
    qs = build_calibration_logs_qs()

    if branch_id is not None:
        qs = qs.filter(batch__branch_id=branch_id)

    if recipe_id is not None:
        qs = qs.filter(batch__recipe_id=recipe_id)

    rows = qs.values(
        "id",
        "ingredient",
        "group",
        "q10_g",
        "b",
        "c_g",
        "pred_g",
        "pred_kg",
        "final_g",
        "final_kg",
        "actual_g",
        "actual_kg",
        "notes",
        "batch_id",
        "batch__recipe_id",
        "batch__branch_id",
        "batch__n_people",
        "batch__protein_type",
        "batch__created_at",
    )

    df = pd.DataFrame(list(rows))

    if df.empty:
        return pd.DataFrame(
            columns=[
                "item_id",
                "batch_id",
                "recipe_id",
                "branch_id",
                "ingredient",
                "group",
                "q10_g",
                "b",
                "c_g",
                "pred_g",
                "pred_kg",
                "final_g",
                "final_kg",
                "actual_g",
                "actual_kg",
                "n_people",
                "protein_type",
                "created_at",
                "day",
                "notes",
            ]
        )

    df = df.rename(
        columns={
            "id": "item_id",
            "batch__recipe_id": "recipe_id",
            "batch__branch_id": "branch_id",
            "batch__n_people": "n_people",
            "batch__protein_type": "protein_type",
            "batch__created_at": "created_at",
        }
    )

    # Ensure consistent types
    df["created_at"] = pd.to_datetime(df["created_at"], errors="coerce")
    df["day"] = (
        df["created_at"] - pd.Timestamp("1970-01-01", tz="UTC")
    ).dt.total_seconds() / 86400.0

    numeric_cols = [
        "q10_g",
        "b",
        "c_g",
        "pred_g",
        "pred_kg",
        "final_g",
        "final_kg",
        "actual_g",
        "actual_kg",
        "n_people",
        "day",
    ]
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    # Final safety filter for calibration usage
    df = df[df["actual_g"].notna() & (df["actual_g"] > 0)].copy()

    return df[
        [
            "item_id",
            "batch_id",
            "recipe_id",
            "branch_id",
            "ingredient",
            "group",
            "q10_g",
            "b",
            "c_g",
            "pred_g",
            "pred_kg",
            "final_g",
            "final_kg",
            "actual_g",
            "actual_kg",
            "n_people",
            "protein_type",
            "created_at",
            "day",
            "notes",
        ]
    ]
