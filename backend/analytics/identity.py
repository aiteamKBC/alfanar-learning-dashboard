"""Owner-approved identities over immutable source snapshots and original evidence.

LMS tables are a rebuildable active projection. Original snapshots, memberships,
operation history and attendance source rows are never deleted by owner actions.
"""
from __future__ import annotations

from collections import defaultdict
import json
from django.core.serializers.json import DjangoJSONEncoder
from django.core.exceptions import ValidationError
from django.db.models import F
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from .models import (IdentityState, IdentitySourceSnapshot, IdentityGroup, IdentityMembership,
                     OriginalLearner, IdentityOperation, Enrollment, LectureTiming, SessionAttendance)


class IdentityError(ValueError):
    pass


def state():
    return IdentityState.objects.filter(pk=1).first()


def policy():
    rows = list(OriginalLearner.objects.select_related('group').all())
    mapping = {r.source_id: r.group.primary_source_id for r in rows if r.group_id and r.group.active and r.source_id != r.group.primary_source_id}
    archived = {r.source_id for r in rows if r.archived or not r.in_source}
    return mapping, archived


def source_enrollments():
    current = state()
    if current and current.restored:
        return {(r.source_id, cid) for r in OriginalLearner.objects.all() for cid in r.enrollments}
    return set(Enrollment.objects.values_list('learner__source_id', 'course__source_id'))


def capture_source(tables):
    from .attendance.timing import digest
    from .importer.normalize import normalize, canonical_sheet_name, REQUIRED_SHEETS, OPTIONAL_SHEETS
    tables = {k: v for k, v in tables.items() if canonical_sheet_name(k) in set(REQUIRED_SHEETS) | set(OPTIONAL_SHEETS)}
    tables = json.loads(json.dumps(tables, cls=DjangoJSONEncoder))
    raw = normalize(tables)
    snapshot, _ = IdentitySourceSnapshot.objects.get_or_create(sha256=digest(tables), defaults={'tables': tables})
    active_groups = IdentityGroup.objects.filter(active=True)
    if any(g.primary_source_id not in raw.learners for g in active_groups):
        raise IdentityError('An approved primary identity disappeared from the source. Undo that merge before retrying sync.')
    OriginalLearner.objects.exclude(source_id__in=raw.learners).update(in_source=False)
    for uid, item in raw.learners.items():
        OriginalLearner.objects.update_or_create(source_id=uid, defaults={**item, 'in_source': True,
            'enrollments': sorted(cid for sid, cid in raw.enrollments if sid == uid)})
    return snapshot, raw


def projection(snapshot, *, identity_policy=None):
    from .attendance.timing import digest
    if digest(snapshot.tables) != snapshot.sha256:
        raise IdentityError('Original source snapshot failed its integrity check.')
    from .importer.normalize import normalize
    mapping, archived = policy() if identity_policy is None else identity_policy
    raw = normalize(snapshot.tables)
    ds = normalize(snapshot.tables, merges=mapping, archived=archived, allow_empty=True)
    ds.courses = raw.courses
    ds.activities = raw.activities
    ds.identities = {uid: {**r, 'canonical_source_id': mapping.get(uid, uid)} for uid, r in raw.identities.items()}
    ds.evidence = {key: {**r, 'canonical_source_id': mapping.get(r['source_learner_id'], r['source_learner_id'])} for key, r in raw.evidence.items()}
    return ds


def rebuild(snapshot):
    from .importer.loader import load_dataset
    return load_dataset(projection(snapshot))


def lock_revision(revision):
    # A compare-and-swap write serializes even SQLite, where FOR UPDATE is ignored.
    if IdentityState.objects.filter(pk=1, revision=revision).update(revision=revision + 1) != 1:
        raise IdentityError('Identity data changed. Refresh and preview again.')


