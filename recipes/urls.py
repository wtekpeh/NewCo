from django.urls import path
from . import views

urlpatterns = [
    path("recipes/", views.list_recipes, name="recipe-list"),
    path(
        "recipes/protein-choices/",
        views.list_protein_choices,
        name="recipe-protein-choices",
    ),
    path("recipes/<int:pk>/predict/", views.predict_recipe, name="recipe-predict"),
    # Recipe management
    path("recipes/manage/", views.create_recipe, name="recipe-create"),
    path(
        "recipes/manage/import-csv/", views.import_recipes_csv, name="recipe-import-csv"
    ),
    path(
        "recipes/manage/<int:pk>/",
        views.recipe_manage_detail,
        name="recipe-manage-detail",
    ),
    path("recipes/<int:pk>/", views.recipe_detail, name="recipe-detail"),
    path(
        "recipes/<int:recipe_id>/lock-actuals/",
        views.lock_recipe_actuals,
        name="recipe-lock-actuals",
    ),
    # Recipe ingredient management
    path(
        "recipes/manage/<int:recipe_id>/ingredients/",
        views.list_recipe_ingredients,
        name="recipe-ingredient-list",
    ),
    path(
        "recipes/manage/<int:recipe_id>/ingredients/create/",
        views.create_recipe_ingredient,
        name="recipe-ingredient-create",
    ),
    path(
        "recipes/ingredients/<int:pk>/",
        views.recipe_ingredient_detail,
        name="recipe-ingredient-detail",
    ),
    path(
        "recipes/ingredients/<int:pk>/manage/",
        views.recipe_ingredient_manage_detail,
        name="recipe-ingredient-manage-detail",
    ),
    path(
        "recipes/<int:recipe_id>/unlock-actuals/",
        views.unlock_recipe_actuals,
        name="recipe-unlock-actuals",
    ),
]
