from django.db import models

# Create your models here.


class Recipe(models.Model):
    """
    A meal bundle, e.g. "Jollof + Kelewele + Protein".
    Each recipe has many RecipeIngredient rows that define scaling params.
    """
    name = models.CharField(max_length=200, unique=True)
    description = models.TextField(blank=True)

    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return self.name


class RecipeIngredient(models.Model):
    """
    Stores scaling parameters per ingredient for a given recipe.

    Core fields:
      - q10_g: grams for 10 people
      - b: exponent
      - c_g: floor

    Optional "option" logic:
      - option_group: e.g. "protein"
      - option_value: e.g. "FRESH CHICKEN"
      If an ingredient has option_group set, it is only included when options match.
    """
    GROUP_CHOICES = (
        ("bulk", "Bulk"),
        ("medium", "Medium"),
        ("aromatic", "Aromatic"),
        ("seasoning", "Seasoning"),
        ("protein", "Protein"),
        ("other", "Other"),
    )

    recipe = models.ForeignKey(Recipe, on_delete=models.CASCADE, related_name="ingredients")

    item_no = models.IntegerField(null=True, blank=True)  # optional: preserve your sheet numbering
    name = models.CharField(max_length=200)

    # scaling params (all grams)
    q10_g = models.FloatField()
    b = models.FloatField(default=1.0)
    c_g = models.FloatField(default=0.0)

        # scaling params (all grams)
    q10_g = models.FloatField()
    b = models.FloatField(default=1.0)
    c_g = models.FloatField(default=0.0)

    # safety guardrails (grams per person)
    min_per_person_g = models.FloatField(
        null=True,
        blank=True,
        help_text="Minimum grams per person (safety floor)"
    )
    max_per_person_g = models.FloatField(
        null=True,
        blank=True,
        help_text="Maximum grams per person (safety ceiling)"
    )

    group = models.CharField(max_length=20, choices=GROUP_CHOICES, default="other")

    # option logic (for protein choice etc.)
    option_group = models.CharField(max_length=50, blank=True)   # e.g. "protein"
    option_value = models.CharField(max_length=200, blank=True)  # e.g. "FRESH CHICKEN"

    is_active = models.BooleanField(default=True)

    class Meta:
        unique_together = ("recipe", "name", "option_group", "option_value")
        ordering = ["item_no", "id"]

    def __str__(self) -> str:
        if self.option_group and self.option_value:
            return f"{self.recipe.name} - {self.name} ({self.option_group}={self.option_value})"
        return f"{self.recipe.name} - {self.name}"
