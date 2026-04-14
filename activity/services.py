from typing import Optional

from activity.models import ActivityEvent
from accounts.models import StaffProfile, Branch


def emit_activity_event(
    *,
    actor: StaffProfile,
    action: str,
    target_type: str,
    target_id: Optional[int] = None,
    branch: Optional[Branch] = None,
    message: str = "",
    metadata: Optional[dict] = None,
) -> ActivityEvent:
    """
    Lightweight helper to create an activity/outbox event.

    This should ONLY be called inside transaction.on_commit(...)
    from write endpoints.
    """

    return ActivityEvent.objects.create(
        actor_staff_profile=actor,
        action=action,
        target_type=target_type,
        target_id=target_id,
        branch=branch,
        message=message,
        metadata_json=metadata or {},
    )
