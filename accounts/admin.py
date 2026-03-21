from django.contrib import admin
from .models import Branch, StaffProfile, BranchRoleAssignment


@admin.register(Branch)
class BranchAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "code", "location", "is_active", "created_at")
    search_fields = ("name", "code", "location")
    list_filter = ("is_active",)


@admin.register(StaffProfile)
class StaffProfileAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "full_name",
        "email",
        "username",
        "keycloak_sub",
        "global_role",
        "is_active",
        "created_at",
    )
    search_fields = ("full_name", "email", "username", "keycloak_sub")
    list_filter = ("global_role", "is_active", "created_at")


@admin.register(BranchRoleAssignment)
class BranchRoleAssignmentAdmin(admin.ModelAdmin):
    list_display = ("id", "staff_profile", "branch", "role", "is_active", "created_at")
    search_fields = (
        "staff_profile__full_name",
        "staff_profile__email",
        "branch__name",
        "role",
    )
    list_filter = ("role", "is_active", "branch")
