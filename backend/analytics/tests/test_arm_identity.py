"""Cross-source identity merge/unmerge between dashboard learners and live-LMS (ARM) learners.

Synthetic data only: dashboard source tables from `fixtures` (learner 103 relabelled 756) and
an in-memory LMS snapshot. The real LMS is never contacted.
"""
import copy
import datetime as dt
import json
from unittest import mock

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core import serializers
from django.test import TestCase

from analytics import identity, lms_live
from analytics.importer.service import run_sync
from analytics.importer.sources import SourcePayload
from analytics.lms_live import ARM_COURSE_ID, ARM_LEARNER_ID_OFFSET as OFFSET, ArmSnapshot, LmsUnavailable
from analytics.models import (Course, Enrollment, IdentityGroup, IdentityOperation, Learner, LmsIdentityMembership,
                              OriginalLearner, QuizAttempt)

from .fixtures import build_tables

UTC = dt.timezone.utc
OCC1, OCC2 = 'OCC-ONE', 'OCC-TWO'
ARM_756 = OFFSET + 1275          # the regression scenario's ARM-only learner (synthetic)
ARM_SECOND = OFFSET + 1276
LMS_NAME, LMS_EMAIL = 'Synthetic Lms Mutrif', 'mutrif.synthetic@lms.invalid'


def tables_with_756(renumber=None):
    """The standard synthetic workbook, with dashboard learner 103 renumbered 756 (and any other renumbering)."""
    renumber = renumber or {103: 756}
    tables = build_tables()
    for rows in tables.values():
        if not rows or 'User ID' not in rows[0]:
            continue
        col = rows[0].index('User ID')
        for row in rows[1:]:
            if row and row[col] is not None and int(row[col]) in renumber:
                row[col] = type(row[col])(renumber[int(row[col])])
    return tables


def entry(i, lms_id, component, *, quiz=None, attempt=None, grade=None, passed=None, minute=0):
    return {'id': i, 'learner_id': lms_id, 'component_ref': None if quiz else component, 'curriculum_component_ref': None,
            'quiz_ref': quiz, 'kind': 'quiz' if quiz else 'video', 'accepted': True, 'attempt': attempt,
            'grade': grade, 'passed': passed, 'occurred_at': dt.datetime(2026, 1, 10, 12, minute, tzinfo=UTC)}


def attendance(occ, lms_id, status, seconds):
    return {'occurrence_id': occ, 'learner_profile_id': lms_id, 'attendance_status': status,
            'attended_seconds': seconds, 'first_join_at': None, 'last_leave_at': None}


def occurrence(occ, number, day):
    return {'id': occ, 'session_number': number, 'scheduled_start': dt.datetime(2026, 1, day, 10, 0),
            'scheduled_end': dt.datetime(2026, 1, day, 12, 0), 'status': 'completed', 'timezone': 'UTC',
            'actual_end': None, 'attendance_finalized_at': dt.datetime(2026, 1, day, 14, 0)}


