"""Select one main transcript for display without changing speech evidence."""
import re


_CUE = re.compile(r'^\s*((?:\d+:)?\d{2}:\d{2}\.\d{3})\s+-->\s+((?:\d+:)?\d{2}:\d{2}\.\d{3})', re.MULTILINE)


def transcript_duration(content):
    """Measure the covered time span, including pauses, from existing VTT cues."""
    def seconds(value):
        parts = value.split(':')
        return sum(float(part) * 60 ** i for i, part in enumerate(reversed(parts)))
    spans = [(seconds(a), seconds(b)) for a, b in _CUE.findall(content or '')]
    spans = [(a, b) for a, b in spans if b > a]
    return max(b for _, b in spans) - min(a for a, _ in spans) if spans else None


def select_transcript(rows, duration, *, available=lambda row: True, content_size=lambda row: 0):
    """Prefer a readable transcript, then the longest verified duration.

    With no timing, use existing content length; ties keep stable source order.
    This never fetches upstream content or removes source rows.
    """
    if not rows:
        return []
    candidates = [row for row in rows if available(row)] or rows
    lengths = [(row, duration(row)) for row in candidates]
    timed = [(row, length) for row, length in lengths if length is not None]
    if timed:
        return [max(timed, key=lambda item: item[1])[0]]
    return [max(candidates, key=content_size)]
