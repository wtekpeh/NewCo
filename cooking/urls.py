from django.urls import path
from . import views

urlpatterns = [
    path("cooking/batches/", views.list_cook_batches, name="cooking-batch-list"),
    path("cooking/batches/<int:batch_id>/", views.retrieve_cook_batch, name="cooking-batch-detail"),
    path("cooking/batches/<int:batch_id>/actuals/", views.update_cook_batch_actuals, name="cooking-batch-actuals"),
    path("cooking/batches/create/", views.create_cook_batch, name="cooking-batch-create"),
]