def lms_snapshot():
    return ArmSnapshot(
        module={'module_catalogue_id': lms_live.ARM_MODULE_ID, 'title': 'Advanced Research Methods',
                'is_deleted': False, 'deleted_at': None},
        roster=[
            {'id': 272, 'full_name': 'Excluded Account', 'email': 'excluded@lms.invalid', 'email_normalized': 'excluded@lms.invalid'},
            {'id': 1275, 'full_name': LMS_NAME, 'email': LMS_EMAIL, 'email_normalized': LMS_EMAIL},
            {'id': 1276, 'full_name': 'Second Lms Account', 'email': 'second@lms.invalid', 'email_normalized': 'second@lms.invalid'},
            # Existing behaviour: an exact unique email match shows this account in dashboard learner 101's row.
            {'id': 1277, 'full_name': 'Email Matched', 'email': 'learner101@example.invalid', 'email_normalized': 'learner101@example.invalid'},
        ],
        components=[
            {'id': 'C-V1', 'type': 'video', 'title': 'Video 1', 'week_number': 1, 'week_order': 1, 'display_order': 1},
            {'id': 'C-V2', 'type': 'video', 'title': 'Video 2', 'week_number': 1, 'week_order': 1, 'display_order': 2},
            {'id': 'C-Q1', 'type': 'quiz', 'title': 'Quiz 1', 'week_number': 1, 'week_order': 1, 'display_order': 3},
            {'id': 'C-A1', 'type': 'assignment', 'title': 'Assignment 1', 'week_number': 1, 'week_order': 1, 'display_order': 4},
        ],
        quiz_links=[{'component_id': 'C-Q1', 'quiz_id': 'Q-1'}],
        entries=[
            entry(1, 1275, 'C-V1'),                  # both accounts completed Video 1: counted once
            entry(2, 1276, 'C-V1', minute=5),
            entry(3, 1276, 'C-V2', minute=6),
            entry(4, 1275, None, quiz='Q-1', attempt=1, grade=50, passed=False, minute=7),
            entry(5, 1276, None, quiz='Q-1', attempt=2, grade=90, passed=True, minute=8),
            entry(6, 1277, 'C-V1', minute=9),
        ],
        submissions=[{'id': 'S-1', 'learner_id': 1276, 'activity_id': 'C-A1', 'status': 'submitted',
                      'submitted_at': dt.datetime(2026, 1, 11, 9, 0, tzinfo=UTC)}],
        occurrences=[occurrence(OCC1, 1, 5), occurrence(OCC2, 2, 6)],
        attendance=[
            # Conflicting records for one person on one lecture: present wins, shown exactly as stored.
            attendance(OCC1, 1275, 'present', 3000), attendance(OCC1, 1276, 'absent', 0),
            attendance(OCC2, 1275, 'absent', 0), attendance(OCC2, 1276, 'present', 5000),
            attendance(OCC1, 1277, 'present', 4000),
            attendance(OCC1, 272, 'present', 7000),
        ],
    )


