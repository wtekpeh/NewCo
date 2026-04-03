from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from typing import Any, cast


from rest_framework import status
from django.db import transaction

from accounts.models import StaffProfile, BranchRoleAssignment, Branch
from accounts.serializers import (
    StaffProfileListSerializer,
    StaffProfileCreateSerializer,
    StaffRoleUpdateSerializer,
)

from accounts.permissions import has_global_access


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def get_current_user(request):
    user = request.user

    active_branch_roles = user.branch_roles.filter(
        is_active=True,
        branch__is_active=True,
    ).select_related("branch")

    branch_roles_data = [
        {
            "branch_id": assignment.branch.id,
            "branch_name": assignment.branch.name,
            "role": assignment.role,
            "is_active": assignment.is_active,
        }
        for assignment in active_branch_roles
    ]

    can_create_batch_any = has_global_access(user) or any(
        assignment.role == "branch_manager" for assignment in active_branch_roles
    )

    return Response(
        {
            "id": user.id,
            "email": user.email,
            "full_name": user.full_name,
            "global_role": user.global_role,
            "can_recalibrate": has_global_access(user),
            "can_create_batch_any": can_create_batch_any,
            "branch_roles": branch_roles_data,
        }
    )


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def list_users(request):
    user = request.user

    # Only boss / managing director
    if not has_global_access(user):
        return Response(
            {"detail": "Not authorized"},
            status=status.HTTP_403_FORBIDDEN,
        )

    users = StaffProfile.objects.all().prefetch_related("branch_roles__branch")

    serializer = StaffProfileListSerializer(users, many=True)
    return Response(serializer.data)


@api_view(["PUT"])
@permission_classes([IsAuthenticated])
def update_user_roles(request, pk):
    user = request.user

    if not has_global_access(user):
        return Response(
            {"detail": "Not authorized"},
            status=status.HTTP_403_FORBIDDEN,
        )

    try:
        staff = StaffProfile.objects.get(id=pk)
    except StaffProfile.DoesNotExist:
        return Response(
            {"detail": "User not found"},
            status=status.HTTP_404_NOT_FOUND,
        )

    serializer = StaffRoleUpdateSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    validated_data = cast(dict[str, Any], serializer.validated_data)

    with transaction.atomic():
        if "global_role" in request.data:
            staff.global_role = validated_data["global_role"]

        if "is_active" in request.data:
            staff.is_active = validated_data["is_active"]

        staff.save()

        if "branch_roles" in request.data:
            BranchRoleAssignment.objects.filter(staff_profile=staff).delete()

            branch_roles = cast(list[dict[str, Any]], validated_data["branch_roles"])

            for role_data in branch_roles:
                branch_id = role_data["branch_id"]
                role = role_data["role"]
                branch_is_active = role_data["is_active"]

                try:
                    branch = Branch.objects.get(id=branch_id)
                except Branch.DoesNotExist:
                    return Response(
                        {"detail": f"Branch with id {branch_id} not found."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )

                BranchRoleAssignment.objects.create(
                    staff_profile=staff,
                    branch=branch,
                    role=role,
                    is_active=branch_is_active,
                )

    staff.refresh_from_db()

    return Response(StaffProfileListSerializer(staff).data)

    return Response(StaffProfileListSerializer(staff).data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def list_branches(request):
    branches = Branch.objects.filter(is_active=True).order_by("name")

    data: list[dict[str, Any]] = []

    for branch in branches:
        data.append(
            {
                "id": int(branch.pk),
                "name": str(branch.name),
                "code": str(branch.code or ""),
            }
        )

    return Response(data)
