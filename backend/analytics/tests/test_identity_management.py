"""Synthetic identity lifecycle, security and source-evidence regression tests."""
import copy
import datetime as dt
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import TestCase, Client, SimpleTestCase

from analytics import identity
from analytics.identity_evidence import projected_timing, projected_matrix
from analytics.importer.normalize import normalize
from analytics.importer.service import run_sync
from analytics.importer.sources import SourcePayload
from analytics.models import (Learner, Enrollment, OriginalLearner, IdentityOperation, IdentityState,
    IdentityGroup, IdentitySourceSnapshot, AcademicEvidence, DashboardAccess, LectureTiming, TimingImport,
    QuizAttempt, Course, SessionAttendance, TeamsMeeting, LectureSession)
from analytics.attendance.timing import verified_timing_records
from analytics.attendance.importer import registry_lookup
from .fixtures import build_tables
from .test_master_export import FakeSheets, synthetic_snapshot
from analytics.attendance.master_writer import Publisher
from analytics.attendance.master_snapshot import HEADERS, ExportError


class IdentityLifecycleTests(TestCase):
    def setUp(self):
        self.tables = build_tables()
        self.owner = get_user_model().objects.create_user(username='owner_review')
        self.sync()

    def sync(self):
        run = run_sync('excel', fetcher=lambda: SourcePayload('excel', 'synthetic', copy.deepcopy(self.tables)))
        self.assertEqual(run.status, 'success', run.errors)
        return run

    def act(self, kind, ids, primary=None):
        p = identity.preview(self.owner, {'kind': kind, 'learner_ids': ids, 'primary_id': primary})
        return identity.confirm(self.owner, p['token'])

    def test_original_source_ids_are_default_no_manual_merges(self):
        expected = normalize(self.tables)
        self.assertEqual(set(Learner.objects.values_list('source_id', flat=True)), set(expected.learners))
        self.assertEqual(OriginalLearner.objects.count(), 3)
        self.assertFalse(IdentityGroup.objects.exists())

    def test_two_person_merge_and_unmerge_restore_exact_academic_projection(self):
        before = identity.projection(identity.state().snapshot).fingerprint()
        snapshot = identity.state().snapshot_id
        original_attempts = set(QuizAttempt.objects.values_list('source_learner_id','source_attempt_number','score'))
        preview = identity.preview(self.owner, {'kind': 'merge', 'learner_ids': [101,102], 'primary_id': 101})
        self.assertEqual(Learner.objects.count(), 3)
        self.assertEqual(len(preview['originals']), 2)
        identity.confirm(self.owner, preview['token'])
        self.assertEqual(Learner.objects.count(), 2)
        self.assertEqual(identity.state().snapshot_id, snapshot)
        self.assertEqual(set(QuizAttempt.objects.values_list('source_learner_id','source_attempt_number','score')), original_attempts)
        self.assertEqual(Enrollment.objects.get(learner__source_id=101,course__source_id=9001).progress_pct, 100)
        self.act('unmerge', [101])
        self.assertEqual(identity.projection(identity.state().snapshot).fingerprint(), before)

    def test_nested_merge_undo_restores_parent_group(self):
        self.act('merge', [101,102], 101)
        self.act('merge', [101,103], 103)
        self.assertEqual(Learner.objects.count(), 1)
        self.act('unmerge', [103])
        self.assertEqual(set(Learner.objects.values_list('source_id',flat=True)), {101,103})
        self.assertEqual(IdentityGroup.objects.filter(active=True).count(),1)
        self.act('unmerge', [101])
        self.assertEqual(Learner.objects.count(),3)
        self.act('merge', [101,102,103], 102)
        self.assertEqual(Learner.objects.count(),1)

    def test_archive_restore_all_and_source_sync_never_reactivates(self):
        expected = identity.projection(identity.state().snapshot).fingerprint()
        evidence_count = AcademicEvidence.objects.count()
        self.act('archive', [101,102,103])
        self.assertEqual(Learner.objects.count(),0)
        self.assertEqual(Enrollment.objects.count(),0)
        self.assertEqual(AcademicEvidence.objects.count(), evidence_count)
        self.assertEqual(Course.objects.count(),2)
        self.sync()
        self.assertEqual(Learner.objects.count(),0)
        self.act('restore',[101,102,103])
        self.assertEqual(identity.projection(identity.state().snapshot).fingerprint(),expected)

    def test_merge_survives_future_source_updates_and_archive_group(self):
        self.act('merge',[101,102],101)
        self.sync()
        self.assertEqual(set(Learner.objects.values_list('source_id',flat=True)),{101,103})
        self.act('archive',[101])
        self.sync()
        self.assertEqual(list(Learner.objects.values_list('source_id',flat=True)),[103])
        self.act('restore',[101])
        self.assertEqual(Learner.objects.count(),2)
        self.assertEqual({r[0] for r in registry_lookup()[0]}, {101,102,103})
        self.assertEqual(identity.source_enrollments(), set(normalize(self.tables).enrollments))

    def test_idempotent_confirm_and_stale_or_expired_preview(self):
        p = identity.preview(self.owner, {'kind':'archive','learner_ids':[101]})
        q = identity.preview(self.owner, {'kind':'archive','learner_ids':[102]})
        result = identity.confirm(self.owner,p['token'])
        self.assertEqual(identity.confirm(self.owner,p['token']),result)
        with self.assertRaises(identity.IdentityError): identity.confirm(self.owner,q['token'])
        q = identity.preview(self.owner, {'kind':'archive','learner_ids':[102]})
        IdentityOperation.objects.filter(pk=q['token']).update(created_at=dt.datetime(2000,1,1,tzinfo=dt.timezone.utc))
        with self.assertRaises(identity.IdentityError): identity.confirm(self.owner,q['token'])

    def test_source_change_invalidates_preview_and_atomic_failure(self):
        p = identity.preview(self.owner, {'kind':'archive','learner_ids':[101]})
        self.tables['Al Fanar Students'][1][5] = 'changed source value'
        # An independent owner action invalidates an old snapshot even if selected IDs survive.
        self.act('archive',[103])
        with self.assertRaises(identity.IdentityError): identity.confirm(self.owner,p['token'])
        p = identity.preview(self.owner, {'kind':'archive','learner_ids':[101]})
        rev = identity.state().revision
        with patch('analytics.identity.rebuild', side_effect=RuntimeError('synthetic failure')):
            with self.assertRaises(RuntimeError): identity.confirm(self.owner,p['token'])
        self.assertEqual(identity.state().revision,rev)
        self.assertFalse(OriginalLearner.objects.get(pk=101).archived)

    def test_invalid_primary_duplicate_selection_and_hidden_member_rejected(self):
        for request in ({'kind':'merge','learner_ids':[101,102],'primary_id':999},
                        {'kind':'archive','learner_ids':[101,101]}, {'kind':'archive','learner_ids':['101']}, []):
            with self.assertRaises(identity.IdentityError): identity.preview(self.owner,request)
        self.act('merge',[101,102],101)
        with self.assertRaises(identity.IdentityError): self.act('archive',[102])

    def test_permission_boundaries_and_csrf(self):
        viewer = get_user_model().objects.create_user(username='synthetic-viewer')
        DashboardAccess.objects.create(user=viewer)
        staff = get_user_model().objects.create_user(username='synthetic-staff', is_staff=True)
        admin = get_user_model().objects.create_user(username='synthetic-admin', is_superuser=True)
        granted = get_user_model().objects.create_user(username='synthetic-granted')
        case_variant = get_user_model().objects.create_user(username='OWNER_REVIEW')
        for user in (staff, granted, case_variant):
            DashboardAccess.objects.create(user=user, can_manage_identities=True)
        for user in (None, viewer, staff, admin, granted, case_variant):
            c=Client()
            if user: c.force_login(user)
            for url in ('identities','identities/preview','identities/confirm'):
                response=c.get('/api/'+url) if url=='identities' else c.post('/api/'+url,{},content_type='application/json')
                self.assertIn(response.status_code,(401,403))
        c=Client(enforce_csrf_checks=True);c.force_login(self.owner)
        self.assertEqual(c.post('/api/identities/preview',{'kind':'archive','learner_ids':[101]},content_type='application/json').status_code,403)
        c.get('/api/auth/session');token=c.cookies['csrftoken'].value
        p=c.post('/api/identities/preview',{'kind':'archive','learner_ids':[101]},content_type='application/json',HTTP_X_CSRFTOKEN=token)
        self.assertEqual(p.status_code,200)
        result=c.post('/api/identities/confirm',{'token':p.json()['token']},content_type='application/json',HTTP_X_CSRFTOKEN=token)
        self.assertEqual(result.status_code,200)
        self.assertEqual(c.get('/api/auth/session').json()['user']['can_manage_identities'],True)
        self.assertFalse(DashboardAccess.objects.filter(user=self.owner).exists())

    def test_owner_without_grant_can_complete_all_identity_actions_through_api(self):
        c = Client(enforce_csrf_checks=True)
        c.force_login(self.owner)
        self.assertEqual(c.get('/api/identities').status_code, 200)
        c.get('/api/auth/session')
        token = c.cookies['csrftoken'].value
        for kind, ids, primary, count in (
            ('merge', [101, 102], 101, 2), ('unmerge', [101], None, 3),
            ('archive', [101], None, 2), ('restore', [101], None, 3),
        ):
            with self.subTest(kind=kind):
                preview = c.post('/api/identities/preview',
                    {'kind': kind, 'learner_ids': ids, 'primary_id': primary},
                    content_type='application/json', HTTP_X_CSRFTOKEN=token)
                self.assertEqual(preview.status_code, 200)
                self.assertEqual(c.post('/api/identities/confirm', {'token': preview.json()['token']},
                    content_type='application/json').status_code, 403)
                confirmed = c.post('/api/identities/confirm', {'token': preview.json()['token']},
                    content_type='application/json', HTTP_X_CSRFTOKEN=token)
                self.assertEqual(confirmed.status_code, 200)
                self.assertEqual(Learner.objects.count(), count)
        self.assertEqual(IdentityOperation.objects.filter(actor=self.owner, applied_at__isnull=False).count(), 4)
        self.assertFalse(DashboardAccess.objects.filter(user=self.owner).exists())

    def test_inactive_owner_cannot_use_identity_apis(self):
        self.client.force_login(self.owner)
        self.owner.is_active = False
        self.owner.save(update_fields=['is_active'])
        for url in ('identities', 'identities/preview', 'identities/confirm'):
            response = (self.client.get('/api/' + url) if url == 'identities' else
                self.client.post('/api/' + url, {}, content_type='application/json'))
            self.assertIn(response.status_code, (401, 403))

    def test_different_owner_cannot_confirm_preview(self):
        other=get_user_model().objects.create_user(username='other-owner')
        p=identity.preview(self.owner,{'kind':'archive','learner_ids':[101]})
        with self.assertRaises(identity.IdentityError):identity.confirm(other,p['token'])

    def test_attendance_union_dedup_originals_and_undo(self):
        run=TimingImport.objects.create(fingerprint='a'*64,source_name='synthetic',source_sha256='b'*64,policy_sha256='c'*64,
            registry_sha256='d'*64,calendar_sha256='e'*64,active=True)
        for uid,start,end in ((101,10,12),(102,11,13)):
            a=dt.datetime(2026,1,1,start,tzinfo=dt.timezone.utc);b=a.replace(hour=end)
            LectureTiming.objects.create(import_run=run,course_source_id=9001,learner_source_id=uid,session_date=a.date(),status='verified',
                first_join=a,last_leave=b,duration_microseconds=7_200_000_000,visit_count=1,
                provenance=[{'join':a.isoformat(),'leave':b.isoformat()}])
        self.act('merge',[101,102],101)
        records=verified_timing_records(9001,101)
        self.assertEqual(next(iter(records.values())).duration_microseconds,10_800_000_000)
        self.assertEqual(LectureTiming.objects.count(),2)
        self.act('unmerge',[101])
        self.assertEqual(len(verified_timing_records(9001)),2)
        self.act('archive',[101])
        self.assertEqual(len(verified_timing_records(9001)),1)

    def test_conflicting_matrix_flags_preserve_originals_for_review(self):
        self.act('merge',[101,102],101)
        rows=[dict(id=uid,session_id=1,learner_source_id=uid,attended=flag,presence_seconds=None) for uid,flag in ((101,False),(102,True))]
        result=projected_matrix(rows)[(101,1)]
        self.assertTrue(result['identity_conflict']);self.assertTrue(result['attended'])
        self.assertFalse(rows[0]['attended'])

    def test_teams_registry_keeps_originals_after_merge_archive_and_sync(self):
        from analytics.attendance.discovery import registry
        self.act('merge', [101,102], 101)
        self.act('archive', [101])
        self.sync()
        emails, aliases, pairs = registry({'aliases': []})
        self.assertEqual(emails['learner101@example.invalid'],101)
        self.assertEqual(emails['learner102@example.invalid'],102)
        self.assertIn((102,9001), pairs)
        self.assertEqual(set(Learner.objects.values_list('source_id',flat=True)),{103})

    def test_ambiguous_email_does_not_become_automatic_match_after_owner_merge(self):
        OriginalLearner.objects.filter(pk__in=[101,102]).update(email='shared@example.invalid')
        self.act('merge',[101,102],101)
        _, emails, ambiguous = registry_lookup()
        self.assertNotIn('shared@example.invalid',emails)
        self.assertIn('shared@example.invalid',ambiguous)

    def test_updated_lms_source_invalidates_preview_but_keeps_owner_policy(self):
        p = identity.preview(self.owner,{'kind':'archive','learner_ids':[101]})
        header = self.tables['Al Fanar Students'][0]
        self.tables['Al Fanar Students'][1][header.index('Progress (%)')] = 88
        self.sync()
        with self.assertRaises(identity.IdentityError):identity.confirm(self.owner,p['token'])
        self.assertEqual(Enrollment.objects.get(learner__source_id=101,course__source_id=9001).progress_pct,88)
        self.assertEqual(IdentitySourceSnapshot.objects.count(),2)


