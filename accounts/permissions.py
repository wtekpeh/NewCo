from accounts.models import (
    GlobalRole,
    BranchRole,
    BranchRoleAssignment,
)


def is_boss(user):
    return bool(
        user
        and getattr(user, "is_authenticated", False)
        and user.global_role == GlobalRole.BOSS
    )


def is_managing_director(user):
    return bool(
        user
        and getattr(user, "is_authenticated", False)
        and user.global_role == GlobalRole.MANAGING_DIRECTOR
    )


def has_global_access(user):
    return is_boss(user) or is_managing_director(user)


def has_branch_role(user, branch, role):
    if not user or not getattr(user, "is_authenticated", False):
        return False

    if not branch:
        return False

    return BranchRoleAssignment.objects.filter(
        staff_profile=user,
        branch=branch,
        role=role,
        is_active=True,
        branch__is_active=True,
        staff_profile__is_active=True,
    ).exists()


def is_branch_manager(user, branch):
    return has_branch_role(user, branch, BranchRole.BRANCH_MANAGER)


def is_chef(user, branch):
    return has_branch_role(user, branch, BranchRole.CHEF)


def can_create_batch(user, branch):
    if not user or not getattr(user, "is_authenticated", False):
        return False

    if has_global_access(user):
        return True

    return is_branch_manager(user, branch)


def can_view_batch(user, branch):
    if not user or not getattr(user, "is_authenticated", False):
        return False

    if has_global_access(user):
        return True

    if is_branch_manager(user, branch):
        return True

    if is_chef(user, branch):
        return True

    return False


def can_update_batch(user, branch):
    if not user or not getattr(user, "is_authenticated", False):
        return False

    if has_global_access(user):
        return True

    return is_branch_manager(user, branch)
