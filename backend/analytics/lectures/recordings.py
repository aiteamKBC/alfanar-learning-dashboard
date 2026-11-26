"""Hide every recording whose verified duration is shorter than ten minutes.

Selection affects presentation only. Original media, transcripts, attendance and
speaker evidence remain untouched. Unknown durations are never guessed.
"""
import datetime as dt
import json
import math
from functools import lru_cache
from pathlib import Path

from django.conf import settings

SHORT_RECORDING_SECONDS = 10 * 60


def seconds_between(start, end):
    try:
        a = start if isinstance(start, dt.datetime) else dt.datetime.fromisoformat(str(start).replace('Z', '+00:00'))
        b = end if isinstance(end, dt.datetime) else dt.datetime.fromisoformat(str(end).replace('Z', '+00:00'))
        seconds = (b - a).total_seconds()
        return seconds if math.isfinite(seconds) and seconds > 0 else None
    except (ValueError, TypeError, OverflowError):
        return None


def select_recordings(rows, duration):
    lengths = [(row, duration(row)) for row in rows]
    return [row for row, length in lengths if length is None or length >= SHORT_RECORDING_SECONDS]


def source_key(provider, drive_id, owner, meeting, source_id):
    return json.dumps([provider, drive_id or '', owner, meeting, source_id], separators=(',', ':'))


def build_duration_index(source):
    """Minimal private index from exact source references, never titles/dates."""
    durations = {}
    transcript_durations = {}
    for series in source.get('series', []):
        for kind, target in [('recordings', durations), ('transcripts', transcript_durations)]:
            for asset in series.get(kind, []):
                length = seconds_between(asset.get('createdDateTime'), asset.get('endDateTime'))
                if length is not None:
                    key = source_key(asset.get('provider', 'teams'), asset.get('drive_id', ''),
                                     series['organizer_id'], series['meeting_id'], asset['id'])
                    target[key] = length
    return {'version': 1, 'durations': durations, 'transcript_durations': transcript_durations}


@lru_cache(maxsize=4)
def _read_index(path, mtime, size, section):
    try:
        value = json.loads(Path(path).read_text(encoding='utf8'))
        if value.get('version') != 1 or not isinstance(value.get(section), dict):
            return {}
        return {k: float(v) for k, v in value[section].items()
                if isinstance(v, (float, int)) and not isinstance(v, bool) and math.isfinite(v) and v > 0}
    except (OSError, ValueError, TypeError, AttributeError):
        return {}


def duration_index(kind='recording'):
    path = Path(settings.LECTURE_RECORDING_DURATIONS_FILE)
    try:
        stat = path.stat()
    except OSError:
        return {}
    section = 'transcript_durations' if kind == 'transcript' else 'durations'
    return _read_index(str(path.resolve()), stat.st_mtime_ns, stat.st_size, section)


def stored_duration(asset, index):
    return index.get(source_key(asset.provider, asset.drive_id, asset.organizer_id, asset.meeting_id, asset.source_id))
