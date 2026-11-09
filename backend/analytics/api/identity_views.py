"""Session-authenticated owner endpoints; DRF enforces CSRF for every mutation."""
from django.db import OperationalError
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from .. import identity
from .permissions import CanManageIdentities


def action(call):
    try:
        return Response(call())
    except identity.IdentityError as exc:
        return Response({'error': {'message': str(exc)}}, status=409)
    except OperationalError:
        return Response({'error': {'message': 'Another update is in progress. Refresh and retry.'}}, status=409)


@api_view(['GET'])
@permission_classes([CanManageIdentities])
def registry(request):
    return action(identity.registry)


@api_view(['POST'])
@permission_classes([CanManageIdentities])
def preview(request):
    return action(lambda: identity.preview(request.user, request.data))


@api_view(['POST'])
@permission_classes([CanManageIdentities])
def confirm(request):
    return action(lambda: identity.confirm(request.user, request.data.get('token') if isinstance(request.data, dict) else None))
