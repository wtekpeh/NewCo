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

    # Model output
    pred_g = serializers.FloatField()
    pred_kg = serializers.FloatField()

    # Final post-safety values
    final_g = serializers.FloatField()
    final_kg = serializers.FloatField()
    was_clamped = serializers.BooleanField()

    # Explainability fields
    scale_used = serializers.FloatField(required=False)
    used_calibration = serializers.BooleanField()


class RecipePredictResponseSerializer(serializers.Serializer):
    recipe = serializers.DictField()
    n_people = serializers.FloatField()
    options = serializers.DictField()
    items = RecipePredictItemSerializer(many=True)


# =========================
# Management / write serializers
# =========================


class RecipeWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = Recipe
        fields = ["id", "name", "description", "is_active"]

    def validate_name(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Recipe name is required.")
        return value


class RecipeIngredientWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = RecipeIngredient
        fields = [
            "id",
            "recipe",
            "item_no",
            "name",
            "group",
            "q10_g",
            "b",
            "c_g",
            "min_per_person_g",
            "max_per_person_g",
            "option_group",
            "option_value",
            "is_active",
        ]

    def validate_name(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Ingredient name is required.")
        return value

    def validate_group(self, value):
        allowed = {choice[0] for choice in RecipeIngredient.GROUP_CHOICES}
        if value not in allowed:
            raise serializers.ValidationError("Invalid ingredient group.")
        return value

    def validate(self, attrs):
        instance = getattr(self, "instance", None)

        recipe = attrs.get("recipe") or getattr(instance, "recipe", None)
        name = attrs.get("name", getattr(instance, "name", "")).strip()
        option_group = (
            attrs.get("option_group", getattr(instance, "option_group", "")) or ""
        ).strip()
        option_value = (
            attrs.get("option_value", getattr(instance, "option_value", "")) or ""
        ).strip()
        group = attrs.get("group", getattr(instance, "group", "other"))

        q10_g = attrs.get("q10_g", getattr(instance, "q10_g", None))
        b = attrs.get("b", getattr(instance, "b", None))
        c_g = attrs.get("c_g", getattr(instance, "c_g", None))
        min_per_person_g = attrs.get(
            "min_per_person_g",
            getattr(instance, "min_per_person_g", None),
        )
        max_per_person_g = attrs.get(
            "max_per_person_g",
            getattr(instance, "max_per_person_g", None),
        )

        if recipe is None:
            raise serializers.ValidationError({"recipe": "Recipe is required."})

        if q10_g is None:
            raise serializers.ValidationError({"q10_g": "q10_g is required."})

        if b is None:
            raise serializers.ValidationError({"b": "b is required."})

        if c_g is None:
            raise serializers.ValidationError({"c_g": "c_g is required."})

        if min_per_person_g is not None and max_per_person_g is not None:
            if float(min_per_person_g) > float(max_per_person_g):
                raise serializers.ValidationError(
                    {
                        "max_per_person_g": "Must be greater than or equal to min_per_person_g."
                    }
                )

        option_group_lower = option_group.lower()

        if group == "protein":
            if option_group_lower != "protein":
                raise serializers.ValidationError(
                    {
                        "option_group": 'Protein ingredients must use option_group="protein".'
                    }
                )
            if not option_value:
                raise serializers.ValidationError(
                    {"option_value": "Protein ingredients must have an option_value."}
                )

        if option_group and not option_value:
            raise serializers.ValidationError(
                {
                    "option_value": "option_value is required when option_group is provided."
                }
            )

        qs = RecipeIngredient.objects.filter(
            recipe=recipe,
            name__iexact=name,
            option_group__iexact=option_group,
            option_value__iexact=option_value,
        )
        if instance:
            qs = qs.exclude(pk=instance.pk)

        if qs.exists():
            raise serializers.ValidationError(
                "An ingredient with this recipe, name, option_group, and option_value already exists."
            )

        attrs["name"] = name
        attrs["option_group"] = option_group
        attrs["option_value"] = option_value
        return attrs


class RecipeCSVRowSerializer(serializers.Serializer):
    recipe_name = serializers.CharField()
    recipe_description = serializers.CharField(required=False, allow_blank=True)
    recipe_is_active = serializers.BooleanField(required=False)
    item_no = serializers.IntegerField(required=False, allow_null=True)
    ingredient_name = serializers.CharField()
    group = serializers.ChoiceField(
        choices=[choice[0] for choice in RecipeIngredient.GROUP_CHOICES]
    )
    q10_g = serializers.FloatField()
    b = serializers.FloatField()
    c_g = serializers.FloatField()
    min_per_person_g = serializers.FloatField(required=False, allow_null=True)
    max_per_person_g = serializers.FloatField(required=False, allow_null=True)
    option_group = serializers.CharField(required=False, allow_blank=True)
    option_value = serializers.CharField(required=False, allow_blank=True)
    ingredient_is_active = serializers.BooleanField(required=False)

    def validate(self, attrs):
        recipe_name = (attrs.get("recipe_name") or "").strip()
        ingredient_name = (attrs.get("ingredient_name") or "").strip()
        group = attrs.get("group")
        option_group = (attrs.get("option_group") or "").strip()
        option_value = (attrs.get("option_value") or "").strip()
        min_per_person_g = attrs.get("min_per_person_g")
        max_per_person_g = attrs.get("max_per_person_g")

        if not recipe_name:
            raise serializers.ValidationError(
                {"recipe_name": "recipe_name is required."}
            )

        if not ingredient_name:
            raise serializers.ValidationError(
                {"ingredient_name": "ingredient_name is required."}
            )

        if min_per_person_g is not None and max_per_person_g is not None:
            if float(min_per_person_g) > float(max_per_person_g):
                raise serializers.ValidationError(
                    {
                        "max_per_person_g": "Must be greater than or equal to min_per_person_g."
                    }
                )

        if group == "protein":
            if option_group.lower() != "protein":
                raise serializers.ValidationError(
                    {"option_group": 'Protein rows must use option_group="protein".'}
                )
            if not option_value:
                raise serializers.ValidationError(
                    {"option_value": "Protein rows must provide option_value."}
                )

        attrs["recipe_name"] = recipe_name
        attrs["ingredient_name"] = ingredient_name
        attrs["option_group"] = option_group
        attrs["option_value"] = option_value
        return attrs


class ProteinChoiceListResponseSerializer(serializers.Serializer):
    count = serializers.IntegerField()
    results = serializers.ListField(child=serializers.CharField())
