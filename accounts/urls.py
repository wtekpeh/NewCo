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
    # branch manager
    path(
        "branch-manager/staff/",
        views.branch_manager_list_staff,
        name="branch-manager-list-staff",
    ),
    path(
        "branch-manager/branches/",
        views.branch_manager_list_branches,
        name="branch-manager-list-branches",
    ),
    path(
        "branch-manager/user-search/",
        views.branch_manager_user_search,
        name="branch-manager-user-search",
    ),
    path(
        "branch-manager/branch-roles/",
        views.branch_manager_create_assignment,
        name="branch-manager-create-assignment",
    ),
    path(
        "branch-manager/branch-roles/<int:assignment_id>/",
        views.branch_manager_update_assignment,
        name="branch-manager-update-assignment",
    ),
    path(
        "branch-manager/branch-roles/<int:assignment_id>/",
        views.branch_manager_update_assignment,
        name="branch-manager-update-assignment",
    ),
]
