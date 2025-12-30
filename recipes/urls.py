from django.urls import path
from . import views

urlpatterns = [
    path("recipes/", views.list_recipes, name="recipe-list"),
    path("recipes/<int:pk>/predict/", views.predict_recipe, name="recipe-predict"),
]
