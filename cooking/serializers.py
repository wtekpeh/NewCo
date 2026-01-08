from rest_framework import serializers
from .models import CookBatch, CookBatchItem


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

    class Meta:
        model = CookBatch
        fields = [
            "id",
            "recipe",
            "recipe_name",
            "n_people",
            "options",
            "protein_type",
            "status",
            "notes",
            "created_at",
            "items",
        ]


class CookBatchCreateRequestSerializer(serializers.Serializer):
    """
    What the frontend sends to create a batch log.
    We will generate predictions server-side and save atomically.
    """
    recipe_id = serializers.IntegerField()
    n_people = serializers.IntegerField(min_value=1)
    options = serializers.DictField(required=False)
    notes = serializers.CharField(required=False, allow_blank=True)


class CookBatchActualItemUpdateSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    actual_g = serializers.FloatField(min_value=0)
    notes = serializers.CharField(required=False, allow_blank=True)


class CookBatchActualsUpdateRequestSerializer(serializers.Serializer):
    items = CookBatchActualItemUpdateSerializer(many=True)
    finalize = serializers.BooleanField(required=False, default=False)
