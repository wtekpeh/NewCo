from rest_framework import serializers

from .models import (
    Branch,
    BranchRole,
    BranchRoleAssignment,
    GlobalRole,
    StaffProfile,
)


class BranchRoleAssignmentSerializer(serializers.ModelSerializer):
    branch_id = serializers.IntegerField(source="branch.id", read_only=True)
    branch_name = serializers.CharField(source="branch.name", read_only=True)

    class Meta:
        model = BranchRoleAssignment
        fields = [
            "id",
            "branch_id",
            "branch_name",
            "role",
            "is_active",
        ]


class StaffProfileListSerializer(serializers.ModelSerializer):
    branch_roles = BranchRoleAssignmentSerializer(many=True, read_only=True)

    class Meta:
        model = StaffProfile
        fields = [
            "id",
            "keycloak_sub",
            "email",
            "username",
            "full_name",
            "global_role",
            "is_active",
            "branch_roles",
            "created_at",
            "updated_at",
        ]


class StaffProfileCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = StaffProfile
        fields = [
            "keycloak_sub",
            "email",
            "username",
            "full_name",
            "global_role",
            "is_active",
        ]

    def validate_global_role(self, value):
        valid_roles = {choice[0] for choice in GlobalRole.choices}
        if value not in valid_roles:
            raise serializers.ValidationError("Invalid global role.")
        return value


class BranchRoleWriteSerializer(serializers.Serializer):
    branch_id = serializers.IntegerField()
    role = serializers.ChoiceField(choices=BranchRole.choices)
    is_active = serializers.BooleanField(default=True)

    def validate_branch_id(self, value):
        if not Branch.objects.filter(id=value).exists():
            raise serializers.ValidationError("Selected branch does not exist.")
        return value


class StaffRoleUpdateSerializer(serializers.Serializer):
    global_role = serializers.ChoiceField(
        choices=GlobalRole.choices,
        required=False,
    )
    is_active = serializers.BooleanField(required=False)
    branch_roles = BranchRoleWriteSerializer(many=True, required=False)

    def save(self, *, staff: StaffProfile) -> StaffProfile:
        raw_validated_data = self.validated_data
        validated_data = (
            raw_validated_data if isinstance(raw_validated_data, dict) else {}
        )

        global_role = validated_data.pop("global_role", None)
        is_active = validated_data.pop("is_active", None)
        branch_roles = validated_data.pop("branch_roles", None)

        if global_role is not None:
            staff.global_role = global_role

        if is_active is not None:
            staff.is_active = is_active

        staff.save()

        if branch_roles is not None:
            BranchRoleAssignment.objects.filter(staff_profile=staff).delete()

            for role_data in branch_roles:
                branch_id = role_data["branch_id"]
                role = role_data["role"]
                role_is_active = role_data.get("is_active", True)

                branch = Branch.objects.get(id=branch_id)

                BranchRoleAssignment.objects.create(
                    staff_profile=staff,
                    branch=branch,
                    role=role,
                    is_active=role_is_active,
                )

        staff.refresh_from_db()
        return staff
