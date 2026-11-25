"""Request-scoped LMS artifact references served through the existing media proxy."""
import hashlib
from types import SimpleNamespace
from urllib.parse import quote
from uuid import UUID

from rest_framework.response import Response

from ..attendance.graph_client import GraphClient, GraphError, meeting_path
from .media import resource_response
from .sync import parse_metadata


def resource_id(source_id):
    # Stable integer below JS's exact-integer limit; scoped by the lecture UUID.
    return int(hashlib.sha256(str(source_id).encode()).hexdigest()[:12], 16)


def valid_reference(row):
    try:
        UUID(row['organizer_id'])
        return (row['artifact_type'] in {'recording', 'transcript'} and
                all(isinstance(row.get(k), str) and 0 < len(row[k]) <= 1024
                    for k in ('id', 'graph_artifact_id', 'meeting_id')))
    except (ValueError, TypeError, KeyError, AttributeError):
        return False


def response(request, lecture_id, row):
    """No browser-provided URLs/IDs select upstream media. Use this LMS row only."""
    if not valid_reference(row):
        return Response({'error': {'message': 'This source file is not currently available.'}}, status=404)
    asset = SimpleNamespace(pk=resource_id(row['id']), lecture_id=lecture_id,
        kind=row['artifact_type'], provider='teams', source_id=row['graph_artifact_id'],
        meeting_id=row['meeting_id'], organizer_id=row['organizer_id'], readable=True)
    if asset.kind == 'transcript':
        try:
            path = meeting_path(asset.organizer_id, asset.meeting_id)
            raw = GraphClient.from_environment().get(path + '/transcripts/' + quote(asset.source_id, safe='') + '/metadataContent', text=True)
            _, content = parse_metadata(raw)
            asset.text_content, asset.vtt_content = content['text'], content['vtt']
        except (GraphError, OSError, ValueError):
            return Response({'error': {'message': 'The transcript could not be opened. Try again later.'}}, status=502)
    return resource_response(request, asset)
