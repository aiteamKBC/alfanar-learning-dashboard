"""Lecture Library evidence stored under original learner IDs, shown under owner-merged identities.

Synthetic only: 14 dashboard originals in six owner merge groups (pairs, a triple and a nested
merge), with lecture rows imported before the merges under their original IDs.
"""
import copy

from django.contrib.auth import get_user_model
from django.contrib.auth.models import User
from django.test import TestCase

from analytics import identity
from analytics.importer.service import run_sync
from analytics.importer.sources import SourcePayload
from analytics.models import DashboardAccess, Learner
from lecture_library.models import LectureCapture, LectureLearner

from .fixtures import build_tables

COURSE = 9001
EXTRA = range(104, 116)      # 101-103 come from the standard fixture
# (members, primary); the nested group is built in two steps (see `merge_all`).
GROUPS = [([101, 102], 101), ([103, 104], 104), ([105, 106, 107], 105), ([111, 112], 111), ([113, 114], 113)]
NESTED = (([108, 109], 108), ([108, 110], 110))


def email(uid):
    return f'learner{uid}@example.invalid'


def tables():
    t = build_tables()
    students = t['Al Fanar Students']
    header = students[0]
    template = next(r for r in students[1:] if r and r[header.index('User ID')] == 101.0
                    and r[header.index('Course ID')] == float(COURSE))
    for uid in EXTRA:
        row = list(template)
        row[header.index('User ID')] = float(uid)
        row[header.index('Name')] = f'Synthetic Learner {uid}'
        row[header.index('Email')] = email(uid)
        students.append(row)
    for uid in (103,):     # 103 is enrolled in 9002 only in the fixture; add it to the lecture module
        row = list(template)
        row[header.index('User ID')] = 103.0
        row[header.index('Name')] = 'Test Learner Three'
        row[header.index('Email')] = email(103)
        students.append(row)
    return t


