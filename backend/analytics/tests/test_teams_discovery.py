"""Fictional Graph responses only; no network, real people, credentials or private IDs."""
import copy
import datetime as dt
import io
import json
import tempfile
import urllib.parse
from pathlib import Path
from unittest.mock import patch

from django.core.management import call_command, CommandError
from django.test import TestCase

from analytics import metrics
from analytics.attendance.discovery import DiscoveryInProgress, run_discovery
from analytics.attendance.graph_client import GraphError, meeting_path
from analytics.attendance.metrics import AttendanceIndex
from analytics.course_labels import COURSE_DISPLAY_NAMES
from analytics.management.commands.run_sync_scheduler import ProcessLock
from analytics.models import (Course, Enrollment, Learner, LectureSession, SessionAttendance, TeamsMeeting,
    TeamsDiscoveredSeries, TeamsDiscoveryRun, TeamsDiscoveryState, TeamsOccurrence,
    TeamsOccurrenceAttendance, TeamsReportEvidence)

UTC=dt.timezone.utc
NOW=dt.datetime(2026,2,10,12,tzinfo=UTC)
ORGANIZER='11111111-1111-4111-8111-111111111111'
SECOND='22222222-2222-4222-8222-222222222222'
READER='33333333-3333-4333-8333-333333333333'
SUBJECTS={83334:'Level 7 Diploma in Project Management',121642:'Module 2-Procurement Risk and Contracts Management',
          135945:'Andrew Operations and Information Management for Project Managers',154268:'Advanced Project and Logistics Management'}


class FakeDiscoveryGraph:
    def __init__(self, config):
        self.config=config;self.events=[];self.metadata={};self.reports={};self.records={};self.calls=[]
        self.errors={};self.hidden=set();self.metadata_404=set()
        for m in config['meetings']:self.series(m['course_source_id'],m['meeting_id'],m['organizer_id'])

    def series(self,cid,mid,organizer=ORGANIZER):
        url='https://teams.microsoft.com/l/meetup-join/synthetic-'+mid
        self.metadata[(organizer,mid)]={'id':mid,'subject':SUBJECTS[cid],'joinWebUrl':url,
            'participants':{'organizer':{'identity':{'user':{'id':organizer}}}}}
        return url

    def lecture(self,cid,mid=None,rid=None,start='2026-02-05T09:00:00Z',organizer=ORGANIZER,uid=None):
        mid=mid or f'SERIES-{cid}';rid=rid or f'REPORT-{cid}'
        url=self.series(cid,mid,organizer);a=dt.datetime.fromisoformat(start.replace('Z','+00:00'));b=a+dt.timedelta(hours=1)
        event={'id':'event-'+rid,'iCalUId':uid or 'ical-'+rid,'subject':SUBJECTS[cid], 'isCancelled':False,
               'isOnlineMeeting':True,'onlineMeeting':{'joinUrl':url},
               'start':{'dateTime':a.isoformat(),'timeZone':'UTC'},'end':{'dateTime':b.isoformat(),'timeZone':'UTC'}}
        self.events.append(event)
        records=[self.participant(n,a,45 if n<106 else 20) for n in range(101,107)]
        report={'id':rid,'meetingStartDateTime':a.isoformat(),'meetingEndDateTime':b.isoformat(),'totalParticipantCount':len(records)}
        self.reports[(organizer,mid,rid)]=report;self.records[rid]=records
        return event,report

    def participant(self,uid,start,minutes):
        interval={'joinDateTime':start.isoformat(),'leaveDateTime':(start+dt.timedelta(minutes=minutes)).isoformat(),'durationInSeconds':minutes*60}
        return {'id':f'participant-{uid}','emailAddress':f'learner{uid}@example.invalid','identity':{'id':f'identity-{uid}','tenantId':'synthetic-tenant'},
                'attendanceIntervals':[interval],'totalAttendanceInSeconds':minutes*60}

    def authenticate(self):
        if 'auth' in self.errors:raise self.errors['auth']

    def get(self,path):
        self.calls.append(path)
        if path in self.errors:raise self.errors[path]
        for key,meta in self.metadata.items():
            if path==meeting_path(*key) and key not in self.metadata_404:return copy.deepcopy(meta)
        for (oid,mid,rid),report in self.reports.items():
            if path==meeting_path(oid,mid)+'/attendanceReports/'+rid:return copy.deepcopy(report)
        raise GraphError(404,'NotFound')

    def collection(self,path):
        self.calls.append(path)
        if path in self.errors:raise self.errors[path]
        if '/calendarView?' in path:return copy.deepcopy(self.events),1
        if '/onlineMeetings?' in path:
            query=urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)['$filter'][0]
            oid=path.split('/')[2]
            return copy.deepcopy([m for (owner,mid),m in self.metadata.items() if owner==oid and m['joinWebUrl'] in query]),1
        for (oid,mid),meta in self.metadata.items():
            base=meeting_path(oid,mid)+'/attendanceReports'
            if path==base:return copy.deepcopy([r for (owner,meeting,rid),r in self.reports.items() if (owner,meeting)==(oid,mid) and rid not in self.hidden]),1
            for (owner,meeting,rid),report in self.reports.items():
                if (owner,meeting)==(oid,mid) and path==base+'/'+rid+'/attendanceRecords':return copy.deepcopy(self.records[rid]),1
        raise GraphError(404,'NotFound')


