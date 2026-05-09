from django.db import transaction
from django.utils import timezone

from cooking.models import (
    DailyConsumptionPlan,
    DailyPlanIngredientScale,
    DailyPlanIngredientScaleSample,
)

WINDOW_SIZE = 30
MIN_SAMPLES_FOR_LOCK = 20


def recompute_scale_from_samples(scale):
    """
    Recompute a scale entirely from sample history.
    """

    latest_samples = DailyPlanIngredientScaleSample.objects.filter(
        ingredient=scale.ingredient,
        branch=scale.branch,
    ).order_by("-created_at")[:WINDOW_SIZE]

    sample_values = [float(sample.observed_factor) for sample in latest_samples]

    if not sample_values:
        return False

    averaged_factor = sum(sample_values) / len(sample_values)

    averaged_factor = max(
        float(scale.min_factor),
        min(float(scale.max_factor), averaged_factor),
    )

    scale.learned_factor = averaged_factor
    scale.sample_count = len(sample_values)
    scale.computed_at = timezone.now()

    scale.save(
        update_fields=[
            "learned_factor",
            "sample_count",
            "computed_at",
        ]
    )

    return True


def update_daily_plan_scales(plan):
    """
    Update learned daily-plan shared ingredient factors.

    Learning logic:
    - Every finalized plan creates a raw sample entry.
    - Only latest WINDOW_SIZE samples are used.
    - Scale values are ALWAYS recomputed from sample history.
    """

    updated_count = 0

    summaries = plan.ingredient_summaries.filter(
        is_shared_adjusted=True,
        raw_total_g__gt=0,
        actual_total_g__isnull=False,
    )

    with transaction.atomic():
        for summary in summaries:
            observed_factor = float(summary.actual_total_g) / float(summary.raw_total_g)

            (
                scale,
                _,
            ) = DailyPlanIngredientScale.objects.select_for_update().get_or_create(
                ingredient=summary.ingredient,
                branch=plan.branch,
                defaults={
                    "learned_factor": observed_factor,
                    "sample_count": 0,
                },
            )

            sample_exists = DailyPlanIngredientScaleSample.objects.filter(
                ingredient=summary.ingredient,
                branch=plan.branch,
                plan=plan,
            ).exists()

            if not sample_exists:
                DailyPlanIngredientScaleSample.objects.create(
                    ingredient=summary.ingredient,
                    branch=plan.branch,
                    plan=plan,
                    summary=summary,
                    raw_total_g=float(summary.raw_total_g),
                    adjusted_total_g=float(summary.adjusted_total_g),
                    actual_total_g=float(summary.actual_total_g),
                    observed_factor=observed_factor,
                )

            recompute_scale_from_samples(scale)

            updated_count += 1

    return updated_count


def rebuild_daily_plan_scales():
    """
    Full rebuild from finalized plans only.
    This completely resets learned scale history.
    """

    with transaction.atomic():

        DailyPlanIngredientScale.objects.all().delete()
        DailyPlanIngredientScaleSample.objects.all().delete()

        finalized_plans = DailyConsumptionPlan.objects.filter(status="final").order_by(
            "created_at"
        )

        for plan in finalized_plans:
            update_daily_plan_scales(plan)