class IdentityExportTransitionTests(SimpleTestCase):
    def test_legacy_keys_retained_inactive_manual_review_and_history_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake=FakeSheets();p=Publisher(fake,Path(tmp),sleep=lambda _:None)
            old=synthetic_snapshot();p.publish(old,initialize=True)
            before_lms=copy.deepcopy({k:v for k,v in fake.cells.items() if k.startswith('LMS')})
            new=synthetic_snapshot(2)
            new['identity_version']={'revision':1,'source_sha256':'a'*64,'audit_sha256':'b'*64}
            for tab in ('AT_Learners','AT_Enrollments','AT_Attendance','AT_Summary'):
                new['tables'][tab][0][HEADERS[tab][0]]='new-'+tab
            p.publish(new)
            self.assertEqual({k:v for k,v in fake.cells.items() if k.startswith('LMS')},before_lms)
            self.assertEqual(len(fake.remote['sheets']),23)
            attendance=fake.cells['AT_Attendance']
            self.assertEqual(len(attendance),3)
            self.assertIs(attendance[1][HEADERS['AT_Attendance'].index('Eligible')],False)
            self.assertEqual(p.publish(new)['status'],'unchanged')
            self.assertTrue(list(Path(tmp).rglob(old['generation']+'.json')))
            rollback=synthetic_snapshot(3)
            rollback['identity_version']={'revision':0,'source_sha256':'a'*64,'audit_sha256':'b'*64}
            with self.assertRaises(ExportError):p.publish(rollback)
