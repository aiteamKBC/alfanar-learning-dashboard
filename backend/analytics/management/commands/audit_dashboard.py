"""Independently reconcile raw LMS rows and attendance cells against all dashboard views."""
import datetime as dt
import hashlib
import json
import os
import re
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean, median

import openpyxl
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from analytics import metrics
from analytics.importer.exclusions import NON_LEARNER_SOURCE_IDS
from analytics.importer.identities import MERGED_LEARNER_IDS
from analytics.importer.service import fetch_source
from analytics.models import AcademicEvidence, Enrollment, Learner, LearnerIdentity, LessonRecord, LectureTiming, QuizAttempt, SessionAttendance, Submission, TeamsOccurrence


class Command(BaseCommand):
    help = 'Read raw sources independently and fail on any dashboard reconciliation discrepancy (no source writes).'

    def add_arguments(self, parser):
        parser.add_argument('--source', choices=['google','excel'], default='google')
        parser.add_argument('--snapshot', help='Previously captured read-only LMS JSON snapshot, for reproducibility')
        parser.add_argument('--discovery-config', default=os.environ.get('TEAMS_DISCOVERY_CONFIG'), help='Private identity policy for independently auditing new Graph occurrences')
        parser.add_argument('--output', default=str(settings.PROJECT_ROOT/'data'/'reconciliation.json'))

    def handle(self, *args, **options):
        tables = json.loads(Path(options['snapshot']).read_text(encoding='utf-8')) if options['snapshot'] else fetch_source(options['source']).tables
        checks, failures = [], []

        def check(label, actual, expected):
            checks.append(label)
            if actual != expected:
                # Natural keys can be tuples; retain readable diagnostics in a valid JSON report.
                failures.append({'check': label,
                                 'actual': repr(actual)[:3000] if isinstance(actual,(dict,set)) else actual,
                                 'expected': repr(expected)[:3000] if isinstance(expected,(dict,set)) else expected})

        def canon(uid):
            return MERGED_LEARNER_IDS.get(int(uid), int(uid))

        raw_sheets = {}
        rows_by_sheet = {}
        for name, table in tables.items():
            key = name.lower().removeprefix('al fanar ')
            raw_sheets[key] = [dict(zip(table[0], row)) for row in table[1:] if any(v not in (None,'') for v in row)]
            rows_by_sheet[key] = (name, table)
        students = [r for r in raw_sheets['students'] if int(r['User ID']) not in NON_LEARNER_SOURCE_IDS]
        ens = defaultdict(list)
        emails = defaultdict(set)
        for r in students:
            ens[(canon(r['User ID']), int(r['Course ID']))].append(r)
            emails[str(r['Email']).strip().lower()].add(canon(r['User ID']))
        email_ids = {email: next(iter(ids)) for email,ids in emails.items() if len(ids)==1}
        # Optional existing, explicitly verified aliases, read without importer code.
        if Path(settings.TEAMS_ALIASES_PATH).is_file():
            import csv
            with open(settings.TEAMS_ALIASES_PATH, encoding='utf-8-sig', newline='') as fh:
                for row in csv.DictReader(fh):
                    email_ids[row['teams_email'].strip().lower()] = canon(row['learner_id'])

        def rows(key):
            return [r for r in raw_sheets[key] if r.get('User ID') is not None
                    and int(r['User ID']) not in NON_LEARNER_SOURCE_IDS
                    and (canon(r['User ID']), int(r['Course ID'])) in ens]

        progress = {pair: max((float(r['Progress (%)']) for r in rs if r['Progress (%)'] not in (None,'')), default=None) for pair,rs in ens.items()}
        catalog = defaultdict(lambda: defaultdict(set))
        lessons = defaultdict(list)
        for r in rows('lesson details'):
            kind = r['Category'].strip().lower()
            k = (canon(r['User ID']), int(r['Course ID']), kind, int(r['Lesson ID']))
            lessons[k].append(r)
            catalog[k[1]][kind].add(k[3])
        results, attempts = defaultdict(list), defaultdict(list)
        for r in rows('quizzes'):
            k = (canon(r['User ID']), int(r['Course ID']), int(r['Quiz ID']))
            results[k].append(r)
            catalog[k[1]]['quiz'].add(k[2])
        for r in rows('quiz attempts'):
            k = (canon(r['User ID']), int(r['Course ID']), int(r['Quiz ID']))
            attempts[k].append(r)
            results.setdefault(k, [])
            catalog[k[1]]['quiz'].add(k[2])
        subs = {int(r['Submission ID']): r for r in rows('submissions')}
        for r in raw_sheets.get('assignments', []) + list(subs.values()):
            catalog[int(r['Course ID'])]['assignment'].add(int(r['Assignment ID']))
        submitted = {sid:r for sid,r in subs.items() if r['Status'].lower()!='draft'}
        submitted_pairs = {(canon(r['User ID']), int(r['Course ID']), int(r['Assignment ID'])) for r in submitted.values()}
        latest = {}
        attempt_n = 0
        for key, rs in attempts.items():
            unique = {(int(r['User ID']), int(r['Attempt #'])): r for r in rs}
            attempt_n += len(unique)
            graded = [r for r in unique.values() if r['Progress (%)'] not in (None,'')]
            if graded:
                if len({int(r['User ID']) for r in rs})>1:
                    graded.sort(key=lambda r: (str(r['Created At']), int(r['User ID']), int(r['Attempt #'])))
                else:
                    graded.sort(key=lambda r: int(r['Attempt #']))
                latest[key] = graded[-1]
        active_pairs = {k[:2] for k in lessons} | {k[:2] for k in attempts} | {(canon(r['User ID']), int(r['Course ID'])) for r in subs.values()}
        last_activity = {}
        for sheet, field in [('lesson details','Start Time'),('quiz attempts','Created At'),('submissions','Updated At')]:
            for r in rows(sheet):
                value = r.get(field)
                if value in (None,''):continue
                try:
                    stamp = dt.datetime.fromtimestamp(float(value),tz=dt.timezone.utc)
                except (ValueError,TypeError):
                    try:
                        stamp = dt.datetime.fromisoformat(str(value)).replace(tzinfo=dt.timezone.utc)
                    except ValueError:
                        stamp = dt.datetime.strptime(str(value),'%Y-%m-%d %H:%M:%S').replace(tzinfo=dt.timezone.utc)
                pair=(canon(r['User ID']),int(r['Course ID']))
                last_activity[pair] = max(last_activity.get(pair,stamp),stamp)
        timed = {}
        for key, rs in lessons.items():
            durations = [int(r['Session Seconds'] if r.get('Session Seconds') not in (None,'') else r['Recorded Seconds'])
                         for r in rs if r.get('Time Source') in ('','start_end_difference')
                         and (r.get('Session Seconds') not in (None,'') or r.get('Recorded Seconds') not in (None,''))]
            if durations:
                timed[key] = max(durations)
        pct = lambda a,b: round(100*a/b,1) if b else None
        avg = lambda vs: round(mean(vs),1) if vs else None
        expected = {
            'learners': len({u for u,c in ens}), 'enrollments': len(ens), 'modules': len({c for u,c in ens}),
            'active_learners': len({u for u,c in active_pairs}), 'active_enrollments': len(active_pairs),
            'mean_progress': avg([p for p in progress.values() if p is not None]),
            'learner_quiz_results': len(results), 'quiz_attempts': attempt_n,
            'distinct_quizzes': sum(len(kinds['quiz']) for kinds in catalog.values()),
            'mean_latest_score': avg([float(r['Progress (%)']) for r in latest.values()]),
            'passed_latest': sum(r['Status'].lower()=='passed' for r in latest.values()),
            'graded_quiz_results': len(latest), 'submission_records': len(subs), 'submitted_records': len(submitted),
            'submission_status': dict(Counter(r['Status'].lower() for r in subs.values())),
            'submitted_assignment_pairs': len(submitted_pairs), 'distinct_lesson_completions': len(lessons),
            'raw_lesson_evidence_rows': sum(len(rs) for rs in lessons.values()),
            'recorded_seconds_unverified': sum(timed.values()), 'timed_records': len(timed),
            'lesson_timers_over_8h': sum(s>8*3600 for s in timed.values()),
        }
        k = metrics.summary()['kpis']
        for field, value in [('unique_learners',expected['learners']), ('enrollments',len(ens)), ('modules',expected['modules']),
                             ('active_learners',expected['active_learners']),('mean_progress',expected['mean_progress']),
                             ('quiz_completion',len(results)),('mean_latest_score',expected['mean_latest_score']),
                             ('pass_rate',expected['passed_latest']),('submissions',len(submitted)),
                             ('materials_completed',len(lessons)),('activity_coverage',len(active_pairs)),
                             ('assignment_coverage',len(submitted_pairs)),('recorded_time',sum(timed.values()))]:
            check(f'overview.{field}', k[field]['value'], value)
        check('overview.submission_status', k['submissions']['by_status'], expected['submission_status'])
        check('overview.total_submissions', k['submissions']['total_records'], len(subs))
        check('overview.pass_denominator', k['pass_rate']['denominator'], len(latest))
        for field,kinds in [('quiz_completion',['quiz']), ('materials_completed',['video','pdf','reading','audio']),('assignment_coverage',['assignment'])]:
            check(f'overview.{field}.denominator', k[field]['denominator'], sum(sum(len(catalog[c][kind]) for kind in kinds) for u,c in ens))
        check('quiz_attempts.preserved', QuizAttempt.objects.count(), attempt_n)
        check('enrollment.every_progress_value',
              {(e.learner.source_id,e.course.source_id):e.progress_pct for e in Enrollment.objects.select_related('learner','course')}, progress)
        expected_subs={sid:(canon(r['User ID']),int(r['Course ID']),int(r['Assignment ID']),r['Status'].lower(),
                           float(r['Grade']) if r.get('Grade') not in (None,'') else None) for sid,r in subs.items()}
        actual_subs={s.source_id:(s.enrollment.learner.source_id,s.enrollment.course.source_id,s.activity.source_id,s.status,s.grade)
                     for s in Submission.objects.select_related('enrollment__learner','enrollment__course','activity')}
        check('submissions.every_id_grade_and_status',actual_subs,expected_subs)
        expected_attempts={(canon(r['User ID']),int(r['Course ID']),int(r['Quiz ID']),int(r['User ID']),int(r['Attempt #']),
                           float(r['Progress (%)']) if r['Progress (%)'] not in (None,'') else None,r['Status'].lower())
                           for rs in attempts.values() for r in rs}
        actual_attempts={(a.enrollment.learner.source_id,a.enrollment.course.source_id,a.activity.source_id,
                         a.source_learner_id,a.source_attempt_number,a.score,a.status)
                         for a in QuizAttempt.objects.select_related('enrollment__learner','enrollment__course','activity')}
        check('quiz_attempts.every_source_score_status_and_identity',actual_attempts==expected_attempts,True)
        actual_lessons={(r.enrollment.learner.source_id,r.enrollment.course.source_id,r.activity.kind,r.activity.source_id):r.recorded_seconds
                        for r in LessonRecord.objects.select_related('enrollment__learner','enrollment__course','activity')}
        check('lessons.every_completion_and_representative_duration',actual_lessons,{key:timed.get(key) for key in lessons})
        # Verify every retained source row, including overlapping records, against the evidence table.
        source_evidence = {}
        for key in ('students','lesson details','quizzes','quiz attempts','submissions'):
            name, table = rows_by_sheet[key]
            for number, row in enumerate(table[1:],2):
                r = dict(zip(table[0],row))
                if r.get('User ID') is None or int(r['User ID']) in NON_LEARNER_SOURCE_IDS:
                    continue
                if (canon(r['User ID']),int(r['Course ID'])) in ens:
                    source_evidence[(name,number)] = json.loads(json.dumps(r,default=str))
        stored_evidence = {(r.sheet,r.row_number):r.payload for r in AcademicEvidence.objects.all()}
        check('academic_evidence.all_source_values_preserved', stored_evidence==source_evidence, True)
        check('identities.excluded_absent', LearnerIdentity.objects.filter(source_id__in=NON_LEARNER_SOURCE_IDS).exists(),False)
        check('learners.exclusions_and_aliases_absent', Learner.objects.filter(source_id__in=set(NON_LEARNER_SOURCE_IDS)|set(MERGED_LEARNER_IDS)).exists(),False)
        check('attendance.exclusions_and_aliases_absent', SessionAttendance.objects.filter(learner_source_id__in=set(NON_LEARNER_SOURCE_IDS)|set(MERGED_LEARNER_IDS)).exists(),False)

        # Separate openpyxl traversal: never call the attendance importer or its date/merge functions.
        source_spec = {
            154268: ('Advanced Project and Logistics Management.xlsx','Advanced Project'),
            135945: ('Andrew Operations and Information Management for Project Managers.xlsx','PCP Alfnar'),
            83334: ('PCP Al-Fanar 2025.xlsx','PCP Al-Fanar 2025'),
            121642: ('Risk Management.xlsx','Risk Management'),
        }
        cells, dates, hashes = {}, {}, {}
        for cid,(filename,sheet) in source_spec.items():
            path = Path(settings.ATTENDANCE_DIRECTORY)/filename
            hashes[filename] = hashlib.sha256(path.read_bytes()).hexdigest()
            wb = openpyxl.load_workbook(path,read_only=True,data_only=True)
            data = {s.title.strip():list(s.values) for s in wb}
            wb.close()
            rs = data[sheet]
            cols = {}
            for col,h in enumerate(rs[0]):
                if str(rs[1][col]).lower()!='attendance':continue
                companion = str(rs[0][col+1]) if col+1<len(rs[0]) else ''
                if '(act)' in companion and re.match(r'\d+/\d+/\d+',companion):
                    d,m,y = map(int,re.match(r'(\d+)/(\d+)/(\d+)',companion).groups())
                elif isinstance(h,dt.datetime): y,m,d=h.year,h.day,h.month
                elif isinstance(h,str) and re.fullmatch(r'\d+/\d+/\d+',h): d,m,y=map(int,h.split('/'))
                else:continue
                cols[col]=dt.date(y,m,d)
            dates[cid]=sorted(set(cols.values()))
            for row in rs[2:]:
                u = email_ids.get(str(row[1] or '').strip().lower())
                if u is None:continue
                for col,day in cols.items():
                    if row[col] not in (None,''):
                        key=(u,cid,day)
                        cells[key]=max(cells.get(key,0),int(row[col]))
            for sheet_name,rs in data.items():
                if 'unregistered' not in sheet_name.lower():continue
                hdr=list(rs[0])
                for row in rs[1:]:
                    u=email_ids.get(str(row[hdr.index('AttendanceEmail')] or '').strip().lower())
                    if u is None:continue
                    day=row[hdr.index('Date')]
                    day=day.date() if isinstance(day,dt.datetime) else dt.datetime.strptime(day,'%d/%m/%Y').date()
                    key=(u,cid,day)
                    cells[key]=max(cells.get(key,0),int(row[hdr.index('present')]))
        db_cells={(a.learner_source_id,a.session.meeting.course_source_id,a.session.session_date):int(a.attended)
                  for a in SessionAttendance.objects.select_related('session__meeting')}
        check('attendance.every_source_cell_reconciled',db_cells==cells,True)
        # Independently derive effective observations from retained source cells and
        # an event sweep of verified interval provenance, not AttendanceIndex/decide.
        effective_cells = dict(cells)
        threshold = settings.ATTENDANCE_RECONCILIATION_MIN_MINUTES * 60 * 1_000_000
        from analytics.attendance.timing import AMBIGUOUS_DATES
        for timing in LectureTiming.objects.filter(import_run__active=True, status='verified'):
            uid,cid,day = timing.learner_source_id,timing.course_source_id,timing.session_date
            if (uid,cid) not in ens or day not in dates.get(cid,[]) or (cid,day) in AMBIGUOUS_DATES:
                continue
            events = Counter()
            for interval in timing.provenance:
                a=dt.datetime.fromisoformat(interval['join'].replace('Z','+00:00'))
                b=dt.datetime.fromisoformat(interval['leave'].replace('Z','+00:00'))
                events[a]+=1;events[b]-=1
            depth=0;previous=None;duration=dt.timedelta()
            for stamp,delta in sorted(events.items()):
                if depth and previous is not None:duration+=stamp-previous
                depth+=delta;previous=stamp
            micros=(duration.days*86400+duration.seconds)*1_000_000+duration.microseconds
            check(f'attendance.timing_union.{uid}.{cid}.{day}',timing.duration_microseconds,micros)
            if micros>=threshold:effective_cells[(uid,cid,day)]=1
        # New occurrences use stable occurrence keys, never date-only grouping.
        # Recompute interval unions from the retained raw Graph report revisions.
        new_counts=Counter()
        occurrences=list(TeamsOccurrence.objects.filter(status='verified').select_related('series'))
        if occurrences and not options['discovery_config']:
            raise CommandError('A private discovery config is required to audit new occurrence identities')
        policy=json.loads(Path(options['discovery_config']).read_text(encoding='utf-8')) if occurrences else {}
        aliases={str(r['email']).strip().lower():r for r in policy.get('aliases',[])}
        for occurrence in occurrences:
            cid=occurrence.course_source_id;new_counts[cid]+=1
            sweeps=defaultdict(Counter)
            for evidence in occurrence.series.reports.filter(complete=True):
                raw=evidence.payload['report']
                a=dt.datetime.fromisoformat(raw['meetingStartDateTime'].replace('Z','+00:00'))
                b=dt.datetime.fromisoformat(raw['meetingEndDateTime'].replace('Z','+00:00'))
                if not (a<occurrence.scheduled_end and b>occurrence.scheduled_start):continue
                for record in evidence.payload['records']:
                    email=str(record.get('emailAddress') or '').strip().lower();u=email_ids.get(email)
                    if email in aliases:
                        alias=aliases[email];identity=record.get('identity') or {}
                        u=alias['learner_source_id'] if (identity.get('id'),identity.get('tenantId'))==(alias['identity_id'],alias['tenant_id']) else None
                    if (u,cid) not in ens:continue
                    for interval in record['attendanceIntervals']:
                        a=dt.datetime.fromisoformat(interval['joinDateTime'].replace('Z','+00:00'))
                        b=dt.datetime.fromisoformat(interval['leaveDateTime'].replace('Z','+00:00'))
                        sweeps[u][a]+=1;sweeps[u][b]-=1
            actual={r.learner_source_id:r for r in occurrence.attendance.all()}
            for u,events in sweeps.items():
                depth=0;previous=None;duration=dt.timedelta()
                for stamp,delta in sorted(events.items()):
                    if depth and previous is not None:duration+=stamp-previous
                    depth+=delta;previous=stamp
                micros=(duration.days*86400+duration.seconds)*1_000_000+duration.microseconds
                if u in actual:
                    check(f'graph.occurrence.{occurrence.pk}.{u}.raw_union',actual[u].duration_microseconds,micros)
                else:check(f'graph.occurrence.{occurrence.pk}.{u}.missing_record',True,False)
                if micros>=threshold:effective_cells[(u,cid,f'auto:{occurrence.pk}')]=1
            check(f'graph.occurrence.{occurrence.pk}.participant_set',set(actual),set(sweeps))
        chart=metrics.charts()
        modules=[]
        for row in chart['module_progress']:
            cid=row['course_id']; pairs={p for p in ens if p[1]==cid}
            known={(u,c,d):v for (u,c,d),v in effective_cells.items() if (u,c) in pairs}
            session_count=len(dates[cid])+new_counts[cid]
            possible=len(pairs)*session_count
            n=sum(known.values());den=len(known)
            check(f'module.{cid}.enrollments',row['enrollments'],len(pairs))
            check(f'module.{cid}.active',row['active'],len(pairs & active_pairs))
            check(f'module.{cid}.progress',row['mean_progress'],avg([progress[p] for p in pairs if progress[p] is not None]))
            check(f'module.{cid}.sessions',row['delivered_sessions'],session_count)
            check(f'module.{cid}.delivered_lectures',row['delivered_lectures']['count'],{83334:16,121642:14,135945:16,154268:12}[cid]+new_counts[cid])
            check(f'module.{cid}.lecture_recordings',row['lecture_recordings'],len(catalog[cid]['video']))
            qrow=next(q for q in chart['quiz_by_module'] if q['course_id']==cid)
            qr={key:r for key,r in latest.items() if key[1]==cid}
            qn=sum(key[1]==cid for key in results)
            for field,v in [('known_quizzes',len(catalog[cid]['quiz'])),('attempted_pairs',qn),
                            ('possible_pairs',len(pairs)*len(catalog[cid]['quiz'])),
                            ('completion_pct',pct(qn,len(pairs)*len(catalog[cid]['quiz']))),
                            ('mean_latest_score',avg([float(r['Progress (%)']) for r in qr.values()])),
                            ('pass_rate_pct',pct(sum(r['Status'].lower()=='passed' for r in qr.values()),len(qr)))]:
                check(f'chart.quiz.{cid}.{field}',qrow[field],v)
            crow=next(q for q in chart['activity_completion'] if q['course_id']==cid)
            for kind,cell in crow['by_type'].items():
                done=qn if kind=='quiz' else (sum(key[1]==cid for key in submitted_pairs) if kind=='assignment'
                     else sum(key[1]==cid and key[2]==kind for key in lessons))
                possible_kind=len(pairs)*len(catalog[cid][kind])
                check(f'chart.completion.{cid}.{kind}',cell,{'completed':done,'possible':possible_kind,
                      'known_items':len(catalog[cid][kind]),'pct':pct(done,possible_kind)})
            for field,v in [('attended',n),('expected',den),('pct',pct(n,den)),('coverage_pct',pct(den,possible))]:
                check(f'module.{cid}.attendance.{field}',row['attendance'][field],v)
            modules.append({'course_id':cid,'title':row['course'],'enrollments':len(pairs),'active_in_lms':len(pairs & active_pairs),
                            'mean_progress':row['mean_progress'],'delivered_lectures':row['delivered_lectures']['count'],
                            'dated_attendance_sessions':session_count,'attendance_numerator':n,'attendance_denominator':den,
                            'attendance_pct':pct(n,den),'possible_learner_sessions':possible,'evidence_coverage_pct':pct(den,possible),
                            'measured_enrollments':len({(u,c) for u,c,d in known}),
                            'missing_learner_sessions':possible-den,'dates':[str(d) for d in dates[cid]]})
        eligible={(u,c,d):v for (u,c,d),v in effective_cells.items() if (u,c) in ens}
        check('overview.attendance.numerator',k['attendance']['attended'],sum(eligible.values()))
        check('overview.attendance.denominator',k['attendance']['expected'],len(eligible))
        for row in chart['activity_by_type']:
            kind=row['type']
            count=len(results) if kind=='quiz' else (len(submitted) if kind=='assignment' else sum(key[2]==kind for key in lessons))
            check(f'chart.activity_type.{kind}',row['count'],count)
        buckets=[(0,0),(0.0001,24.9999),(25,49.9999),(50,74.9999),(75,99.9999),(100,100)]
        check('chart.progress_distribution',[r['count'] for r in chart['progress_distribution']['buckets']],
              [sum(p is not None and lo<=p<=hi for p in progress.values()) for lo,hi in buckets])
        check('chart.matrix.progress',{r['learner_id']:r['progress'] for r in chart['learner_module_matrix']['rows']},
              {u:{c:progress[(u,c)] for l,c in ens if l==u} for u in {l for l,c in ens}})
        directory=metrics.learner_directory()
        for row in directory:
            uid=row['learner_id']; pairs={p for p in ens if p[0]==uid}
            learner_latest={key:r for key,r in latest.items() if key[0]==uid}
            check(f'learner.{uid}.progress',row['mean_progress'],avg([progress[p] for p in pairs if progress[p] is not None]))
            check(f'learner.{uid}.latest_score',row['mean_latest_score'],avg([float(r['Progress (%)']) for r in learner_latest.values()]))
            check(f'learner.{uid}.lessons',row['lessons_completed'],sum(k[0]==uid for k in lessons))
            check(f'learner.{uid}.quizzes',row['quizzes_attempted'],sum(k[0]==uid for k in results))
            check(f'learner.{uid}.active_in_lms',row['has_activity'],any(p in active_pairs for p in pairs))
            check(f'learner.{uid}.submitted_assignments',row['assignments_submitted'],sum(key[0]==uid for key in submitted_pairs))
            check(f'learner.{uid}.last_activity',row['last_activity'],max((stamp for pair,stamp in last_activity.items() if pair[0]==uid),default=None))
            known={key:v for key,v in eligible.items() if key[0]==uid}
            check(f'learner.{uid}.attendance',row['attendance']['pct'],pct(sum(known.values()),len(known)))
            detail=metrics.learner_detail(Learner.objects.get(source_id=uid))
            check(f'learner.{uid}.profile_consistent',detail['summary'],{key:row[key] for key in detail['summary']})
        for enrollment in Enrollment.objects.select_related('learner','course'):
            u,c=enrollment.learner.source_id,enrollment.course.source_id
            matrix=metrics.module_matrix(enrollment)
            lesson_keys={key for key in lessons if key[:2]==(u,c)}
            check(f'matrix.{u}.{c}.completions',sum(matrix['footer'][kind]['recorded'] for kind in ['video','pdf','audio','reading']),len(lesson_keys))
            check(f'matrix.{u}.{c}.quiz_results',matrix['footer']['quiz']['recorded'],sum(key[:2]==(u,c) for key in results))
            check(f'matrix.{u}.{c}.submissions',matrix['footer']['assignment']['submitted'],sum(key[:2]==(u,c) for key in submitted_pairs))
            scores=[float(r['Progress (%)']) for key,r in latest.items() if key[:2]==(u,c)]
            check(f'matrix.{u}.{c}.score',matrix['footer']['quiz']['mean_latest_score'],avg(scores))
        for status in ('active','inactive'):
            learners=metrics.learner_directory(engagement=status)
            check(f'filter.{status}.learner_count_consistent',metrics.summary(engagement=status)['kpis']['unique_learners']['value'],len(learners))
        report={'status':'failed' if failures else 'passed','check_count':len(checks),'failures':failures,
                'source':options['source'],'independent_totals':expected,'modules':modules,
                'source_sha256':hashes,'dashboard_summary':k}
        output=Path(options['output']);output.parent.mkdir(parents=True,exist_ok=True)
        output.write_text(json.dumps(report,indent=2,default=str),encoding='utf-8')
        self.stdout.write(f"Independent reconciliation: {report['status']}, {len(checks)} checks, {len(failures)} discrepancies. Report: {output}")
        if failures:
            for f in failures[:15]:self.stdout.write(str(f)[:300])
            raise CommandError('Raw source reconciliation failed')
