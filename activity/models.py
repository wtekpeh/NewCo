from django.db import models
from django.utils import timezone


class ActivityAction(models.TextChoices):
    COOK_BATCH_CREATED = "cook_batch_created", "Cook Batch Created"
    COOK_BATCH_ACTUALS_UPDATED = (
        "cook_batch_actuals_updated",
        "Cook Batch Actuals Updated",
    )
    COOK_BATCH_FINALIZED = "cook_batch_finalized", "Cook Batch Finalized"
    INGREDIENT_SCALES_RECALIBRATED = (
        "ingredient_scales_recalibrated",
        "Ingredient Scales Recalibrated",
    )


class ActivityTargetType(models.TextChoices):
    COOK_BATCH = "cook_batch", "Cook Batch"
    RECIPE = "recipe", "Recipe"
    INGREDIENT_SCALE = "ingredient_scale", "Ingredient Scale"
    SYSTEM = "system", "System"


class ActivityEvent(models.Model):
    actor_staff_profile = models.ForeignKey(
        "accounts.StaffProfile",
        on_delete=models.PROTECT,
        related_name="activity_events",
    )

    action = models.CharField(
        max_length=100,
        choices=ActivityAction.choices,
    )

    target_type = models.CharField(
        max_length=50,
        choices=ActivityTargetType.choices,
    )

    target_id = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Primary key of the target record if applicable.",
    )

    branch = models.ForeignKey(
        "accounts.Branch",
        on_delete=models.PROTECT,
        related_name="activity_events",
        null=True,
        blank=True,
    )

    message = models.TextField(blank=True)

    metadata_json = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(default=timezone.now)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["action"]),
            models.Index(fields=["target_type", "target_id"]),
            models.Index(fields=["branch"]),
            models.Index(fields=["processed_at"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self):
        return f"{self.action} | {self.target_type} | {self.target_id or '-'}"