class ArmIdentityTests(TestCase):
    databases = {'default', 'lecture_library'}

    def setUp(self):
        self.tables = tables_with_756()
        self.sync()
        self.owner = get_user_model().objects.create_user(username='owner_review')
        self.client.force_login(self.owner)
        self.live, self.lms_down, self.lms_reads = True, False, 0
        self.pristine = lms_snapshot()
        self.served = []
        for target in ('analytics.lms_live', 'analytics.api.views'):
            for name, fake in (('configured', lambda: self.live), ('load_snapshot', self.fake_load)):
                patcher = mock.patch(f'{target}.{name}', side_effect=fake)
                patcher.start()
                self.addCleanup(patcher.stop)

    def fake_load(self):
        self.lms_reads += 1
        if self.lms_down:
            raise LmsUnavailable('The LMS is currently unavailable.')
        snap = lms_snapshot()
        self.served.append(snap)
        return snap

    def sync(self):
        run = run_sync('excel', fetcher=lambda: SourcePayload('excel', 'synthetic', copy.deepcopy(self.tables)))
        self.assertEqual(run.status, 'success', run.errors)

    # --- helpers -----------------------------------------------------------------------

    def get(self, url, status=200):
        response = self.client.get(url)
        self.assertEqual(response.status_code, status, response.content[:400])
        return response.json()

    def post(self, url, body, status=200):
        response = self.client.post(url, body, content_type='application/json')
        self.assertEqual(response.status_code, status, response.content[:400])
        return response.json()

    def act(self, kind, ids, primary=None):
        preview = self.post('/api/identities/preview', {'kind': kind, 'learner_ids': ids, 'primary_id': primary})
        return self.post('/api/identities/confirm', {'token': preview['token']})

    def matrix_ids(self, course=''):
        rows = self.get(f'/api/dashboard/charts?course={course}')['learner_module_matrix']['rows']
        return [r['learner_id'] for r in rows]

    def directory(self, course=''):
        rows = self.get(f'/api/learners?page_size=100&course={course}')['results']
        return {r['learner_id']: r for r in rows}

    def courses(self, learner_id):
        return sorted(c['course_id'] for c in self.get(f'/api/learners/{learner_id}')['courses'])

    def assert_lms_untouched(self):
        for snap in self.served:
            self.assertEqual(snap, self.pristine)

    def assert_no_lms_data_persisted(self):
        dump = ''.join(serializers.serialize('json', m.objects.all())
                       for m in apps.get_app_config('analytics').get_models())
        for value in (LMS_NAME, LMS_EMAIL, 'Second Lms Account', 'second@lms.invalid'):
            self.assertNotIn(value, dump)

    # --- the regression scenario: dashboard 756 + ARM-only 2001275 ---------------------------

    def test_regression_scenario_756_and_2001275(self):
        self.assertIn(756, self.matrix_ids())
        self.assertIn(ARM_756, self.matrix_ids())                                 # 1. both selectable rows
        registry = {r['source_id']: r for r in self.get('/api/identities')['originals']}
        self.assertEqual((registry[756]['source'], registry[ARM_756]['source']), ('dashboard', 'lms'))
        self.assertEqual(registry[ARM_756]['name'], LMS_NAME)                     # 2. both shown in the dialog
        self.assertEqual(registry[ARM_756]['primary_id'], ARM_756)
        for primary in (756, ARM_756):                                            # 3. either may be primary
            preview = self.post('/api/identities/preview',                        # 4. no HTTP 409
                                {'kind': 'merge', 'learner_ids': [756, ARM_756], 'primary_id': primary})
            self.assertEqual({o['source_id'] for o in preview['originals']}, {756, ARM_756})
            self.assertIn(LMS_NAME, [o['name'] for o in preview['originals']])
        before = {(e.learner.source_id, e.course.source_id, e.progress_pct) for e in Enrollment.objects.select_related('learner', 'course')}
        attempts = set(QuizAttempt.objects.values_list('source_learner_id', 'source_attempt_number', 'score'))

        result = self.act('merge', [756, ARM_756], 756)                           # 5. one unified learner
        self.assertEqual(result['source_ids'], [756, ARM_756])
        for _ in range(2):                                                        # 8. reloads stay merged
            ids = self.matrix_ids()
            self.assertEqual(ids.count(756), 1)
            self.assertNotIn(ARM_756, ids)
            self.assertNotIn(ARM_756, self.directory())
            self.assertNotIn(ARM_756, self.directory(ARM_COURSE_ID))
        self.assertEqual(self.courses(756), [9002, ARM_COURSE_ID])
        self.assertEqual(self.directory()[756]['modules'], 2)
        # 6. historical dashboard data unchanged (756 kept its ID, so nothing was remapped).
        self.assertEqual({(e.learner.source_id, e.course.source_id, e.progress_pct) for e in Enrollment.objects.select_related('learner', 'course')}, before)
        self.assertEqual(set(QuizAttempt.objects.values_list('source_learner_id', 'source_attempt_number', 'score')), attempts)
        # 7. ARM figures still come from the live LMS read, under the unified ID.
        reads = self.lms_reads
        matrix = self.get(f'/api/learners/756/courses/{ARM_COURSE_ID}/matrix')
        self.assertGreater(self.lms_reads, reads)
        self.assertEqual([s['status'] for s in matrix['attendance']['sessions']], ['attended', 'absent'])

        self.act('unmerge', [756])                                                # 9. two learners again
        ids = self.matrix_ids()
        self.assertIn(756, ids)
        self.assertIn(ARM_756, ids)
        self.assertEqual(self.courses(756), [9002])
        self.assertEqual(self.courses(ARM_756), [ARM_COURSE_ID])
        self.assertEqual(self.get(f'/api/learners/{ARM_756}')['learner']['name'], LMS_NAME)
        history = self.get('/api/identities')['history']
        self.assertEqual([h['kind'] for h in history], ['unmerge', 'merge'])
        self.assertEqual(history[1]['source_ids'], [756, ARM_756])
        self.assert_lms_untouched()                                               # 10. LMS unchanged
        self.assert_no_lms_data_persisted()

    def test_arm_primary_with_dashboard_member_survives_sync_and_unmerges(self):
        self.act('merge', [756, ARM_756], ARM_756)
        # The dashboard projection carries 756's own records under the chosen primary ID.
        self.assertTrue(Learner.objects.filter(source_id=ARM_756).exists())
        self.assertFalse(Learner.objects.filter(source_id=756).exists())
        self.assertEqual(OriginalLearner.objects.get(pk=756).group.primary_source_id, ARM_756)
        self.sync()                                                               # LMS/source updates keep it
        self.assertEqual(self.courses(ARM_756), [9002, ARM_COURSE_ID])
        self.get('/api/learners/756', status=404)
        ids = self.matrix_ids()
        self.assertEqual((ids.count(ARM_756), ids.count(756)), (1, 0))
        self.assertEqual(sorted(e['course_id'] for e in self.directory()[ARM_756]['enrollments']), [9002, ARM_COURSE_ID])
        self.act('unmerge', [ARM_756])
        self.assertEqual(self.courses(756), [9002])
        self.assertEqual(self.courses(ARM_756), [ARM_COURSE_ID])
        self.assert_no_lms_data_persisted()

    # --- ARM + ARM, overlapping records ------------------------------------------------------

    def test_two_lms_accounts_combine_without_duplicating_records(self):
        before = self.get(f'/api/dashboard/summary?course={ARM_COURSE_ID}')['kpis']
        self.act('merge', [ARM_756, ARM_SECOND], ARM_SECOND)
        self.assertNotIn(ARM_756, self.matrix_ids(ARM_COURSE_ID))
        after = self.get(f'/api/dashboard/summary?course={ARM_COURSE_ID}')['kpis']
        self.assertEqual(after['unique_learners']['value'], before['unique_learners']['value'] - 1)
        row = self.directory(ARM_COURSE_ID)[ARM_SECOND]
        self.assertEqual(row['name'], 'Second Lms Account')                       # primary's own name
        self.assertEqual(row['lessons_completed'], 2)                             # Video 1 counted once
        self.assertEqual(row['quiz_attempts'], 2)                                 # both accounts' attempts kept
        self.assertEqual(row['assignments_submitted'], 1)
        self.assertEqual((row['attendance']['attended'], row['attendance']['expected']), (2, 2))
        matrix = self.get(f'/api/learners/{ARM_SECOND}/courses/{ARM_COURSE_ID}/matrix')
        # One stored record per lecture, never a sum: OCC1 the present 3000s record, OCC2 the present 5000s one.
        self.assertEqual([s['timing']['duration_seconds'] for s in matrix['attendance']['sessions']], [3000, 5000])
        self.assertEqual(matrix['attendance']['verified_attendance_seconds'], 8000)
        lectures = {r['date']: r for r in self.get(f'/api/lectures?course={ARM_COURSE_ID}')['results']}
        occ1 = lectures['2026-01-05']
        self.assertEqual(occ1['present'], 2)                                      # merged learner counted once
        detail = self.get(f"/api/lectures/{occ1['id']}")
        mine = [p for p in detail['participants'] if p['learner_id'] == ARM_SECOND]
        self.assertEqual([(p['attendance'], p['duration_seconds']) for p in mine], [('present', 3000)])
        self.assertNotIn(ARM_756, [p['learner_id'] for p in detail['participants']])
        self.act('unmerge', [ARM_SECOND])
        self.assertEqual(self.directory(ARM_COURSE_ID)[ARM_756]['attendance']['attended'], 1)
        self.assert_lms_untouched()

    def test_multiple_sources_into_one_dashboard_identity_and_email_match_is_preserved(self):
        self.assertIn(101, {x['learner_id'] for x in self.directory(ARM_COURSE_ID).values()})  # 1277 by email
        all_before = self.get('/api/dashboard/summary')['kpis']['unique_learners']['value']
        self.act('merge', [101, ARM_756, ARM_SECOND], 101)
        self.assertEqual(sorted(LmsIdentityMembership.objects.values_list('lms_learner_id', flat=True)), [1275, 1276, 1277])
        self.assertEqual(self.get('/api/dashboard/summary')['kpis']['unique_learners']['value'], all_before - 2)
        arm_rows = self.directory(ARM_COURSE_ID)
        self.assertEqual(set(arm_rows) & {101, ARM_756, ARM_SECOND, OFFSET + 1277}, {101})
        self.assertEqual(self.courses(101), [9001, 9002, ARM_COURSE_ID])
        self.act('unmerge', [101])
        arm_rows = self.directory(ARM_COURSE_ID)
        self.assertTrue({101, ARM_756, ARM_SECOND} <= set(arm_rows))              # 1277 back on its email match
        self.assertEqual(self.get('/api/dashboard/summary')['kpis']['unique_learners']['value'], all_before)

    def test_dashboard_only_merge_unchanged_and_nested_cross_source_undo(self):
        self.act('merge', [101, 102], 102)                                        # dashboard + dashboard
        self.assertEqual(set(Learner.objects.values_list('source_id', flat=True)), {102, 756})
        self.act('merge', [756, ARM_756], 756)
        self.act('merge', [756, ARM_SECOND], ARM_SECOND)                          # nested, ARM primary
        ids = self.matrix_ids()
        self.assertEqual([i for i in ids if i in (756, ARM_756, ARM_SECOND)], [ARM_SECOND])
        self.act('unmerge', [ARM_SECOND])                                         # parent group restored
        ids = self.matrix_ids()
        self.assertIn(756, ids)
        self.assertIn(ARM_SECOND, ids)
        self.assertNotIn(ARM_756, ids)
        self.assertEqual(IdentityGroup.objects.filter(active=True).count(), 2)
        self.assertEqual(Course.objects.count(), 2)                               # source modules untouched

    # --- archive, availability and safety -----------------------------------------------------

    def test_archive_restore_unchanged_and_lms_learners_cannot_be_archived(self):
        body = self.post('/api/identities/preview', {'kind': 'archive', 'learner_ids': [ARM_756]}, status=409)
        self.assertIn('read live from the LMS', body['error']['message'])
        self.act('archive', [102])
        self.assertFalse(Learner.objects.filter(source_id=102).exists())
        self.act('restore', [102])
        self.assertTrue(Learner.objects.filter(source_id=102).exists())

    def test_lms_unavailable_fails_safely_without_writes(self):
        self.lms_down = True
        body = self.post('/api/identities/preview', {'kind': 'merge', 'learner_ids': [756, ARM_756], 'primary_id': 756}, status=409)
        self.assertIn('LMS is currently unavailable', body['error']['message'])
        self.lms_down = False
        preview = self.post('/api/identities/preview', {'kind': 'merge', 'learner_ids': [756, ARM_756], 'primary_id': 756})
        self.lms_down = True                                                      # the LMS drops before confirm
        self.post('/api/identities/confirm', {'token': preview['token']}, status=409)
        self.assertFalse(IdentityGroup.objects.exists())
        self.assertFalse(LmsIdentityMembership.objects.exists())
        self.assertFalse(IdentityOperation.objects.filter(applied_at__isnull=False).exists())

    def test_excluded_and_unknown_lms_ids_are_rejected(self):
        for bad in (OFFSET + 272, OFFSET + 9999):
            self.post('/api/identities/preview', {'kind': 'merge', 'learner_ids': [756, bad], 'primary_id': 756}, status=409)
        self.assertNotIn(OFFSET + 272, self.matrix_ids())

    def test_preview_stores_lms_ids_only(self):
        preview = self.post('/api/identities/preview', {'kind': 'merge', 'learner_ids': [756, ARM_756], 'primary_id': 756})
        op = IdentityOperation.objects.get(pk=preview['token'])
        self.assertEqual(op.request['source_ids'], [756, ARM_756])
        self.assertNotIn(LMS_NAME, json.dumps([op.request, op.preview]))
        self.assertEqual(preview['module_counts'][-1], {'course_id': ARM_COURSE_ID, 'lms_accounts': 1})

    def test_lms_reads_stay_select_only(self):
        self.assertTrue(all(sql.lstrip().lower().startswith('select') for sql in lms_live.QUERIES.values()))
        # Identity actions reach the LMS only through the read-only snapshot loader: no other connection.
        with mock.patch('psycopg.connect', side_effect=AssertionError('unexpected LMS connection')):
            self.act('merge', [756, ARM_756], 756)
            self.get('/api/identities')
            self.act('unmerge', [756])
        self.assertGreater(self.lms_reads, 0)
        self.assert_lms_untouched()
        self.assertEqual(identity.lms_links(), {})


