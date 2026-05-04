from django.urls import path
from . import views

urlpatterns = [
    path("me/", views.get_current_user, name="accounts-me"),
    # user management
    # user management
    path("users/", views.list_users, name="accounts-list-users"),
    # branches
    path("branches/", views.list_branches, name="accounts-list-branches"),
    path("branches/create/", views.create_branch, name="accounts-create-branch"),
    path(
        "branches/<int:pk>/",
        views.get_branch_detail,
        name="accounts-get-branch-detail",
    ),
    path(
        "branches/<int:pk>/update/",
        views.update_branch,
        name="accounts-update-branch",
    ),
    path(
        "branches/<int:pk>/status/",
        views.update_branch_status,
        name="accounts-update-branch-status",
    ),
    path(
        "users/<int:pk>/roles/",
        views.update_user_roles,
        name="accounts-update-user-roles",
    ),
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
]
