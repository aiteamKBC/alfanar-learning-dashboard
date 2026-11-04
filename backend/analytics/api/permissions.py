from rest_framework.permissions import BasePermission

from ..models import DashboardAccess


def has_dashboard_access(user) -> bool:
    if not (user and user.is_authenticated and user.is_active):
        return False
    if user.is_superuser:
        return True
    try:
        return user.dashboard_access.is_valid()
    except DashboardAccess.DoesNotExist:
        return False


class HasDashboardAccess(BasePermission):
    """Authenticated, active, and holding a valid (unexpired, unrevoked) dashboard grant."""

    message = "Your account does not have dashboard access."

    def has_permission(self, request, view):
        return has_dashboard_access(request.user)
