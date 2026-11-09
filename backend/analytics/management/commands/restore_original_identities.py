"""Explicit, backed-up restoration; no network calls or external source writes."""
import hashlib
import json
import sqlite3
from collections import Counter
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.utils import timezone

from analytics.models import (IdentityState, IdentityEvidenceArchive, IdentityOperation,
    SessionAttendance, LectureTiming, TimingImport, Learner, Enrollment, DashboardAccess, LearnerIdentity, TeamsOccurrenceAttendance)
from analytics.identity import capture_source, rebuild, lock_revision
from analytics.importer.identities import LEGACY_MANUAL_MERGES
from analytics.attendance.timing import prepare_tables, activate_snapshot, digest
from analytics.attendance.importer import read_workbook, run_attendance_import
from analytics.attendance.graph_timing import combine_tables


def serial(value):
    return json.loads(json.dumps(value, cls=DjangoJSONEncoder))


class Command(BaseCommand):
    help = 'Dry-run original identity restoration (default); apply only with a matching reviewed plan and SQLite backup.'

    def add_arguments(self, parser):
        for name in ('lms-json', 'timing-source', 'timing-policy', 'graph-cache', 'matrices', 'report'):
            parser.add_argument('--' + name, required=True)
        parser.add_argument('--apply', action='store_true')
        parser.add_argument('--expected-plan')
        parser.add_argument('--backup')
        parser.add_argument('--owner')

    def handle(self, *args, **o):
        try:
            self.restore(o)
        except Exception as exc:
            if isinstance(exc, CommandError): raise
            raise CommandError(f'Restoration failed ({type(exc).__name__}); transaction rolled back. Inspect private validation inputs.') from exc

    def restore(self, o):
        if o['apply']:
            path = Path(o['backup'] or '')
            if not path.is_file() or not o['expected_plan']:
                raise CommandError('Apply requires a verified backup and the dry-run plan hash.')
            with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True) as backup:
                if backup.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise CommandError('Backup integrity check failed.')
                before_ids = {r[0] for r in backup.execute('SELECT source_id FROM analytics_learner')}
                if before_ids != set(Learner.objects.values_list('source_id', flat=True)):
                    raise CommandError('Backup does not match the current learner projection.')
        tables = json.loads(Path(o['lms_json']).read_text(encoding='utf-8-sig'))
        source = Path(o['timing_source'])
        source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
        cache_path = Path(o['graph_cache'])
        cache_bytes = cache_path.read_bytes()
        graph = json.loads(cache_bytes)
        graph_sha = hashlib.sha256(cache_bytes).hexdigest()
        policy = json.loads(Path(o['timing_policy']).read_text(encoding='utf-8-sig'))
        combined, selection = combine_tables(read_workbook(source), graph, source_sha, graph_sha)
        with transaction.atomic():
            s, _ = IdentityState.objects.get_or_create(pk=1)
            if s.restored:
                raise CommandError('Original identities are already restored; restoration is not reapplied.')
            from django.db.models import F
            active_mappings = dict(LearnerIdentity.objects.exclude(source_id=F('canonical_source_id'))
                                   .values_list('source_id', 'canonical_source_id'))
            if any(LEGACY_MANUAL_MERGES.get(uid) != canonical for uid, canonical in active_mappings.items()):
                raise CommandError('Unrecognized identity relationships exist; explicit evidence review is required.')
            if TeamsOccurrenceAttendance.objects.exists():
                raise CommandError('New occurrence attendance exists; reconcile its original identities before historical restoration.')
            lock_revision(s.revision)
            previous = TimingImport.objects.get(active=True)
            if previous.counts.get('graph_snapshot_sha256') != graph_sha or previous.counts.get('excel_fallback_sha256') != source_sha:
                raise CommandError('Use the exact active Graph cache and Excel source; mismatched evidence is refused.')
            before = {'learners': Learner.objects.count(), 'enrollments': Enrollment.objects.count(),
                      'timing': previous.fingerprint, 'manual_mappings': active_mappings}
            from analytics.attendance.metrics import AttendanceIndex
            prior_index = AttendanceIndex()
            prior_decisions = [{'learner_source_id': key[0], 'session_id': key[1],
                'decision': value, 'evidence': prior_index.decision_evidence[key]}
                for key, value in sorted(prior_index.decisions.items())]
            IdentityEvidenceArchive.objects.get_or_create(key=digest(prior_decisions), defaults={
                'reason': 'pre_restoration_attendance_decisions', 'payload': prior_decisions})
            observations = serial(list(SessionAttendance.objects.order_by('pk').values()))
            IdentityEvidenceArchive.objects.get_or_create(key=digest(observations), defaults={
                'reason': 'pre_restoration_matrix_projection', 'payload': observations})
            snapshot, raw = capture_source(tables)
            s.snapshot, s.restored, s.revision = snapshot, True, s.revision + 1
            s.save()
            rebuild(snapshot)
            run = run_attendance_import(o['matrices'])
            if run.status != 'success':
                raise CommandError('Original-identity matrix validation failed; restoration rolled back.')
            meta, rows, counts = prepare_tables(combined, policy, {'source_name': 'Original source identities: verified Graph + Excel', 'source_sha256': source_sha})
            counts.update(primary_source='microsoft_graph', graph_snapshot_sha256=graph_sha, excel_fallback_sha256=source_sha,
                          identity_version=s.revision, **selection)
            retained = {digest([r['course_source_id'], str(r['session_date']), p['meeting_id'], p['report_id'], p['attendance_record_id'], p['join'], p['leave']]) for r in rows for p in r['provenance']}
            held = []
            for old in previous.records.all():
                missing = [p for p in old.provenance if digest([old.course_source_id, str(old.session_date), p['meeting_id'], p['report_id'], p['attendance_record_id'], p['join'], p['leave']]) not in retained]
                if missing:
                    item = {'old_record_id': old.pk, 'old_learner_id': old.learner_source_id, 'course_id': old.course_source_id,
                            'date': str(old.session_date), 'provenance': missing, 'prior_status': old.status}
                    held.append(item)
                    IdentityEvidenceArchive.objects.get_or_create(key=digest(item), defaults={'reason': 'original_identity_or_enrollment_unverified',
                        'payload': item, 'needs_review': True})
            activate_snapshot(meta, rows, counts)
            after = {'learners': Learner.objects.count(), 'enrollments': Enrollment.objects.count(),
                     'source_ids': sorted(raw.learners), 'source_counts': raw.counts(),
                     'timing': counts, 'held_prior_pairs': len(held), 'held_prior_intervals': sum(len(h['provenance']) for h in held)}
            plan = {'before': before, 'after': after, 'source_sha256': snapshot.sha256,
                    'matrix_sha256': digest(observations), 'timing_fingerprint': meta['fingerprint']}
            plan_sha = digest(plan)
            if o['apply'] and plan_sha != o['expected_plan']:
                raise CommandError('Dry-run plan changed; apply refused. Review a new dry run.')
            if o['owner']:
                grant = DashboardAccess.objects.filter(user__username=o['owner'], user__is_active=True).first()
                if not grant or not grant.is_valid():
                    raise CommandError('Selected owner has no valid dashboard grant.')
                grant.can_manage_identities = True
                grant.save(update_fields=['can_manage_identities'])
            IdentityOperation.objects.create(kind='restore_originals', revision=s.revision-1, snapshot_sha256=snapshot.sha256,
                request={'legacy_mappings': LEGACY_MANUAL_MERGES}, preview=serial(before), result=serial(after), applied_at=timezone.now())
            output = {**plan, 'plan_sha256': plan_sha, 'applied': o['apply'], 'identity_review': held}
            Path(o['report']).write_text(json.dumps(output, cls=DjangoJSONEncoder, indent=2), encoding='utf-8')
            if not o['apply']: transaction.set_rollback(True)
        self.stdout.write(json.dumps({'applied': o['apply'], 'plan_sha256': plan_sha, 'learners': after['learners'],
            'enrollments': after['enrollments'], 'verified_timing': counts['verified'], 'timed_dates': counts['verified_dates'],
            'review_pairs': len(held)}))
