import assert from 'node:assert/strict'
import { test } from 'node:test'
import type { Charts, Matrix, MatrixAttendance } from '../src/api/types.ts'
import { verifiedAttendanceSeconds, withModulePerformance } from '../src/api/modulePerformance.ts'

function attendance(): MatrixAttendance {
  return {
    available: true, delivered: 3, attended: 1, pct: 100, live_seconds: null, timing_available: true,
    sessions: [2700, 41, null].map((seconds, i) => ({
      number: i + 1, date: `2026-01-0${i + 5}`, starts_at: '', ends_at: '', subject: 'Fictional module',
      learners_attended: 1, status: 'unavailable', presence_seconds: null, first_join: null,
      last_leave: null, joins: null, reconciliation: null,
      timing: { available: seconds !== null, timezone: 'UTC', first_join: '2026-01-05T09:00:00Z',
        last_leave: '2026-01-05T11:35:00Z', duration_seconds: seconds, visits: 2 },
    })),
  }
}

function matrix(): Matrix {
  return {
    learner: { learner_id: 101, name: 'Fictional learner' },
    course: { course_id: 9001, title: 'Fictional module', progress_pct: 50 },
    attendance: attendance(), matrix_basis: 'activity', matrix_note: '', kinds: [], rows: [], definitions: {},
    footer: {
      activities: { known: 10, recorded: 4 },
      quiz: { known: 5, recorded: 2, passed_latest: 1, mean_latest_score: 63.5, graded: 2, attempts: 4 },
      video: { known: 3, recorded: 1 }, pdf: { known: 2, recorded: 0 },
      audio: { known: 1, recorded: 1 }, reading: { known: 0, recorded: 0 },
      assignment: { known: 2, recorded: 2, submitted: 1 }, recorded_seconds: 999999,
    },
  }
}

function charts(): Charts {
  return {
    module_progress: [], quiz_by_module: [], activity_completion: [], activity_by_type: [],
    progress_distribution: { buckets: [], missing: 0, total: 1 }, definitions: {},
    learner_module_matrix: {
      modules: [{ course_id: 9001, course: 'Fictional module' }],
      rows: [{ learner_id: 101, name: 'Fictional learner', progress: { '9001': 50 } }],
    },
  }
}

test('old API without aggregate fields uses exact learner-module matrix values', async () => {
  const input = charts()
  const calls: number[][] = []
  const result = await withModulePerformance(input, '9001', async (learner, course) => {
    calls.push([learner, course]); return matrix()
  })
  assert.deepEqual(calls, [[101, 9001]])
  assert.deepEqual(result.learner_module_matrix.rows[0].module_metrics?.['9001'], {
    quizzes_attempted: 2, quizzes_known: 5, mean_latest_score: 63.5,
    lessons_by_type: { video: { completed: 1, known: 3 }, pdf: { completed: 0, known: 2 },
      audio: { completed: 1, known: 1 }, reading: { completed: 0, known: 0 } },
    assignments_submitted: 1, assignments_known: 2, verified_attendance_seconds: 2741,
  })
  assert.equal(input.learner_module_matrix.rows[0].module_metrics, undefined)
})

test('complete current API response needs no per-learner requests, including genuine nulls', async () => {
  const current = await withModulePerformance(charts(), '9001', async () => matrix())
  const metrics = current.learner_module_matrix.rows[0].module_metrics!['9001']
  metrics.mean_latest_score = null
  metrics.verified_attendance_seconds = null
  const result = await withModulePerformance(current, '9001', async () => { throw new Error('Unnecessary fallback') })
  assert.deepEqual(result, current)
})

test('partial aggregates are resolved from the matrix; all-module view fetches nothing', async () => {
  const current = await withModulePerformance(charts(), '9001', async () => matrix())
  Reflect.deleteProperty(current.learner_module_matrix.rows[0].module_metrics!['9001'], 'verified_attendance_seconds')
  const repaired = await withModulePerformance(current, '9001', async () => matrix())
  assert.equal(repaired.learner_module_matrix.rows[0].module_metrics?.['9001'].verified_attendance_seconds, 2741)
  const all = charts()
  assert.equal(await withModulePerformance(all, '', async () => { throw new Error('Unrelated module fetch') }), all)
})

test('wrong learner, wrong course and failed retrieval cannot silently become N/A', async () => {
  for (const mismatch of ['learner', 'course']) {
    const wrong = matrix()
    if (mismatch === 'learner') wrong.learner.learner_id = 102
    else wrong.course.course_id = 9002
    await assert.rejects(withModulePerformance(charts(), '9001', async () => wrong), /does not match/)
  }
  await assert.rejects(withModulePerformance(charts(), '9002', async () => matrix()), /selected module/)
  await assert.rejects(withModulePerformance(charts(), '9001', async () => { throw new Error('Offline') }), /Offline/)
})

test('footer total uses verified merged seconds, including brief visits, not elapsed spans or LMS timers', () => {
  const data = attendance()
  data.verified_attendance_seconds = 999999 // An inconsistent cached aggregate is not authoritative here.
  assert.equal(verifiedAttendanceSeconds(data), 2741)
  data.sessions[0].timing.available = false
  assert.equal(verifiedAttendanceSeconds(data), 41)
  data.sessions[1].timing.duration_seconds = null
  assert.equal(verifiedAttendanceSeconds(data), null)
  data.sessions[1].timing.duration_seconds = 0
  assert.equal(verifiedAttendanceSeconds(data), 0)
  assert.equal(verifiedAttendanceSeconds({ sessions: [] }), null)
})

test('parallel module requests retain their selected course and learner identity', async () => {
  const first = withModulePerformance(charts(), '9001', async () => matrix())
  const secondCharts = charts()
  secondCharts.learner_module_matrix.rows[0].progress = { '9002': 75 }
  const secondMatrix = matrix()
  secondMatrix.course.course_id = 9002
  secondMatrix.footer.quiz.mean_latest_score = 95
  const second = withModulePerformance(secondCharts, '9002', async () => secondMatrix)
  const [a, b] = await Promise.all([first, second])
  assert.equal(a.learner_module_matrix.rows[0].module_metrics?.['9001'].mean_latest_score, 63.5)
  assert.equal(b.learner_module_matrix.rows[0].module_metrics?.['9002'].mean_latest_score, 95)
})
