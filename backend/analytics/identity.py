"""Owner-approved identities over immutable source snapshots and original evidence.

LMS tables are a rebuildable active projection. Original snapshots, memberships,
operation history and attendance source rows are never deleted by owner actions.

Advanced Research Methods learners are read live from the LMS and are never copied
here. A group may also link LMS learner accounts (`LmsIdentityMembership`, LMS learner
IDs only); their original identity ID is `ARM_LEARNER_ID_OFFSET + LMS learner ID`.
The LMS is only ever read (`lms_live`), never written.
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

from .lms_live import ARM_COURSE_ID, ARM_LEARNER_ID_OFFSET
from .models import (IdentityState, IdentitySourceSnapshot, IdentityGroup, IdentityMembership,
                     LmsIdentityMembership, OriginalLearner, IdentityOperation, Enrollment, LectureTiming,
                     SessionAttendance)


class IdentityError(ValueError):
    pass


def is_arm_id(uid) -> bool:
    return uid > ARM_LEARNER_ID_OFFSET


def lms_links() -> dict[int, int]:
    """LMS learner ID -> displayed learner ID, for owner-approved links in active groups."""
    return dict(LmsIdentityMembership.objects.filter(group__active=True)
                .values_list('lms_learner_id', 'group__primary_source_id'))


def live_arm():
    """This request's live, read-only ARM view, or None if the LMS is not configured or unavailable."""
    from . import lms_live
    if not lms_live.configured():
        return None
    from .arm import Arm
    try:
        return Arm(lms_live.load_snapshot())
    except lms_live.LmsUnavailable:
        return None


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
    # An ARM primary is an LMS account, never part of this source.
    if any(g.primary_source_id not in raw.learners and not is_arm_id(g.primary_source_id) for g in active_groups):
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


def _display_id(original):
    return original.group.primary_source_id if original.group_id and original.group.active else original.source_id


def lms_accounts(arm):
    """Displayed learner ID -> {LMS learner ID: account} for linked and live ARM accounts.

    Links come from this database; live accounts and names come from the read-only LMS
    view `arm` (None when unavailable). Names are only returned, never stored.
    """
    accounts = defaultdict(dict)
    for link in LmsIdentityMembership.objects.filter(group__active=True).select_related('group'):
        accounts[link.group.primary_source_id][link.lms_learner_id] = {
            'group_id': link.group_id, 'name': None, 'in_source': arm is None}
    if arm is not None:
        for learner in arm.learners:
            for account in learner['accounts']:
                found = accounts[learner['learner_id']].setdefault(account['lms_id'], {'group_id': None})
                found.update(name=account['name'], in_source=True)
    return accounts


def members(selected, *, archived=False, arm=None):
    """The original identities behind the selected displayed learners.

    Returns (dashboard originals, {LMS learner ID: account}). A displayed learner's LMS
    accounts are those linked to its group plus those the live LMS view shows in its row.
    """
    from . import lms_live
    visible = defaultdict(list)
    for r in OriginalLearner.objects.select_related('group').all():
        if r.archived == archived:
            visible[_display_id(r)].append(r)
    available = {uid for uid, rows in visible.items() if any(r.in_source for r in rows)}
    accounts = defaultdict(dict) if archived else lms_accounts(arm)
    available |= {uid for uid, found in accounts.items() if any(a['in_source'] for a in found.values())}
    missing = set(selected or ()) - available
    if not selected or missing:
        if arm is None and not archived and any(is_arm_id(uid) for uid in missing) and lms_live.configured():
            raise IdentityError('The LMS is currently unavailable, so Advanced Research Methods learners '
                                'cannot be checked. Try again.')
        raise IdentityError('A selected learner is no longer available. Refresh the selection.')
    chosen = sorted(set(selected))
    return ([r for uid in chosen for r in visible[uid]],
            {lms_id: account for uid in chosen for lms_id, account in accounts[uid].items()})


def _resolve(kind, ids, arm):
    """(dashboard originals, LMS accounts, group) an action applies to."""
    rows, accounts = members(ids, archived=kind == 'restore', arm=arm)
    group = None
    if kind == 'unmerge':
        # Only accounts linked to the group are undone; an account shown in the row by its
        # existing email match is not part of the group and is left as it is.
        accounts = {lms_id: a for lms_id, a in accounts.items() if a['group_id']}
        groups = {r.group_id for r in rows} | {a['group_id'] for a in accounts.values()}
        if len(ids) != 1 or len(groups) != 1 or None in groups:
            raise IdentityError('Choose one active merged group to undo.')
        group = IdentityGroup.objects.get(pk=next(iter(groups)))
    elif kind in ('archive', 'restore'):
        if set(ids) - {_display_id(r) for r in rows}:
            raise IdentityError('Advanced Research Methods learners are read live from the LMS and cannot be '
                                'deleted or restored here.')
        accounts = {}
    return rows, accounts, group


def _source_ids(rows, accounts):
    return sorted({r.source_id for r in rows} | {ARM_LEARNER_ID_OFFSET + lms_id for lms_id in accounts})