class LibraryMergeResolutionTests(TestCase):
    databases = {'default', 'lecture_library'}

    def setUp(self):
        source = tables()
        run = run_sync('excel', fetcher=lambda: SourcePayload('excel', 'synthetic', copy.deepcopy(source)))
        self.assertEqual(run.status, 'success', run.errors)
        self.owner = get_user_model().objects.create_user(username='owner_review')
        viewer = User.objects.create_user('library_viewer')
        DashboardAccess.objects.create(user=viewer)
        self.client.force_login(viewer)
        self.l1 = self.lecture('l1', '2026-10-01')
        self.l2 = self.lecture('l2', '2026-10-02')
        rows = [
            (self.l1, 101, 'absent', 0), (self.l1, 102, 'present', 3600),       # secondary-only presence
            (self.l1, 103, 'present', 2000), (self.l1, 104, 'present', 4000),   # both present: one row, no double count
            (self.l1, 106, 'present', 1500),                                    # only a secondary of a triple
            (self.l1, 109, 'present', 2500),                                    # nested merge member
            (self.l1, 111, 'absent', 0), (self.l1, 112, 'review', 300),         # explicit Absent vs short visit
            (self.l1, 115, 'present', 3000),                                    # not merged
            # The lecture's only Present learner is a merged secondary: it must stay listed after the merge.
            (self.l2, 105, 'absent', 0), (self.l2, 107, 'present', 2400), (self.l2, 101, 'absent', 0),
        ]
        for lecture, uid, status, seconds in rows:
            LectureLearner.objects.create(lecture=lecture, learner_source_id=uid, source_emails=[email(uid)],
                                          attendance=status, duration_seconds=seconds)
        LectureLearner.objects.create(lecture=self.l1, learner_source_id=114, source_emails=[email(114)], attendance='present',
                                      duration_seconds=3000, speaker_verified=True, speaking_seconds=120,
                                      speaking_emails=[email(114)], speaking_match='verified_identity')
        self.stored = self.evidence()

    def lecture(self, key, day):
        return LectureCapture.objects.create(source_key=key, course_source_id=COURSE, session_date=day,
                                             source_label='Teams attendance', source_fingerprint=key)

    def evidence(self):
        return sorted(LectureLearner.objects.values_list(
            'lecture_id', 'learner_source_id', 'attendance', 'duration_seconds', 'speaking_seconds', 'source_emails'))

    def act(self, kind, ids, primary=None):
        preview = identity.preview(self.owner, {'kind': kind, 'learner_ids': ids, 'primary_id': primary})
        return identity.confirm(self.owner, preview['token'])

    def merge_all(self):
        for members, primary in GROUPS + list(NESTED):
            self.act('merge', members, primary)

    def detail(self, lecture):
        response = self.client.get(f'/api/lectures/{lecture.pk}')
        self.assertEqual(response.status_code, 200, response.content[:300])
        body = response.json()
        ids = [p['learner_id'] for p in body['participants']]
        self.assertEqual(len(ids), len(set(ids)))                               # never a duplicate participant
        self.assertEqual(body['present'], sum(p['attendance'] == 'present' for p in body['participants']))
        return body, {p['learner_id']: p for p in body['participants']}

    def listed(self):
        body = self.client.get('/api/lectures').json()
        return {r['id']: r for r in body['results']}

    def test_six_owner_merges_show_secondary_evidence_under_the_primary(self):
        self.merge_all()
        self.assertEqual(sorted(Learner.objects.values_list('source_id', flat=True)), [101, 104, 105, 110, 111, 113, 115])
        body, people = self.detail(self.l1)
        self.assertEqual(set(people), {101, 104, 105, 110, 111, 113, 115})
        self.assertEqual((people[101]['attendance'], people[101]['duration_seconds']), ('present', 3600))  # from 102
        self.assertEqual((people[104]['attendance'], people[104]['duration_seconds']), ('present', 4000))  # not 6000
        self.assertEqual((people[105]['attendance'], people[105]['duration_seconds']), ('present', 1500))  # from 106
        self.assertEqual(people[110]['attendance'], 'present')                                            # nested: 109
        self.assertEqual(people[111]['attendance'], 'absent')
        self.assertEqual((people[113]['speaking_seconds'], people[113]['speaking_match']), (120, 'verified_identity'))
        self.assertEqual(body['present'], 6)                                    # 101, 104, 105, 110, 113, 115
        self.assertEqual(body['verified_speakers'], 1)
        listed = self.listed()
        self.assertEqual(listed[str(self.l1.pk)]['present'], 6)                 # list and detail agree
        # Lecture 2's only Present row belongs to merged-away 107: still listed, under 105.
        self.assertIn(str(self.l2.pk), listed)
        self.assertEqual(self.detail(self.l2)[1][105]['attendance'], 'present')
        self.assertEqual(self.evidence(), self.stored)                          # stored evidence never rewritten

    def test_unmerge_restores_each_learners_own_rows(self):
        self.merge_all()
        for members, primary in GROUPS:
            self.act('unmerge', [primary])
        self.act('unmerge', [110])                                              # nested: parent 108+109 again
        body, people = self.detail(self.l1)
        self.assertEqual((people[101]['attendance'], people[102]['attendance']), ('absent', 'present'))
        self.assertEqual((people[103]['duration_seconds'], people[104]['duration_seconds']), (2000, 4000))
        self.assertEqual(people[108]['attendance'], 'present')                  # 109's row under parent primary 108
        self.assertNotIn(109, people)
        self.assertEqual(people[110]['attendance'], 'not_available')
        self.assertEqual(body['present'], 7)    # 102, 103, 104, 106, 108 (via 109), 114, 115
        self.act('unmerge', [108])
        self.assertEqual(self.detail(self.l1)[1][109]['attendance'], 'present')
        self.assertEqual(self.evidence(), self.stored)

    def speech(self, lecture, uid, seconds, speaking, method, attendance='present', emails=None):
        LectureLearner.objects.create(
            lecture=lecture, learner_source_id=uid, source_emails=[email(uid)], attendance=attendance,
            duration_seconds=seconds, speaking_seconds=speaking, speaker_verified=method == 'verified_identity',
            speaking_match=method, speaking_emails=emails or [email(uid)])

    def test_speaking_evidence_from_every_merged_account_and_unmerge(self):
        l3 = self.lecture('l3', '2026-10-03')
        self.speech(l3, 101, 3600, None, '')                     # attendance row has no speech
        self.speech(l3, 102, 1000, 200, 'verified_identity')     # the secondary spoke
        self.speech(l3, 103, 2000, 300, 'meeting_name')
        self.speech(l3, 104, 4000, 90, 'verified_identity')      # verified wins; never 390
        self.speech(l3, 106, 1500, 50, 'meeting_name')
        self.speech(l3, 107, 1200, 80, 'meeting_name')           # longer of two name matches
        self.speech(l3, 112, 1800, 60, 'verified_identity', emails=[email(115)])  # someone else's speech: rejected
        stored = self.evidence()
        self.merge_all()
        body, people = self.detail(l3)
        got = {uid: (people[uid]['duration_seconds'], people[uid]['speaking_seconds'], people[uid]['speaking_match'])
               for uid in (101, 104, 105, 111)}
        self.assertEqual(got, {101: (3600, 200, 'verified_identity'), 104: (4000, 90, 'verified_identity'),
                               105: (1500, 80, 'meeting_name'), 111: (1800, None, None)})
        self.assertEqual((body['present'], body['verified_speakers'], body['speaking_covered']), (4, 2, 3))
        self.assertEqual(self.listed()[str(l3.pk)]['speaking_covered'], 3)
        for members, primary in GROUPS:
            self.act('unmerge', [primary])
        people = self.detail(l3)[1]
        own = {uid: (people[uid]['speaking_seconds'], people[uid]['speaking_match']) for uid in (101, 102, 103, 104, 106, 107, 112)}
        self.assertEqual(own, {101: (None, None), 102: (200, 'verified_identity'), 103: (300, 'meeting_name'),
                               104: (90, 'verified_identity'), 106: (50, 'meeting_name'), 107: (80, 'meeting_name'),
                               112: (None, None)})
        self.assertEqual(self.evidence(), stored)

    def test_unmerged_library_is_unchanged_and_archived_learners_stay_hidden(self):
        body, people = self.detail(self.l1)
        self.assertEqual(people[102]['attendance'], 'present')
        self.assertEqual(people[101]['attendance'], 'absent')
        self.assertEqual(body['present'], 7)                                    # 112's 300 s short visit is not Present
        self.act('archive', [115])
        self.assertNotIn(115, self.detail(self.l1)[1])
        self.act('restore', [115])
        self.assertEqual(self.detail(self.l1)[1][115]['attendance'], 'present')
        self.assertEqual(self.evidence(), self.stored)