def sync_projection(tables):
    """Called inside the sync transaction, after reading external data."""
    s, _ = IdentityState.objects.get_or_create(pk=1)
    lock_revision(s.revision)
    snapshot, raw = capture_source(tables)
    if not s.restored:
        # A deployment must explicitly restore old mappings after its backup/dry run.
        from .models import LearnerIdentity
        legacy_active = LearnerIdentity.objects.exclude(source_id=F('canonical_source_id')).exists()
        if legacy_active:
            raise IdentityError('Run restore_original_identities after backup before LMS sync.')
        s.restored = True
    changed = s.snapshot_id != snapshot.pk
    s.snapshot = snapshot
    s.revision += int(changed)
    s.save(update_fields=['snapshot', 'restored', 'revision'])
    return projection(snapshot), rebuild(snapshot)


def members(selected, *, archived=False):
    originals = list(OriginalLearner.objects.select_related('group').all())
    visible = defaultdict(list)
    for r in originals:
        if r.archived != archived:
            continue
        visible[r.group.primary_source_id if r.group_id and r.group.active else r.source_id].append(r)
    available = {uid for uid, rows in visible.items() if any(r.in_source for r in rows)}
    if not selected or set(selected) - available:
        raise IdentityError('A selected learner is no longer available. Refresh the selection.')
    return [r for uid in sorted(set(selected)) for r in visible[uid]]


def registry():
    s = state()
    return {'revision': s.revision if s else 0, 'ready': bool(s and s.restored),
        'originals': [{'source_id': r.source_id, 'name': r.name, 'email': r.email,
            'modules': r.enrollments, 'archived': r.archived, 'in_source': r.in_source,
            'primary_id': r.group.primary_source_id if r.group_id and r.group.active else r.source_id,
            'group_id': str(r.group_id) if r.group_id and r.group.active else None}
            for r in OriginalLearner.objects.select_related('group').order_by('source_id')],
        'history': [{'id': str(o.pk), 'kind': o.kind, 'at': o.applied_at, 'revision': o.revision,
            'result': o.result, 'primary_id': o.request.get('primary_id'),
            'source_ids': o.result.get('source_ids', [])} for o in IdentityOperation.objects.filter(applied_at__isnull=False).order_by('-applied_at')[:100]]}


