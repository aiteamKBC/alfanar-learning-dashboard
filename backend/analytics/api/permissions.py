from rest_framework.permissions import BasePermission

from ..models import DashboardAccess

IDENTITY_OWNER_USERNAME = 'owner_review'


def can_manage_identities(user) -> bool:
    """Only the project's existing active owner account; no separate grant."""
    return bool(user and user.is_authenticated and user.is_active
                and user.get_username() == IDENTITY_OWNER_USERNAME)


def has_dashboard_access(user) -> bool:
    if not (user and user.is_authenticated and user.is_active):
        return False
    if can_manage_identities(user) or user.is_superuser:
        return True
    try:
        return user.dashboard_access.is_valid()
    except DashboardAccess.DoesNotExist:
        return False


class HasDashboardAccess(BasePermission):
    """Active project owner, or an existing valid dashboard access policy."""

    message = "Your account does not have dashboard access."

    def has_permission(self, request, view):
        return has_dashboard_access(request.user)


class CanManageIdentities(BasePermission):
    message = 'Only the active owner_review account can manage identities.'

    def has_permission(self, request, view):
        return can_manage_identities(request.user)
