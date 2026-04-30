from django.db import models
from django.utils import timezone


class CookBatch(models.Model):
    """
    A single cooking run for a given recipe and number of people.
    This is the "header" record for logging and future learning.
    """

    recipe = models.ForeignKey(
        "recipes.Recipe",
        on_delete=models.PROTECT,
        related_name="cook_batches",
    )

    branch = models.ForeignKey(
        "accounts.Branch",
        on_delete=models.PROTECT,
        related_name="cook_batches",
    )

    created_by = models.ForeignKey(
        "accounts.StaffProfile",
        on_delete=models.PROTECT,
        related_name="created_cook_batches",
    )

    n_people = models.PositiveIntegerField()

    used_date = models.DateField(
        null=True,
        blank=True,
        help_text="Date this batch is planned to be used.",
    )

    # Keep options flexible (today: protein choice; future: spice level, etc.)
    options = models.JSONField(default=dict, blank=True)

    # Convenience: store chosen protein explicitly (optional but useful)
    protein_type = models.CharField(max_length=100, blank=True)

    # Workflow fields
    status = models.CharField(
        max_length=20,
        default="draft",
        help_text="draft / confirmed / cooked / final",
    )

    notes = models.TextField(blank=True)

    # Lock actuals after approval (Boss / MD)
    actuals_locked = models.BooleanField(default=False)

    actuals_locked_by = models.ForeignKey(
        "accounts.StaffProfile",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="locked_batches",
    )

    actuals_locked_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Batch #{self.id} | {self.recipe_id} | {self.branch_id} | {self.n_people} people"


class CookBatchItem(models.Model):
    """
    One ingredient row for a CookBatch.
    We store BOTH:
      - predicted values (raw + clamped)
      - actual used values (entered by kitchen)
    """

    batch = models.ForeignKey(
        CookBatch,
        on_delete=models.CASCADE,
        related_name="items",
    )

    ingredient = models.CharField(max_length=120)

    group = models.CharField(max_length=40, blank=True)

    # Snapshot of scaling parameters used at the time (so history is consistent)
    q10_g = models.FloatField()
    b = models.FloatField()
    c_g = models.FloatField(default=0.0)

    # Bounds snapshot (optional)
    min_per_person_g = models.FloatField(null=True, blank=True)
    max_per_person_g = models.FloatField(null=True, blank=True)

    # What the engine predicted
    pred_g = models.FloatField()
    pred_kg = models.FloatField()

    # What the system recommends operationally (after bounds clamp)
    final_g = models.FloatField()
    final_kg = models.FloatField()
    was_clamped = models.BooleanField(default=False)

    # What was actually used in the kitchen (can be filled later)
    actual_g = models.FloatField(null=True, blank=True)
    actual_kg = models.FloatField(null=True, blank=True)

    notes = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(
                fields=["batch", "ingredient"],
                name="uniq_batch_ingredient",
            )
        ]

    def __str__(self):
        return f"{self.ingredient} (Batch #{self.batch_id})"


class IngredientScale(models.Model):
    """
    Stores learned calibration scale factors per ingredient.

    Scope:
      - global: branch=None, recipe=None
      - branch-specific: branch set, recipe=None
      - recipe-specific: recipe set, branch=None
      - branch+recipe specific: both set

    For now we can start with global storage, but the model supports
    future branch/recipe scoped calibration without redesign.
    """

    ingredient = models.CharField(max_length=120)

    branch = models.ForeignKey(
        "accounts.Branch",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="ingredient_scales",
    )

    recipe = models.ForeignKey(
        "recipes.Recipe",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="ingredient_scales",
    )

    s = models.FloatField(help_text="Learned multiplicative scale factor")

    tau_days = models.FloatField(default=14.0)

    sample_count = models.PositiveIntegerField(
        default=0,
        help_text="Number of log rows used for this learned scale",
    )

    computed_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["ingredient"]
        constraints = [
            models.UniqueConstraint(
                fields=["ingredient", "branch", "recipe"],
                name="uniq_ingredient_scale_scope",
            )
        ]

    def __str__(self):
        scope = []
        if self.branch_id:
            scope.append(f"branch={self.branch_id}")
        if self.recipe_id:
            scope.append(f"recipe={self.recipe_id}")
        scope_text = ", ".join(scope) if scope else "global"
        return f"{self.ingredient} | s={self.s:.4f} | {scope_text}"
