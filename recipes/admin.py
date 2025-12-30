from django.contrib import admin

# Register your models here.
from .models import Recipe, RecipeIngredient


class RecipeIngredientInline(admin.TabularInline):
    model = RecipeIngredient
    extra = 0
    fields = (
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
    )
    ordering = ("item_no", "id")


@admin.register(Recipe)
class RecipeAdmin(admin.ModelAdmin):
    list_display = ("name", "is_active", "updated_at", "created_at")
    list_filter = ("is_active",)
    search_fields = ("name",)
    inlines = [RecipeIngredientInline]


@admin.register(RecipeIngredient)
class RecipeIngredientAdmin(admin.ModelAdmin):
    list_display = (
        "recipe", "item_no", "name", "group",
        "q10_g", "b", "c_g",
        "min_per_person_g", "max_per_person_g",
        "option_group", "option_value", "is_active",
    )
    list_filter = ("group", "is_active", "option_group", "recipe")
    search_fields = ("name", "recipe__name", "option_value", "option_group")
    ordering = ("recipe", "item_no", "id")