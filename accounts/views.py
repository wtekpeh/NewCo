from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from typing import Any, cast


from rest_framework import status
from rest_framework.pagination import PageNumberPagination
from django.db import transaction, models

from accounts.models import StaffProfile, BranchRoleAssignment, Branch
from accounts.permissions import get_managed_branch_ids, has_any_managed_branch
from accounts.serializers import (
    StaffProfileListSerializer,
    StaffRoleUpdateSerializer,
    BranchManagerAssignmentListSerializer,
    BranchManagerUserSearchSerializer,
    BranchManagerAssignmentCreateSerializer,
    BranchManagerAssignmentUpdateSerializer,
    BranchSerializer,
)

from accounts.permissions import has_global_access


class AdminListPagination(PageNumberPagination):
    page_size = 10
    page_size_query_param = "page_size"
    max_page_size = 100


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

    can_update_batch_any = has_global_access(user) or any(
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
            "can_update_batch": can_update_batch_any,
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

    search = (request.query_params.get("search") or "").strip()
    role = (request.query_params.get("role") or "").strip()
    branch = request.query_params.get("branch")

    users = StaffProfile.objects.all().prefetch_related("branch_roles__branch")

    if search:
        users = users.filter(
            models.Q(full_name__icontains=search)
            | models.Q(email__icontains=search)
            | models.Q(username__icontains=search)
        )

    if role:
        if role in ["boss", "managing_director", "none"]:
            users = users.filter(global_role=role)
        elif role in ["branch_manager", "chef", "kitchen_staff", "store"]:
            users = users.filter(
                branch_roles__role=role,
                branch_roles__is_active=True,
                branch_roles__branch__is_active=True,
            )
        else:
            return Response(
                {"detail": "Invalid role filter."},
                status=status.HTTP_400_BAD_REQUEST,
            )

    if branch:
        try:
            branch_id = int(branch)
        except ValueError:
            return Response(
                {"detail": "Invalid branch filter."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        users = users.filter(
            branch_roles__branch_id=branch_id,
            branch_roles__branch__is_active=True,
        )

    users = users.distinct().order_by("full_name", "email")

    paginator = AdminListPagination()
    paginated_users = paginator.paginate_queryset(users, request)
    serializer = StaffProfileListSerializer(paginated_users, many=True)

    return paginator.get_paginated_response(serializer.data)


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
    branches = Branch.objects.all().order_by("name")
    serializer = BranchSerializer(branches, many=True)
    return Response(serializer.data)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def create_branch(request):
    user = request.user

    if not has_global_access(user):
        return Response(
            {"detail": "Not authorized"},
            status=status.HTTP_403_FORBIDDEN,
        )

    serializer = BranchSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    with transaction.atomic():
        branch = serializer.save()

    return Response(
        BranchSerializer(branch).data,
        status=status.HTTP_201_CREATED,
    )


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def get_branch_detail(request, pk):
    try:
        branch = Branch.objects.get(pk=pk)
    except Branch.DoesNotExist:
        return Response(
            {"detail": "Branch not found"},
            status=status.HTTP_404_NOT_FOUND,
        )

    return Response(BranchSerializer(branch).data)


@api_view(["PUT", "PATCH"])
@permission_classes([IsAuthenticated])
def update_branch(request, pk):
    user = request.user

    if not has_global_access(user):
        return Response(
            {"detail": "Not authorized"},
            status=status.HTTP_403_FORBIDDEN,
        )

    try:
        branch = Branch.objects.get(pk=pk)
    except Branch.DoesNotExist:
        return Response(
            {"detail": "Branch not found"},
            status=status.HTTP_404_NOT_FOUND,
        )

    serializer = BranchSerializer(branch, data=request.data, partial=True)
    serializer.is_valid(raise_exception=True)

    with transaction.atomic():
        branch = serializer.save()

    return Response(BranchSerializer(branch).data)


@api_view(["PATCH"])
@permission_classes([IsAuthenticated])
def update_branch_status(request, pk):
    user = request.user

    if not has_global_access(user):
        return Response(
            {"detail": "Not authorized"},
            status=status.HTTP_403_FORBIDDEN,
        )

    try:
        branch = Branch.objects.get(pk=pk)
    except Branch.DoesNotExist:
        return Response(
            {"detail": "Branch not found"},
            status=status.HTTP_404_NOT_FOUND,
        )

    if "is_active" not in request.data:
        return Response(
            {"detail": "is_active is required."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    is_active = request.data.get("is_active")

    if not isinstance(is_active, bool):
        return Response(
            {"detail": "is_active must be true or false."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    with transaction.atomic():
        branch.is_active = is_active
        branch.save(update_fields=["is_active", "updated_at"])

    return Response(BranchSerializer(branch).data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def branch_manager_list_staff(request):
    user = request.user

    # Must be a branch manager
    if not has_any_managed_branch(user):
        return Response(
            {"detail": "Not authorized"},
            status=status.HTTP_403_FORBIDDEN,
        )

    managed_branch_ids = get_managed_branch_ids(user)

    queryset = BranchRoleAssignment.objects.filter(
        branch_id__in=managed_branch_ids,
        role__in=["chef", "kitchen_staff", "store"],
        is_active=True,
        branch__is_active=True,
        staff_profile__is_active=True,
    ).select_related("branch", "staff_profile")

    search = (request.query_params.get("search") or "").strip()
    branch_id = request.query_params.get("branch")
    role = (request.query_params.get("role") or "").strip()

    if branch_id:
        try:
            branch_id_int = int(branch_id)
        except ValueError:
            return Response(
                {"detail": "Invalid branch filter."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if branch_id_int not in managed_branch_ids:
            return Response(
                {"detail": "Not authorized for this branch."},
                status=status.HTTP_403_FORBIDDEN,
            )

        queryset = queryset.filter(branch_id=branch_id_int)

    if role:
        if role not in ["chef", "kitchen_staff", "store"]:
            return Response(
                {"detail": "Invalid role filter."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        queryset = queryset.filter(role=role)

    if search:
        queryset = queryset.filter(
            models.Q(staff_profile__full_name__icontains=search)
            | models.Q(staff_profile__email__icontains=search)
            | models.Q(staff_profile__username__icontains=search)
        )

    queryset = queryset.order_by(
        "branch__name",
        "role",
        "staff_profile__full_name",
    )

    paginator = AdminListPagination()
    paginated_queryset = paginator.paginate_queryset(queryset, request)
    serializer = BranchManagerAssignmentListSerializer(paginated_queryset, many=True)

    return paginator.get_paginated_response(serializer.data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def branch_manager_list_branches(request):
    user = request.user

    if not has_any_managed_branch(user):
        return Response(
            {"detail": "Not authorized"},
            status=status.HTTP_403_FORBIDDEN,
        )

    managed_branch_ids = get_managed_branch_ids(user)

    branches = Branch.objects.filter(
        id__in=managed_branch_ids,
        is_active=True,
    ).order_by("name")

    data = [
        {
            "id": int(branch.pk),
            "name": str(branch.name),
            "code": str(branch.code or ""),
        }
        for branch in branches
    ]

    return Response(data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def branch_manager_user_search(request):
    user = request.user

    if not has_any_managed_branch(user):
        return Response(
            {"detail": "Not authorized"},
            status=status.HTTP_403_FORBIDDEN,
        )

    search = (request.query_params.get("search") or "").strip()

    managed_branch_ids = get_managed_branch_ids(user)

    # Exclude users already assigned to ANY managed branch
    assigned_user_ids = BranchRoleAssignment.objects.filter(
        branch_id__in=managed_branch_ids
    ).values_list("staff_profile_id", flat=True)

    queryset = StaffProfile.objects.filter(is_active=True).exclude(
        id__in=assigned_user_ids
    )

    if search:
        queryset = queryset.filter(
            models.Q(full_name__icontains=search)
            | models.Q(email__icontains=search)
            | models.Q(username__icontains=search)
        )

    queryset = queryset.order_by("full_name", "email")[:20]

    serializer = BranchManagerUserSearchSerializer(queryset, many=True)
    return Response(serializer.data)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def branch_manager_create_assignment(request):
    user = request.user

    if not has_any_managed_branch(user):
        return Response(
            {"detail": "Not authorized"},
            status=status.HTTP_403_FORBIDDEN,
        )

    serializer = BranchManagerAssignmentCreateSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    validated_data = cast(dict[str, Any], serializer.validated_data)

    staff_profile_id = validated_data["staff_profile_id"]
    branch_id = validated_data["branch_id"]
    role = validated_data["role"]

    managed_branch_ids = get_managed_branch_ids(user)

    if branch_id not in managed_branch_ids:
        return Response(
            {"detail": "Not authorized for this branch."},
            status=status.HTTP_403_FORBIDDEN,
        )

    with transaction.atomic():
        existing_assignment = (
            BranchRoleAssignment.objects.select_for_update()
            .filter(
                staff_profile_id=staff_profile_id,
                branch_id=branch_id,
            )
            .exists()
        )

        if existing_assignment:
            return Response(
                {"detail": "This user already has an assignment in this branch."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        assignment = BranchRoleAssignment.objects.create(
            staff_profile_id=staff_profile_id,
            branch_id=branch_id,
            role=role,
            is_active=True,
        )

    assignment = BranchRoleAssignment.objects.select_related(
        "branch",
        "staff_profile",
    ).get(id=assignment.pk)

    response_serializer = BranchManagerAssignmentListSerializer(assignment)
    return Response(response_serializer.data, status=status.HTTP_201_CREATED)


@api_view(["PUT"])
@permission_classes([IsAuthenticated])
def branch_manager_update_assignment(request, assignment_id):
    user = request.user

    if not has_any_managed_branch(user):
        return Response(
            {"detail": "Not authorized"},
            status=status.HTTP_403_FORBIDDEN,
        )

    try:
        assignment = BranchRoleAssignment.objects.select_related(
            "branch",
            "staff_profile",
        ).get(id=assignment_id)
    except BranchRoleAssignment.DoesNotExist:
        return Response(
            {"detail": "Assignment not found."},
            status=status.HTTP_404_NOT_FOUND,
        )

    managed_branch_ids = get_managed_branch_ids(user)

    if int(assignment.branch.pk) not in managed_branch_ids:
        return Response(
            {"detail": "Not authorized for this assignment."},
            status=status.HTTP_403_FORBIDDEN,
        )

    if assignment.role not in ["chef", "kitchen_staff", "store"]:
        return Response(
            {"detail": "Only chef and kitchen staff assignments can be updated here."},
            status=status.HTTP_403_FORBIDDEN,
        )

    serializer = BranchManagerAssignmentUpdateSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    validated_data = cast(dict[str, Any], serializer.validated_data)
    assignment.role = validated_data["role"]
    assignment.save(update_fields=["role", "updated_at"])

    assignment.refresh_from_db()

    response_serializer = BranchManagerAssignmentListSerializer(assignment)
    return Response(response_serializer.data, status=status.HTTP_200_OK)


@api_view(["DELETE"])
@permission_classes([IsAuthenticated])
def branch_manager_delete_assignment(request, assignment_id):
    user = request.user

    if not has_any_managed_branch(user):
        return Response(
            {"detail": "Not authorized"},
            status=status.HTTP_403_FORBIDDEN,
        )

    try:
        assignment = BranchRoleAssignment.objects.select_related(
            "branch",
            "staff_profile",
        ).get(pk=assignment_id)
    except BranchRoleAssignment.DoesNotExist:
        return Response(
            {"detail": "Assignment not found."},
            status=status.HTTP_404_NOT_FOUND,
        )

    managed_branch_ids = get_managed_branch_ids(user)

    if int(assignment.branch.pk) not in managed_branch_ids:
        return Response(
            {"detail": "Not authorized for this assignment."},
            status=status.HTTP_403_FORBIDDEN,
        )

    if assignment.role not in ["chef", "kitchen_staff", "store"]:
        return Response(
            {"detail": "Only chef and kitchen staff assignments can be deleted here."},
            status=status.HTTP_403_FORBIDDEN,
        )

    assignment.delete()
    return Response(status=status.HTTP_204_NO_CONTENT)
