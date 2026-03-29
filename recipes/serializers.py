from rest_framework import serializers
from .models import Recipe, RecipeIngredient


class RecipeIngredientSerializer(serializers.ModelSerializer):
    class Meta:
        model = RecipeIngredient
        fields = [
            "id",
            "item_no",
            "name",
            "q10_g",
            "b",
            "c_g",
            "group",
            "option_group",
            "option_value",
            "is_active",
        ]


class RecipeSerializer(serializers.ModelSerializer):
    ingredients = RecipeIngredientSerializer(many=True, read_only=True)

    class Meta:
        model = Recipe
        fields = ["id", "name", "description", "is_active", "ingredients"]


class RecipePredictOptionsSerializer(serializers.Serializer):
    protein = serializers.CharField(required=False, allow_blank=True)


class RecipePredictRequestSerializer(serializers.Serializer):
    n_people = serializers.FloatField()
    options = RecipePredictOptionsSerializer(required=False)


class RecipePredictItemSerializer(serializers.Serializer):
    ingredient = serializers.CharField()
    group = serializers.CharField(required=False, allow_null=True)

    q10_g = serializers.FloatField()
    b = serializers.FloatField()
    c_g = serializers.FloatField()

    # Base prediction (always available)
    base_pred_g = serializers.FloatField()
    base_pred_kg = serializers.FloatField()

    # Final prediction (what system uses)
    pred_g = serializers.FloatField()
    pred_kg = serializers.FloatField()

    # Explainability fields
    scale_used = serializers.FloatField(required=False)
    used_calibration = serializers.BooleanField()


class RecipePredictResponseSerializer(serializers.Serializer):
    recipe = serializers.DictField()
    n_people = serializers.FloatField()
    options = serializers.DictField()
    items = RecipePredictItemSerializer(many=True)
