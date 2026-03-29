from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from accounts.permissions import has_global_access


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def get_current_user(request):
    user = request.user

    return Response(
        {
            "id": user.id,
            "email": user.email,
            "full_name": user.full_name,
            "global_role": user.global_role,
            "can_recalibrate": has_global_access(user),
        }
    )
