from django.db import models
from django.utils import timezone


class GlobalRole(models.TextChoices):
    NONE = "none", "None"
    BOSS = "boss", "Boss"
    MANAGING_DIRECTOR = "managing_director", "Managing Director"


class BranchRole(models.TextChoices):
    BRANCH_MANAGER = "branch_manager", "Branch Manager"
    CHEF = "chef", "Chef"
    KITCHEN_STAFF = "kitchen_staff", "Kitchen Staff"
    STORE = "store", "Store"


class Branch(models.Model):
    name = models.CharField(max_length=255, unique=True)
    code = models.CharField(max_length=50, unique=True, blank=True)
    location = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class StaffProfile(models.Model):
    """
    Local business-side user record linked to a Keycloak user.
    Authentication still happens in Keycloak.
    This table stores application identity and business metadata.
    """

    keycloak_sub = models.CharField(max_length=255, unique=True)
    email = models.EmailField(blank=True)
    username = models.CharField(max_length=150, blank=True)
    full_name = models.CharField(max_length=255, blank=True)

    global_role = models.CharField(
        max_length=50,
        choices=GlobalRole.choices,
        default=GlobalRole.NONE,
    )

    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        if self.full_name:
            return self.full_name
        if self.email:
            return self.email
        return self.keycloak_sub

    @property
    def is_authenticated(self):
        return True

    @property
    def is_anonymous(self):
        return False


class BranchRoleAssignment(models.Model):
    staff_profile = models.ForeignKey(
        StaffProfile,
        on_delete=models.CASCADE,
        related_name="branch_roles",
    )
    branch = models.ForeignKey(
        Branch,
        on_delete=models.CASCADE,
        related_name="staff_roles",
    )
    role = models.CharField(
        max_length=50,
        choices=BranchRole.choices,
    )
    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["branch__name", "role", "staff_profile__full_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["staff_profile", "branch", "role"],
                name="unique_staff_branch_role",
            )
        ]

    def __str__(self):
        return f"{self.staff_profile} - {self.branch} - {self.role}"