@transaction.atomic
def preview(actor, request):
    if not isinstance(request, dict):
        raise IdentityError('Identity request must be an object.')
    s = state()
    if not s or not s.restored or not s.snapshot_id:
        raise IdentityError('Original identity restoration must be completed first.')
    kind = request.get('kind')
    if kind not in ('merge', 'unmerge', 'archive', 'restore'):
        raise IdentityError('Unknown identity action.')
    ids = request.get('learner_ids')
    if not isinstance(ids, list) or not ids or any(type(i) is not int for i in ids) or len(ids) != len(set(ids)):
        raise IdentityError('Select unique learner source IDs.')
    rows = members(ids, archived=kind == 'restore')
    originals = {r.source_id for r in rows}
    primary = request.get('primary_id')
    if kind == 'merge' and (len(ids) < 2 or type(primary) is not int or primary not in {r.source_id for r in rows if r.in_source}):
        raise IdentityError('Choose at least two learners and a primary original identity.')
    if kind == 'unmerge' and (len(ids) != 1 or len({r.group_id for r in rows}) != 1 or not rows[0].group_id):
        raise IdentityError('Choose one active merged group to undo.')
    mapping, archived = policy()
    if kind == 'merge':
        mapping.update({uid: primary for uid in originals})
    elif kind == 'archive':
        archived |= originals
    elif kind == 'restore':
        archived -= originals
    else:
        for uid in originals: mapping.pop(uid, None)
        for parent in IdentityGroup.objects.filter(pk__in=rows[0].group.parent_groups):
            mapping.update({uid: parent.primary_source_id for uid in parent.memberships.values_list('original_id', flat=True)})
    from .importer.normalize import normalize
    proposed = projection(s.snapshot, identity_policy=(mapping, archived))
    details = {'kind': kind, 'originals': [{'source_id': r.source_id, 'name': r.name,
        'modules': r.enrollments} for r in rows], 'primary_id': primary,
        'resulting_dashboard': proposed.counts(),
        'affected_source_enrollments': sum(len(r.enrollments) for r in rows),
        'retained_timing_records': LectureTiming.objects.filter(import_run__active=True, learner_source_id__in=originals).count(),
        'retained_matrix_records': SessionAttendance.objects.filter(learner_source_id__in=originals).count(),
        'rules': 'Original evidence is retained. Shared LMS progress uses the highest source percentage, never a sum or average. Distinct activity IDs are counted once. Timing uses the union of intervals. Delete archives the selected original identities.'}
    from .identity_evidence import projected_timing, projected_matrix
    timing = projected_timing(LectureTiming.objects.filter(import_run__active=True, status='verified'), identity_policy=(mapping, archived))
    matrix = projected_matrix(SessionAttendance.objects.values('id', 'learner_source_id', 'session_id', 'attended',
        'presence_seconds', 'first_join', 'last_leave', 'interval_count'), identity_policy=(mapping, archived))
    details['module_counts'] = [{
        'course_id': cid,
        'enrollments': sum(c == cid for uid, c in proposed.enrollments),
        'lesson_records': sum(c == cid for uid, c, kind, sid in proposed.lesson_records),
        'quiz_results': sum(c == cid for uid, c, qid in proposed.quiz_results),
        'quiz_attempts': sum(c == cid for uid, c, qid, n in proposed.quiz_attempts),
        'submissions': sum(r['cid'] == cid for r in proposed.submissions.values()),
        'timing_pairs': sum(c == cid and (uid, c) in proposed.enrollments for uid, c, day in timing),
        'timing_seconds': sum(r.duration_microseconds // 1_000_000 for (uid, c, day), r in timing.items() if c == cid and (uid, c) in proposed.enrollments),
    } for cid in sorted({cid for r in rows for cid in r.enrollments})]
    details['matrix_conflicts_for_review'] = sum(r['identity_conflict'] for r in matrix.values())
    op = IdentityOperation.objects.create(actor=actor, kind=kind, revision=s.revision,
        snapshot_sha256=s.snapshot.sha256, request={'kind': kind, 'learner_ids': ids, 'primary_id': primary}, preview=details)
    return {'token': str(op.pk), **details}


@transaction.atomic
def confirm(actor, token):
    try:
        op = IdentityOperation.objects.get(pk=token, actor=actor)
    except (IdentityOperation.DoesNotExist, ValueError, ValidationError):
        raise IdentityError('Preview not found.') from None
    if op.applied_at:
        return op.result
    s = state()
    if not s or not s.restored or s.revision != op.revision or s.snapshot.sha256 != op.snapshot_sha256 or op.created_at < timezone.now() - timedelta(minutes=15):
        raise IdentityError('Preview expired or source data changed. Preview again.')
    lock_revision(op.revision)
    rows = members(op.request['learner_ids'], archived=op.kind == 'restore')
    ids = [r.source_id for r in rows]
    old_groups = {str(r.group_id) for r in rows if r.group_id}
    if op.kind == 'merge':
        group = IdentityGroup.objects.create(primary_source_id=op.request['primary_id'], parent_groups=sorted(old_groups))
        IdentityMembership.objects.bulk_create([IdentityMembership(group=group, original=r) for r in rows])
        IdentityGroup.objects.filter(pk__in=old_groups).update(active=False)
        OriginalLearner.objects.filter(pk__in=ids).update(group=group)
    elif op.kind == 'unmerge':
        group = rows[0].group
        group.active = False
        group.save(update_fields=['active'])
        OriginalLearner.objects.filter(pk__in=ids).update(group=None)
        for parent in IdentityGroup.objects.filter(pk__in=group.parent_groups):
            parent.active = True
            parent.save(update_fields=['active'])
            OriginalLearner.objects.filter(pk__in=parent.memberships.values_list('original_id', flat=True)).update(group=parent)
    else:
        OriginalLearner.objects.filter(pk__in=ids).update(archived=op.kind == 'archive')
    rebuild(s.snapshot)
    op.applied_at = timezone.now()
    op.result = {'operation_id': str(op.pk), 'kind': op.kind, 'source_ids': sorted(ids), 'revision': op.revision + 1,
                 'learners': projection(s.snapshot).counts()['learners']}
    op.save(update_fields=['applied_at', 'result'])
    return op.result
