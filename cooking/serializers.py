from rest_framework import serializers
from .models import (
    CookBatch,
    CookBatchItem,
    DailyConsumptionPlan,
    DailyConsumptionPlanRecipe,
    DailyConsumptionPlanIngredientSummary,
    DailySharedIngredientRule,
)


class CookBatchItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = CookBatchItem
        fields = [
            "id",
            "ingredient",
            "group",
            "q10_g",
            "b",
            "c_g",
            "min_per_person_g",
            "max_per_person_g",
            "pred_g",
            "pred_kg",
            "final_g",
            "final_kg",
            "was_clamped",
            "actual_g",
            "actual_kg",
            "notes",
        ]


class CookBatchSerializer(serializers.ModelSerializer):
    items = CookBatchItemSerializer(many=True, read_only=True)
    recipe_name = serializers.CharField(source="recipe.name", read_only=True)
    branch_name = serializers.CharField(source="branch.name", read_only=True)
    created_by_name = serializers.CharField(
        source="created_by.full_name", read_only=True
    )
    actuals_locked = serializers.BooleanField(read_only=True)

    actuals_locked_by_name = serializers.CharField(
        source="actuals_locked_by.full_name",
        read_only=True,
    )

    recipe_actuals_locked = serializers.BooleanField(
        source="recipe.actuals_locked",
        read_only=True,
    )

    recipe_actuals_locked_by_name = serializers.CharField(
        source="recipe.actuals_locked_by.full_name",
        read_only=True,
    )

    recipe_actuals_locked_at = serializers.DateTimeField(
        source="recipe.actuals_locked_at",
        read_only=True,
    )
    actuals_locked_at = serializers.DateTimeField(read_only=True)

    class Meta:
        model = CookBatch
        fields = [
            "id",
            "recipe",
            "recipe_name",
            "branch",
            "branch_name",
            "created_by",
            "created_by_name",
            "n_people",
            "used_date",
            "options",
            "protein_type",
            "status",
            "notes",
            "created_at",
            "actuals_locked",
            "actuals_locked_by_name",
            "actuals_locked_at",
            "recipe_actuals_locked",
            "recipe_actuals_locked_by_name",
            "recipe_actuals_locked_at",
            "items",
        ]
        read_only_fields = [
            "created_by",
            "created_by_name",
            "branch_name",
            "recipe_name",
            "created_at",
            "items",
        ]


class CookBatchCreateRequestSerializer(serializers.Serializer):
    """
    What the frontend sends to create a batch log.
    We will generate predictions server-side and save atomically.
    """

    recipe_id = serializers.IntegerField()
    branch_id = serializers.IntegerField()
    n_people = serializers.IntegerField(min_value=1)
    used_date = serializers.DateField(required=False)
    options = serializers.DictField(required=False)
    notes = serializers.CharField(required=False, allow_blank=True)


class CookBatchActualItemUpdateSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    actual_g = serializers.FloatField(min_value=0)
    notes = serializers.CharField(required=False, allow_blank=True)


class CookBatchActualsUpdateRequestSerializer(serializers.Serializer):
    items = CookBatchActualItemUpdateSerializer(many=True)
    finalize = serializers.BooleanField(required=False, default=False)


class CookBatchPostReviewItemUpdateSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    actual_g = serializers.FloatField(min_value=0)
    notes = serializers.CharField(required=False, allow_blank=True)


class CookBatchPostReviewUpdateRequestSerializer(serializers.Serializer):
    items = CookBatchPostReviewItemUpdateSerializer(many=True)
    notes = serializers.CharField(required=False, allow_blank=True)


class DailyPlanActualItemUpdateSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    actual_total_g = serializers.FloatField(min_value=0)


class DailyPlanActualsUpdateRequestSerializer(serializers.Serializer):
    items = DailyPlanActualItemUpdateSerializer(many=True)
    finalize = serializers.BooleanField(required=False, default=False)


class DailyConsumptionPlanRecipeInputSerializer(serializers.Serializer):
    recipe_id = serializers.IntegerField()
    n_people = serializers.IntegerField(min_value=1)
    options = serializers.DictField(required=False)
    notes = serializers.CharField(required=False, allow_blank=True)


class DailyConsumptionPlanCreateRequestSerializer(serializers.Serializer):
    branch_id = serializers.IntegerField()
    used_date = serializers.DateField()
    recipes = DailyConsumptionPlanRecipeInputSerializer(many=True)
    notes = serializers.CharField(required=False, allow_blank=True)

    def validate_recipes(self, value):
        if not value:
            raise serializers.ValidationError(
                "At least one recipe is required for a daily consumption plan."
            )

        if len(value) < 2:
            raise serializers.ValidationError(
                "Daily Consumption Plan requires at least two recipes. Use Single Consumption for one recipe."
            )

        return value


class DailyConsumptionPlanRecipeSerializer(serializers.ModelSerializer):
    recipe_name = serializers.CharField(source="recipe.name", read_only=True)
    cook_batch_detail = CookBatchSerializer(source="cook_batch", read_only=True)

    class Meta:
        model = DailyConsumptionPlanRecipe
        fields = [
            "id",
            "recipe",
            "recipe_name",
            "cook_batch",
            "cook_batch_detail",
            "n_people",
            "options",
            "protein_type",
        ]


class DailyConsumptionPlanIngredientSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = DailyConsumptionPlanIngredientSummary
        fields = [
            "id",
            "ingredient",
            "group",
            "raw_total_g",
            "raw_total_kg",
            "adjusted_total_g",
            "adjusted_total_kg",
            "daily_factor",
            "adjustment_g",
            "unit_display",
            "adjustment_reason",
            "is_shared_adjusted",
            "actual_total_g",
            "actual_total_kg",
        ]


class DailyConsumptionPlanSerializer(serializers.ModelSerializer):
    recipes = DailyConsumptionPlanRecipeSerializer(many=True, read_only=True)
    ingredient_summaries = DailyConsumptionPlanIngredientSummarySerializer(
        many=True,
        read_only=True,
    )
    branch_name = serializers.CharField(source="branch.name", read_only=True)
    created_by_name = serializers.CharField(
        source="created_by.full_name",
        read_only=True,
    )

    class Meta:
        model = DailyConsumptionPlan
        fields = [
            "id",
            "branch",
            "branch_name",
            "created_by",
            "created_by_name",
            "used_date",
            "status",
            "notes",
            "created_at",
            "recipes",
            "ingredient_summaries",
        ]
        read_only_fields = [
            "created_by",
            "created_by_name",
            "branch_name",
            "created_at",
            "recipes",
            "ingredient_summaries",
        ]


class DailySharedIngredientRuleSerializer(serializers.ModelSerializer):
    created_by_name = serializers.CharField(
        source="created_by.full_name",
        read_only=True,
    )

    class Meta:
        model = DailySharedIngredientRule
        fields = [
            "id",
            "keyword",
            "factor",
            "is_active",
            "created_by",
            "created_by_name",
            "created_at",
        ]
        read_only_fields = [
            "created_by",
            "created_by_name",
            "created_at",
        ]
