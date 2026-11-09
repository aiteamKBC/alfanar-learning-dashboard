"""Read-only projections of original attendance for the current owner identity version."""
import copy
from collections import defaultdict

from .identity import policy, IdentityError
from .importer.exclusions import NON_LEARNER_SOURCE_IDS


def projected_matrix(rows, *, identity_policy=None):
    rows = list(rows)
    if not rows: return {}
    mapping, archived = policy() if identity_policy is None else identity_policy
    grouped = defaultdict(list)
    for raw in rows:
        if raw['learner_source_id'] in archived | set(NON_LEARNER_SOURCE_IDS): continue
        uid = mapping.get(raw['learner_source_id'], raw['learner_source_id'])
        grouped[(uid, raw['session_id'])].append(raw)
    result = {}
    for key, group in grouped.items():
        row = {**group[0], 'learner_source_id': key[0], 'attended': any(r['attended'] for r in group),
               'source_record_ids': sorted(r['id'] for r in group),
               'source_learner_ids': sorted({r['learner_source_id'] for r in group}),
               'identity_conflict': len({r['attended'] for r in group}) > 1}
        if len(group) > 1:
            # Binary evidence has no measured duration. Never add legacy elapsed spans.
            row.update(presence_seconds=None, first_join=None, last_leave=None, interval_count=None)
        result[key] = row
    return result


def projected_timing(rows, *, occurrence=False, identity_policy=None):
    rows = list(rows)
    if not rows: return {}
    from .attendance.importer import merge_intervals
    from .attendance.timing import timestamp, microseconds, digest
    mapping, archived = policy() if identity_policy is None else identity_policy
    grouped = defaultdict(list)
    for raw in rows:
        if raw.learner_source_id in archived | set(NON_LEARNER_SOURCE_IDS): continue
        uid = mapping.get(raw.learner_source_id, raw.learner_source_id)
        key = (uid, raw.occurrence_id) if occurrence else (uid, raw.course_source_id, raw.session_date)
        grouped[key].append(raw)
    result = {}
    for key, group in grouped.items():
        row = copy.copy(group[0])
        row.learner_source_id = key[0]
        row.source_record_ids = sorted(r.pk for r in group)
        row.source_learner_ids = sorted({r.learner_source_id for r in group})
        if len(group) > 1:
            if occurrence:
                intervals = [(timestamp(a), timestamp(b)) for r in group for a, b in r.intervals]
                row.needs_review = any(r.needs_review for r in group)
            else:
                evidence = {digest(p): p for r in group for p in r.provenance}
                row.provenance = [evidence[k] for k in sorted(evidence)]
                intervals = [(timestamp(p['join']), timestamp(p['leave'])) for p in row.provenance]
            if not intervals:
                # A persisted duration without its intervals cannot be safely combined.
                raise IdentityError('Merged timing requires original intervals; restore the evidence first.')
            merged = merge_intervals(intervals)
            row.first_join, row.last_leave = merged[0][0], merged[-1][1]
            row.visit_count = len(merged)
            row.duration_microseconds = sum(microseconds(b-a) for a, b in merged)
            if occurrence: row.intervals = [[a.isoformat(), b.isoformat()] for a, b in merged]
        result[key] = row
    return result
