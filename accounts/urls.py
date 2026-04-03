from django.urls import path
from . import views

urlpatterns = [
    path("me/", views.get_current_user, name="accounts-me"),
    # user management
    path("users/", views.list_users, name="accounts-list-users"),
    path(
        "users/<int:pk>/roles/",
        views.update_user_roles,
        name="accounts-update-user-roles",
    ),
    path("branches/", views.list_branches, name="accounts-list-branches"),
]