def registry():
    s = state()
    originals = [{'source_id': r.source_id, 'name': r.name, 'email': r.email,
        'modules': r.enrollments, 'archived': r.archived, 'in_source': r.in_source,
        'primary_id': _display_id(r), 'group_id': str(r.group_id) if r.group_id and r.group.active else None,
        'source': 'dashboard'}
        for r in OriginalLearner.objects.select_related('group').order_by('source_id')]
    # ARM accounts: names are read live from the LMS for this response only.
    for primary_id, found in lms_accounts(live_arm()).items():
        for lms_id, a in sorted(found.items()):
            originals.append({'source_id': ARM_LEARNER_ID_OFFSET + lms_id, 'name': a['name'] or f'LMS learner {lms_id}',
                'email': '', 'modules': [ARM_COURSE_ID], 'archived': False, 'in_source': a['in_source'],
                'primary_id': primary_id, 'group_id': str(a['group_id']) if a['group_id'] else None, 'source': 'lms'})
    return {'revision': s.revision if s else 0, 'ready': bool(s and s.restored),
        'originals': originals,
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
    rows, accounts, group = _resolve(kind, ids, live_arm() if kind != 'restore' else None)
    originals = {r.source_id for r in rows}
    primary = request.get('primary_id')
    candidates = {r.source_id for r in rows if r.in_source} | {
        ARM_LEARNER_ID_OFFSET + lms_id for lms_id, a in accounts.items() if a['in_source']}
    if kind == 'merge' and (len(ids) < 2 or type(primary) is not int or primary not in candidates):
        raise IdentityError('Choose at least two learners and a primary original identity.')
    mapping, archived = policy()
    if kind == 'merge':
        mapping.update({uid: primary for uid in originals})
    elif kind == 'archive':
        archived |= originals
    elif kind == 'restore':
        archived -= originals
    else:
        for uid in originals: mapping.pop(uid, None)
        for parent in IdentityGroup.objects.filter(pk__in=group.parent_groups):
            mapping.update({uid: parent.primary_source_id for uid in parent.memberships.values_list('original_id', flat=True)})
    proposed = projection(s.snapshot, identity_policy=(mapping, archived))
    lms_ids = sorted(accounts)
    # LMS names are shown in the response but never stored with the operation.
    lms_originals = [{'source_id': ARM_LEARNER_ID_OFFSET + lms_id, 'modules': [ARM_COURSE_ID]} for lms_id in lms_ids]
    details = {'kind': kind, 'originals': [{'source_id': r.source_id, 'name': r.name,
        'modules': r.enrollments} for r in rows] + lms_originals, 'primary_id': primary,
        'resulting_dashboard': proposed.counts(),
        'affected_source_enrollments': sum(len(r.enrollments) for r in rows) + len(lms_ids),
        'retained_timing_records': LectureTiming.objects.filter(import_run__active=True, learner_source_id__in=originals).count(),
        'retained_matrix_records': SessionAttendance.objects.filter(learner_source_id__in=originals).count(),
        'rules': 'Original evidence is retained. Shared LMS progress uses the highest source percentage, never a sum or average. Distinct activity IDs are counted once. Timing uses the union of intervals. Advanced Research Methods data stays live in the LMS: for each lecture one stored LMS attendance record is shown (present, then absent, then the longest duration), never a sum. Delete archives the selected original identities.'}
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
    if lms_ids:
        details['module_counts'].append({'course_id': ARM_COURSE_ID, 'lms_accounts': len(lms_ids)})
    details['matrix_conflicts_for_review'] = sum(r['identity_conflict'] for r in matrix.values())
    op = IdentityOperation.objects.create(actor=actor, kind=kind, revision=s.revision,
        snapshot_sha256=s.snapshot.sha256, preview=details,
        request={'kind': kind, 'learner_ids': ids, 'primary_id': primary, 'source_ids': _source_ids(rows, accounts)})
    names = {ARM_LEARNER_ID_OFFSET + lms_id: accounts[lms_id]['name'] or f'LMS learner {lms_id}' for lms_id in lms_ids}
    shown = [{**o, 'name': names[o['source_id']]} if o['source_id'] in names else o for o in details['originals']]
    return {'token': str(op.pk), **details, 'originals': shown}


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
    rows, accounts, group = _resolve(op.kind, op.request['learner_ids'], live_arm() if op.kind != 'restore' else None)
    source_ids = _source_ids(rows, accounts)
    if 'source_ids' in op.request and source_ids != op.request['source_ids']:
        # e.g. the live LMS roster changed, or could not be read, since the preview.
        raise IdentityError('Preview expired or source data changed. Preview again.')
    ids = [r.source_id for r in rows]
    if op.kind == 'merge':
        old_groups = {str(r.group_id) for r in rows if r.group_id} | {
            str(a['group_id']) for a in accounts.values() if a['group_id']}
        group = IdentityGroup.objects.create(primary_source_id=op.request['primary_id'], parent_groups=sorted(old_groups))
        IdentityMembership.objects.bulk_create([IdentityMembership(group=group, original=r) for r in rows])
        LmsIdentityMembership.objects.bulk_create([LmsIdentityMembership(group=group, lms_learner_id=lms_id)
                                                   for lms_id in sorted(accounts)])
        IdentityGroup.objects.filter(pk__in=old_groups).update(active=False)
        OriginalLearner.objects.filter(pk__in=ids).update(group=group)
    elif op.kind == 'unmerge':
        # Links are kept as history and stop applying with their group; parent links apply again.
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
    op.result = {'operation_id': str(op.pk), 'kind': op.kind, 'source_ids': source_ids, 'revision': op.revision + 1,
                 'learners': projection(s.snapshot).counts()['learners']}
    op.save(update_fields=['applied_at', 'result'])
    return op.result