def owner_merge_snapshot(order=1, extra=()):
    """Production shape: LMS 1257 carries merged identity 1055's email, LMS 1275 carries 756's (other case)."""
    snap = lms_snapshot()
    snap.roster = [
        {'id': 1257, 'full_name': 'Synthetic Lms Aldowsari', 'email': 'learner102@example.invalid',
         'email_normalized': 'learner102@example.invalid'},
        {'id': 1275, 'full_name': LMS_NAME, 'email': 'Learner103@Example.invalid', 'email_normalized': 'learner103@example.invalid'},
        *extra,
    ][::order]
    snap.entries = [entry(1, 1257, 'C-V1'), entry(2, 1275, 'C-V1', minute=3), entry(3, 1275, 'C-V2', minute=4)]
    snap.submissions = []
    snap.attendance = [attendance(OCC1, 1257, 'present', 2000), attendance(OCC1, 1275, 'absent', 0),
                       attendance(OCC2, 1275, 'present', 4000)][::order]
    return snap


class ExistingOwnerMergeArmTests(TestCase):
    """An owner merge of 756 + 1055 made before any LMS links existed; no new merge may be created."""
    databases = {'default', 'lecture_library'}
    DUPLICATE = {'id': 1290, 'full_name': 'Same Email Account', 'email': 'learner103@example.invalid',
                 'email_normalized': 'learner103@example.invalid'}

    def setUp(self):
        tables = tables_with_756({103: 756, 102: 1055})
        run_sync('excel', fetcher=lambda: SourcePayload('excel', 'synthetic', copy.deepcopy(tables)))
        self.owner = get_user_model().objects.create_user(username='owner_review')
        self.client.force_login(self.owner)
        # The pre-existing production decision, made while no LMS was configured (so: no LMS links).
        preview = identity.preview(self.owner, {'kind': 'merge', 'learner_ids': [756, 1055], 'primary_id': 756})
        identity.confirm(self.owner, preview['token'])
        self.decisions = (list(IdentityGroup.objects.values_list('pk', 'primary_source_id', 'active')),
                          IdentityOperation.objects.count())
        self.snapshot = owner_merge_snapshot
        for target in ('analytics.lms_live', 'analytics.api.views'):
            for name, fake in (('configured', lambda: True), ('load_snapshot', lambda: self.snapshot())):
                patcher = mock.patch(f'{target}.{name}', side_effect=fake)
                patcher.start()
                self.addCleanup(patcher.stop)

    def get(self, url):
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200, response.content[:400])
        return response.json()

    def arm_rows(self):
        return {r['learner_id']: r for r in self.get(f'/api/learners?course={ARM_COURSE_ID}&page_size=100')['results']}

    def assert_decisions_unchanged(self):
        self.assertEqual((list(IdentityGroup.objects.values_list('pk', 'primary_source_id', 'active')),
                          IdentityOperation.objects.count()), self.decisions)
        self.assertFalse(LmsIdentityMembership.objects.exists())

    def test_both_lms_accounts_show_once_under_the_merged_learner(self):
        self.assertFalse(LmsIdentityMembership.objects.exists())
        for order in (1, -1):                                                     # independent of LMS ordering
            self.snapshot = lambda order=order: owner_merge_snapshot(order)
            rows = self.arm_rows()
            self.assertEqual(set(rows), {756})
            row = rows[756]
            self.assertEqual(row['lessons_completed'], 2)                         # Video 1 once, Video 2
            self.assertEqual((row['attendance']['attended'], row['attendance']['expected']), (2, 2))
            self.assertEqual(row['verified_attendance_seconds'], 6000)            # one stored record per lecture
            self.assertEqual(self.get(f'/api/dashboard/summary?course={ARM_COURSE_ID}')['kpis']['unique_learners']['value'], 1)
            # All modules: dashboard learners 101 and 756 only; no extra ARM-only learner.
            self.assertEqual(self.get('/api/dashboard/summary')['kpis']['unique_learners']['value'], 2)
            matrix_ids = [r['learner_id'] for r in self.get('/api/dashboard/charts')['learner_module_matrix']['rows']]
            self.assertEqual(sorted(matrix_ids), [101, 756])
            courses = sorted(c['course_id'] for c in self.get('/api/learners/756')['courses'])
            self.assertEqual(courses, [9001, 9002, ARM_COURSE_ID])                # enrolments not duplicated
            self.assertEqual(self.client.get(f'/api/learners/{ARM_756}').status_code, 404)
            lectures = {r['date']: r for r in self.get(f'/api/lectures?course={ARM_COURSE_ID}')['results']}
            occ1 = lectures['2026-01-05']
            self.assertEqual((occ1['learners'], occ1['present']), (1, 1))
            participants = self.get(f"/api/lectures/{occ1['id']}")['participants']
            self.assertEqual([(p['learner_id'], p['attendance'], p['duration_seconds']) for p in participants],
                             [(756, 'present', 2000)])
        self.assert_decisions_unchanged()                                         # nothing was persisted

    def test_unmerging_the_dashboard_identities_separates_their_lms_accounts(self):
        preview = identity.preview(self.owner, {'kind': 'unmerge', 'learner_ids': [756]})
        # Only the owner's dashboard group is undone; email-matched LMS accounts were never part of it.
        self.assertEqual(sorted(o['source_id'] for o in preview['originals']), [756, 1055])
        identity.confirm(self.owner, preview['token'])
        rows = self.arm_rows()
        self.assertEqual(set(rows), {756, 1055})
        self.assertEqual(rows[756]['lessons_completed'], 2)                       # LMS 1275 only
        self.assertEqual(rows[1055]['lessons_completed'], 1)                      # LMS 1257 only
        self.assertFalse(LmsIdentityMembership.objects.exists())

    def test_owner_link_overrides_email_matching(self):
        from analytics.arm import Arm, _dashboard_registry
        arm = Arm(owner_merge_snapshot(), _dashboard_registry(), links={1257: 101})
        self.assertEqual({x['learner_id']: x['lms_ids'] for x in arm.learners}, {101: [1257], 756: [1275]})
        # A linked account's email does not also admit another account carrying that email.
        arm = Arm(owner_merge_snapshot(extra=[self.DUPLICATE]), _dashboard_registry(), links={1275: ARM_756})
        self.assertEqual({x['learner_id']: x['lms_ids'] for x in arm.learners},
                         {756: [1257], ARM_756: [1275], OFFSET + 1290: [1290]})

    def test_duplicate_email_accounts_stay_separate(self):
        for order in (1, -1):
            self.snapshot = lambda order=order: owner_merge_snapshot(order, extra=[self.DUPLICATE])
            rows = self.arm_rows()
            # Lowest LMS ID per email joins; the other account with the same email is not combined.
            self.assertEqual(set(rows), {756, OFFSET + 1290})
        self.assert_decisions_unchanged()

    def test_new_owner_merge_with_an_arm_only_learner_still_works(self):
        self.snapshot = lambda: owner_merge_snapshot(extra=[{'id': 1276, 'full_name': 'Second Lms Account',
                                                              'email': 'second@lms.invalid', 'email_normalized': 'second@lms.invalid'}])
        self.assertEqual(set(self.arm_rows()), {756, ARM_SECOND})
        preview = identity.preview(self.owner, {'kind': 'merge', 'learner_ids': [756, ARM_SECOND], 'primary_id': 756})
        identity.confirm(self.owner, preview['token'])
        self.assertEqual(set(self.arm_rows()), {756})
        self.assertEqual(sorted(LmsIdentityMembership.objects.values_list('lms_learner_id', flat=True)), [1257, 1275, 1276])
        identity.confirm(self.owner, identity.preview(self.owner, {'kind': 'unmerge', 'learner_ids': [756]})['token'])
        self.assertEqual(set(self.arm_rows()), {756, ARM_SECOND})                 # back to the existing owner merge
