from django.urls import path
from . import views

urlpatterns = [
    path("cooking/batches/", views.list_cook_batches, name="cooking-batch-list"),
    path(
        "cooking/batches/<int:batch_id>/",
        views.retrieve_cook_batch,
        name="cooking-batch-detail",
    ),
    path(
        "cooking/batches/<int:batch_id>/actuals/",
        views.update_cook_batch_actuals,
        name="cooking-batch-actuals",
    ),
    path(
        "cooking/batches/<int:batch_id>/post-review/",
        views.post_review_update_cook_batch,
        name="cooking-batch-post-review",
    ),
    path(
        "cooking/batches/create/", views.create_cook_batch, name="cooking-batch-create"
    ),
    path(
        "cooking/recalibrate/",
        views.recalibrate_ingredient_scales,
        name="cooking-recalibrate",
    ),
    path(
        "batches/<int:batch_id>/lock-actuals/",
        views.lock_cook_batch_actuals,
    ),
    path(
        "cooking/daily-plans/create/",
        views.create_daily_consumption_plan_view,
        name="daily-consumption-plan-create",
    ),
    path(
        "cooking/daily-plans/",
        views.list_daily_consumption_plans,
        name="daily-consumption-plan-list",
    ),
    path(
        "cooking/daily-plans/<int:plan_id>/",
        views.retrieve_daily_consumption_plan,
        name="daily-consumption-plan-detail",
    ),
]