class TeamsDiscoveryTests(TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name)
        self.path=self.root/'policy.json';self.lock=self.root/'sync.lock'
        self.config={'version':1,'meetings':[{'course_source_id':cid,'organizer_id':ORGANIZER,
            'meeting_id':f'SERIES-{cid}','approved_subject':SUBJECTS[cid]} for cid in SUBJECTS], 'aliases':[],
            'automation':{'cutover_at':'2026-02-01T00:00:00Z','calendar_readers':[ORGANIZER],'lookback_days':30,'grace_minutes':120}}
        self.path.write_text(json.dumps(self.config));self.graph=FakeDiscoveryGraph(self.config)
        for uid in range(101,108):Learner.objects.create(source_id=uid,name=f'Fictional Learner {uid}',email=f'learner{uid}@example.invalid')
        for cid,title in COURSE_DISPLAY_NAMES.items():
            course=Course.objects.create(source_id=cid,title=title)
            for learner in Learner.objects.all():Enrollment.objects.create(course=course,learner=learner)
            meeting=TeamsMeeting.objects.create(meeting_id=f'matrix:{cid}',course_source_id=cid,subject=title,evidence_source='attendance_matrix')
            session=LectureSession.objects.create(meeting=meeting,session_date=dt.date(2026,1,1),report_count=0,verified_learners=1,delivered=True)
            SessionAttendance.objects.create(session=session,learner_source_id=107,attended=False)

    def sync(self,**kw):return run_discovery(self.path,client=self.graph,now=NOW,lock_path=self.lock,**kw)
    def lecture(self,cid=83334,**kw):return self.graph.lecture(cid,**kw)
    def matrix(self,uid=101,cid=83334):
        return metrics.module_matrix(Enrollment.objects.get(learner__source_id=uid,course__source_id=cid))['attendance']

    def test_all_four_modules_including_previously_completed(self):
        before=list(SessionAttendance.objects.values())
        for cid in SUBJECTS:self.lecture(cid)
        result=self.sync();self.assertEqual(result['status'],'success');self.assertEqual(result['counts']['lectures_added'],4)
        self.assertEqual(TeamsOccurrence.objects.filter(status='verified').count(),4)
        self.assertEqual(before,list(SessionAttendance.objects.values()))
        for row in metrics.charts()['module_progress']:
            self.assertEqual(row['delivered_lectures']['count'],{83334:17,121642:15,135945:17,154268:13}[row['course_id']])
            self.assertEqual(row['attendance']['attended'],5);self.assertEqual(row['attendance']['expected'],6)
            self.assertEqual(row['attendance']['possible_learner_sessions'],14)
        self.assertEqual(metrics.summary()['kpis']['attendance']['attended'],20)

    def test_45_minutes_present_20_minutes_review_missing_not_absent(self):
        self.lecture();self.sync()
        self.assertEqual(self.matrix(101)['sessions'][-1]['status'],'attended')
        self.assertEqual(self.matrix(106)['sessions'][-1]['status'],'review')
        self.assertEqual(self.matrix(107)['sessions'][-1]['status'],'unavailable')
        self.assertEqual(self.matrix(107)['sessions'][0]['status'],'absent')
        self.assertEqual(self.matrix(101)['sessions'][-1]['timing']['duration_seconds'],2700)

    def test_multiple_reports_repeat_joins_and_overlaps_merge_once(self):
        event,report=self.lecture();r=self.graph.records[report['id']][0];a=dt.datetime(2026,2,5,9,tzinfo=UTC)
        r['attendanceIntervals']=[self.graph.participant(101,a,20)['attendanceIntervals'][0],self.graph.participant(101,a+dt.timedelta(minutes=10),40)['attendanceIntervals'][0]]
        r['totalAttendanceInSeconds']=3600
        self.graph.reports[(ORGANIZER,'SERIES-83334','duplicate')]=dict(report,id='duplicate')
        self.graph.records['duplicate']=copy.deepcopy(self.graph.records[report['id']])
        self.sync();self.assertEqual(TeamsOccurrence.objects.count(),1)
        self.assertEqual(self.matrix()['sessions'][-1]['timing']['duration_seconds'],3000)

    def test_same_day_distinct_occurrences_and_midnight(self):
        self.lecture(rid='morning');self.lecture(rid='evening',start='2026-02-05T23:30:00Z')
        self.sync();rows=self.matrix()['sessions'][-2:]
        self.assertEqual(len({r['occurrence_key'] for r in rows}),2)
        self.assertEqual(rows[0]['date'],rows[1]['date'])
        self.assertEqual(rows[1]['timing']['last_leave'].date(),dt.date(2026,2,6))

    def test_overview_and_detail_total_distinct_same_day_occurrences(self):
        self.lecture(rid='morning');self.lecture(rid='evening',start='2026-02-05T23:30:00Z')
        self.sync()
        detail=self.matrix()
        rows=metrics.charts(course_id=83334)['learner_module_matrix']['rows']
        learner=next(r for r in rows if r['learner_id']==101)
        self.assertEqual(detail['verified_attendance_seconds'],5400)
        self.assertEqual(learner['module_metrics'][83334]['verified_attendance_seconds'],5400)
        # Brief timing is real attended time even when it does not qualify as Present.
        brief=next(r for r in rows if r['learner_id']==106)
        self.assertEqual(brief['module_metrics'][83334]['verified_attendance_seconds'],2400)
        missing=next(r for r in rows if r['learner_id']==107)
        self.assertIsNone(missing['module_metrics'][83334]['verified_attendance_seconds'])

    def test_new_meeting_id_discovered_from_exact_join_url(self):
        self.lecture(mid='BRAND-NEW');result=self.sync()
        self.assertEqual(result['counts']['lectures_added'],1)
        self.assertTrue(TeamsDiscoveredSeries.objects.filter(meeting_id='BRAND-NEW').exists())

    def test_multiple_risk_organizers(self):
        self.config['meetings'].append({'course_source_id':121642,'organizer_id':SECOND,'meeting_id':'RISK-SECOND','approved_subject':SUBJECTS[121642]})
        self.path.write_text(json.dumps(self.config));self.graph=FakeDiscoveryGraph(self.config)
        self.lecture(121642);self.lecture(121642,mid='RISK-SECOND',rid='risk-later',start='2026-02-06T09:00:00Z',organizer=SECOND)
        self.sync();self.assertEqual(TeamsOccurrence.objects.filter(course_source_id=121642,status='verified').count(),2)

    def test_known_series_metadata_404_does_not_gate_attendance(self):
        self.lecture();self.sync();self.graph.metadata_404.add((ORGANIZER,'SERIES-83334'))
        self.graph.calls=[];self.sync()
        self.assertIn(meeting_path(ORGANIZER,'SERIES-83334')+'/attendanceReports',self.graph.calls)
        self.assertEqual(TeamsOccurrence.objects.filter(status='verified').count(),1)

    def test_partial_report_never_creates_delivery_or_absence(self):
        event,report=self.lecture();self.graph.records[report['id']].pop()
        result=self.sync();self.assertEqual(result['status'],'partial')
        self.assertFalse(TeamsOccurrence.objects.filter(status='verified').exists())
        self.assertFalse(TeamsOccurrenceAttendance.objects.exists())
        self.assertTrue(TeamsReportEvidence.objects.filter(complete=False).exists())

    def test_late_report_and_longer_correction(self):
        event,report=self.lecture();saved=self.graph.reports.pop((ORGANIZER,'SERIES-83334',report['id']))
        self.sync();self.assertEqual(TeamsOccurrence.objects.get().status,'pending')
        self.graph.reports[(ORGANIZER,'SERIES-83334',report['id'])]=saved
        self.sync();self.assertEqual(self.matrix(106)['sessions'][-1]['status'],'review')
        self.graph.records[report['id']][-1]=self.graph.participant(106,dt.datetime(2026,2,5,9,tzinfo=UTC),45)
        self.sync();self.assertEqual(self.matrix(106)['sessions'][-1]['status'],'attended')
        self.assertEqual(TeamsReportEvidence.objects.count(),2)

    def test_repeat_is_idempotent_and_historical_rows_untouched(self):
        self.lecture();self.sync()
        old=[list(m.objects.order_by('pk').values()) for m in (TeamsOccurrence,TeamsOccurrenceAttendance,TeamsReportEvidence,SessionAttendance,LectureSession,Enrollment,Learner)]
        result=self.sync()
        self.assertEqual(old,[list(m.objects.order_by('pk').values()) for m in (TeamsOccurrence,TeamsOccurrenceAttendance,TeamsReportEvidence,SessionAttendance,LectureSession,Enrollment,Learner)])
        self.assertEqual(result['counts'].get('lectures_added',0),0);self.assertEqual(result['counts']['report_revisions_added'],0)

    def test_cancelled_and_unrelated_meetings_do_not_count(self):
        event,report=self.lecture();event['isCancelled']=True
        other,_=self.lecture(154268);other['subject']='Unrelated rehearsal'
        self.sync();self.assertFalse(TeamsOccurrence.objects.filter(status='verified').exists())

    def test_same_event_on_multiple_calendars_deduplicates(self):
        event,_=self.lecture();self.graph.events.append(dict(event,id='attendee-copy'))
        self.sync();self.assertEqual(TeamsOccurrence.objects.count(),1)

    def test_overlapping_calendar_events_quarantine_report(self):
        self.lecture();self.lecture(rid='different-calendar-id')
        result=self.sync();self.assertEqual(result['status'],'partial')
        self.assertFalse(TeamsOccurrence.objects.filter(status='verified').exists())

    def test_no_new_learner_from_unknown_display_name(self):
        _,r=self.lecture();self.graph.records[r['id']][0]['emailAddress']='unknown@example.invalid'
        self.graph.records[r['id']][0]['identity']['displayName']='Fictional Learner 101'
        self.sync();self.assertEqual(Learner.objects.count(),7)
        self.assertFalse(TeamsOccurrence.objects.filter(status='verified').exists())

    def test_alias_requires_approved_identity_and_tenant(self):
        self.config['aliases']=[{'email':'approved@example.invalid','learner_source_id':101,'identity_id':'identity-101','tenant_id':'synthetic-tenant'}]
        self.path.write_text(json.dumps(self.config));_,r=self.lecture()
        self.graph.records[r['id']][0]['emailAddress']='approved@example.invalid'
        self.sync();self.assertEqual(self.matrix()['sessions'][-1]['status'],'attended')

    def test_report_rollover_uses_saved_report_id(self):
        _,r=self.lecture();self.sync();self.graph.hidden.add(r['id']);self.graph.calls=[]
        self.sync();self.assertIn(meeting_path(ORGANIZER,'SERIES-83334')+'/attendanceReports/'+r['id'],self.graph.calls)

    def test_missing_or_empty_graph_preserves_existing_timing(self):
        _,r=self.lecture();self.sync();before=list(TeamsOccurrenceAttendance.objects.values())
        path=meeting_path(ORGANIZER,'SERIES-83334')+'/attendanceReports/'+r['id']+'/attendanceRecords'
        self.graph.errors[path]=GraphError(503,'TemporaryFailure');self.sync()
        self.assertEqual(before,list(TeamsOccurrenceAttendance.objects.values()))

    def test_authentication_failure_preserves_all_data(self):
        self.graph.errors['auth']=GraphError(401,'InvalidToken')
        with self.assertRaises(GraphError):self.sync()
        self.assertEqual(TeamsDiscoveryRun.objects.get().status,'failed')
        self.assertFalse(TeamsDiscoveryState.objects.exists())

    def test_calendar_permission_failure_and_checkpoint_retry(self):
        original=self.graph.collection
        self.graph.collection=lambda path: (_ for _ in ()).throw(GraphError(403,'AccessDenied')) if '/calendarView?' in path else original(path)
        self.assertEqual(self.sync()['status'],'partial')
        self.assertEqual(TeamsDiscoveryState.objects.get().checkpoints,{})
        self.graph.collection=original;self.lecture();self.assertEqual(self.sync()['status'],'success')

    def test_dry_run_leaves_no_domain_or_audit_rows(self):
        self.lecture();r=self.sync(dry_run=True);self.assertEqual(r['counts']['lectures_added'],1)
        for m in (TeamsDiscoveryRun,TeamsDiscoveryState,TeamsOccurrence,TeamsReportEvidence,TeamsOccurrenceAttendance):self.assertFalse(m.objects.exists())

    def test_single_run_lock_and_release_after_failure(self):
        lock=ProcessLock(self.lock);self.assertTrue(lock.acquire())
        try:
            with self.assertRaises(DiscoveryInProgress):self.sync()
        finally:lock.release()
        self.lecture();self.assertEqual(self.sync()['status'],'success')

    def test_cutover_is_immutable_and_old_reports_never_add_lectures(self):
        self.lecture(start='2026-01-05T09:00:00Z');self.sync();self.assertFalse(TeamsOccurrence.objects.exists())
        self.config['automation']['cutover_at']='2026-02-02T00:00:00Z';self.path.write_text(json.dumps(self.config))
        with self.assertRaisesMessage(ValueError,'immutable'):self.sync()

    def test_command_outputs_only_aggregate_diagnostics(self):
        self.lecture();out=io.StringIO()
        with patch('analytics.management.commands.sync_teams_lectures.run_discovery',side_effect=lambda *a,**kw:self.sync(**kw)):
            call_command('sync_teams_lectures',config=str(self.path),stdout=out)
        text=out.getvalue();self.assertNotIn(ORGANIZER,text);self.assertNotIn('example.invalid',text);self.assertNotIn('SERIES-',text)

    def test_shorter_complete_revision_retains_timing_and_flags_review(self):
        _,r=self.lecture();self.sync()
        self.graph.records[r['id']][0]=self.graph.participant(101,dt.datetime(2026,2,5,9,tzinfo=UTC),20)
        result=self.sync();self.assertEqual(result['status'],'partial')
        row=self.matrix()['sessions'][-1]
        self.assertEqual(row['timing']['duration_seconds'],2700);self.assertEqual(row['status'],'attended')
        self.assertTrue(row['reconciliation']['needs_review'])

    def test_duplicate_delivery_across_series_is_held(self):
        self.lecture();self.lecture(mid='SECOND-ROOM',rid='same-time')
        result=self.sync();self.assertEqual(result['status'],'partial')
        self.assertEqual(TeamsOccurrence.objects.filter(status='verified').count(),1)
        self.assertTrue(TeamsOccurrence.objects.filter(reason='overlapping_delivery_needs_review').exists())

    def test_unapproved_organizer_cannot_create_lecture_by_subject(self):
        self.lecture(mid='UNAPPROVED',organizer=SECOND)
        self.assertEqual(self.sync()['status'],'partial')
        self.assertFalse(TeamsOccurrence.objects.exists())
        self.assertTrue(TeamsDiscoveryState.objects.get().pending_events)

    def test_grace_period_does_not_publish_recent_meeting(self):
        self.lecture(start='2026-02-10T10:00:00Z');self.sync()
        self.assertFalse(TeamsOccurrence.objects.filter(status='verified').exists())

    def test_transaction_failure_rolls_back_all_domain_writes(self):
        self.lecture()
        with patch('analytics.attendance.discovery.reconcile_occurrences',side_effect=RuntimeError('synthetic')):
            with self.assertRaises(RuntimeError):self.sync()
        self.assertFalse(TeamsOccurrence.objects.exists());self.assertFalse(TeamsDiscoveredSeries.objects.exists())
        self.assertEqual(TeamsDiscoveryRun.objects.get().status,'failed')

    def test_organizer_calendar_naive_utc_and_offset_are_not_local_dates(self):
        e,_=self.lecture(start='2026-02-05T23:30:00Z')
        e['start']={'dateTime':'2026-02-06T01:30:00+02:00','timeZone':'FLE Standard Time'}
        e['end']={'dateTime':'2026-02-06T00:30:00','timeZone':'UTC'}
        self.sync();self.assertEqual(self.matrix()['sessions'][-1]['date'],dt.date(2026,2,5))

    def test_cancelled_occurrence_reinstatement_retains_calendar_history(self):
        e,_=self.lecture();e['isCancelled']=True;self.sync()
        self.assertEqual(TeamsOccurrence.objects.get().status,'cancelled')
        e['isCancelled']=False;self.sync()
        occurrence=TeamsOccurrence.objects.get();self.assertEqual(occurrence.status,'verified')
        self.assertTrue(occurrence.provenance['calendar_revisions'][0]['event']['isCancelled'])

    def test_long_outage_restarts_from_durable_checkpoint(self):
        self.sync();self.graph.calls=[]
        run_discovery(self.path,client=self.graph,now=NOW+dt.timedelta(days=90),lock_path=self.lock)
        windows=[urllib.parse.parse_qs(urllib.parse.urlsplit(p).query) for p in self.graph.calls if '/calendarView?' in p]
        self.assertEqual(windows[0]['startDateTime'][0],'2026-02-01T00:00:00+00:00')
        self.assertGreater(len(windows),12)

    def test_query_cost_does_not_grow_per_new_lecture(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        self.lecture();self.sync()
        with CaptureQueriesContext(connection) as first:AttendanceIndex()
        self.lecture(rid='next-day',start='2026-02-06T09:00:00Z');self.sync()
        with CaptureQueriesContext(connection) as second:AttendanceIndex()
        self.assertEqual(len(first),len(second))

    def test_primary_reader_does_not_require_the_attendance_organizers_calendar(self):
        self.config['automation']['calendar_readers']=[READER]
        self.path.write_text(json.dumps(self.config))
        original=self.graph.collection
        def separate_paths(path):
            if '/calendarView?' in path and not path.startswith('/users/'+READER+'/'):
                raise GraphError(404,'MailboxNotEnabledForRESTAPI')
            return original(path)
        self.graph.collection=separate_paths
        for cid in SUBJECTS:self.lecture(cid)
        self.assertEqual(self.sync()['status'],'success')
        self.assertEqual(TeamsOccurrence.objects.filter(status='verified').count(),4)
        self.assertTrue(any('/users/'+READER+'/calendarView?' in p for p in self.graph.calls))
        self.assertFalse(any('/users/'+READER+'/onlineMeetings' in p for p in self.graph.calls))
        self.assertEqual(set(TeamsDiscoveredSeries.objects.values_list('organizer_id',flat=True)),{ORGANIZER})

    def test_no_lectures_leaves_all_dashboard_data_unchanged(self):
        before={cid:metrics.summary(course_id=cid) for cid in SUBJECTS}
        original=list(SessionAttendance.objects.values())
        for _ in range(2):
            self.assertEqual(self.sync()['counts']['lectures_added'],0)
        self.assertEqual(before,{cid:metrics.summary(course_id=cid) for cid in SUBJECTS})
        self.assertEqual(original,list(SessionAttendance.objects.values()))
        self.assertEqual(Learner.objects.count(),7)
        self.assertEqual(Enrollment.objects.count(),28)

    def test_two_modules_update_while_modules_without_lectures_are_unchanged(self):
        quiet={cid:metrics.summary(course_id=cid) for cid in (121642,154268)}
        self.lecture(83334);self.lecture(135945)
        self.assertEqual(self.sync()['counts']['lectures_added'],2)
        self.assertEqual(self.sync()['counts']['lectures_added'],0)
        self.assertEqual(quiet,{cid:metrics.summary(course_id=cid) for cid in quiet})
        self.assertEqual(TeamsOccurrence.objects.filter(status='verified').count(),2)
        self.assertEqual(TeamsOccurrenceAttendance.objects.count(),12)

    def test_recurring_instances_from_primary_reader_are_distinct_and_repeatable(self):
        self.config['automation']['calendar_readers']=[READER]
        self.path.write_text(json.dumps(self.config))
        for day in (2,9):
            event,_=self.lecture(rid=f'week-{day}',start=f'2026-02-{day:02}T09:00:00Z',uid=f'instance-{day}')
            event.update(type='occurrence',seriesMasterId='same-recurring-series')
        self.sync();self.sync()
        self.assertEqual(TeamsOccurrence.objects.filter(status='verified').count(),2)
        self.assertEqual(TeamsOccurrenceAttendance.objects.count(),12)

    def test_retained_approved_join_url_resolves_when_metadata_is_unavailable(self):
        self.config['automation']['calendar_readers']=[READER]
        event,_=self.lecture()
        self.config['meetings'][0]['join_url']=event['onlineMeeting']['joinUrl']
        self.path.write_text(json.dumps(self.config))
        self.graph.metadata_404.add((ORGANIZER,'SERIES-83334'))
        self.assertEqual(self.sync()['status'],'success')
        self.assertEqual(TeamsOccurrence.objects.filter(status='verified').count(),1)
        self.assertIn(meeting_path(ORGANIZER,'SERIES-83334')+'/attendanceReports',self.graph.calls)
        self.assertFalse(any('/users/'+READER+'/onlineMeetings' in p for p in self.graph.calls))
