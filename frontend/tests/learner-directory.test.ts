import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import { completionLabel, directoryCsv, directoryCsvCells, directoryCsvHeaders, downloadDirectory } from '../src/api/learnerDirectory.ts'
import type { LearnerRow } from '../src/api/types.ts'
import { attendanceHours, dateTime, pct } from '../src/utils/format.ts'

function learner(id = 1): LearnerRow {
  return {
    learner_id: id, name: 'Synthetic learner', mean_progress: 38.7,
    enrollments: [{ course_id: 83334, course: 'Planning, Controlling and Leading a Project', progress_pct: 50 },
      { course_id: 154268, course: 'Advanced Project and Logistics Management', progress_pct: 27.4 }],
    attendance: { available: true, pct: 75, attended: 3, expected: 4 },
    lessons_completed: 35, lessons_known: 130, lessons_completion_pct: 26.9,
    quizzes_attempted: 2, quizzes_known: 4, quiz_completion_pct: 50,
    mean_latest_score: 95.2, quizzes_passed_latest: 1, quizzes_graded: 2,
    assignments_submitted: 0, assignments_known: 2,
    verified_attendance_seconds: 10448, last_activity: '2030-01-01T00:15:42+03:00',
  } as LearnerRow
}

// Parse real CSV delimiters, including quoted commas, doubled quotes and embedded newlines.
function parseCsv(csv: string): string[][] {
  const rows: string[][] = []; let row: string[] = [], cell = '', quoted = false
  const text = csv.replace(/^\uFEFF/, '')
  for (let i = 0; i < text.length; i++) {
    const c = text[i]
    if (c === '"') {
      if (quoted && text[i + 1] === '"') { cell += '"'; i++ } else quoted = !quoted
    } else if (c === ',' && !quoted) { row.push(cell); cell = '' }
    else if (c === '\r' && text[i + 1] === '\n' && !quoted) {
      row.push(cell); rows.push(row); row = []; cell = ''; i++
    } else cell += c
  }
  assert.equal(quoted, false)
  return rows
}

test('CSV retains the table column order, IDs, full module names and every displayed metric', () => {
  const table = readFileSync(new URL('../src/components/learners/LearnerTable.vue', import.meta.url), 'utf8')
  assert.deepEqual(directoryCsvHeaders, [...table.matchAll(/label: '([^']+)'/g)].map(m => m[1]))
  assert.deepEqual(directoryCsvCells(learner()), [
    'Synthetic learner ID 1', 'Planning, Controlling and Leading a Project | Advanced Project and Logistics Management',
    '38.7%', '75% 3 / 4 lectures', '2h 54m',
    '35 / 130 (26.9%)', '2 / 4 (50%)', '95.2% 1 / 2 passed', '0 / 2', '31 Dec 2029',
  ])
})

test('missing data remains distinct from known zero activity and zero measured duration', () => {
  const missing = learner()
  Object.assign(missing, {
    mean_progress: null, attendance: { available: false, pct: null },
    lessons_completed: 0, lessons_known: 0, lessons_completion_pct: null,
    quizzes_attempted: 0, quizzes_known: 0, quiz_completion_pct: null,
    mean_latest_score: null, quizzes_graded: 0, assignments_known: 0,
    verified_attendance_seconds: null, last_activity: null,
  })
  assert.deepEqual(directoryCsvCells(missing).slice(2), ['—', 'N/A', 'N/A', 'N/A', 'N/A', 'N/A', 'N/A', 'No recorded activity'])
  Object.assign(missing, { lessons_known: 10, lessons_completion_pct: 0, quizzes_known: 4, quiz_completion_pct: 0,
    mean_latest_score: 0, quizzes_graded: 1, quizzes_passed_latest: 0, assignments_known: 2, verified_attendance_seconds: 0 })
  assert.deepEqual(directoryCsvCells(missing).slice(4, 9), ['0h 0m', '0 / 10 (0%)', '0 / 4 (0%)', '0% 0 / 1 passed', '0 / 2'])
  assert.equal(attendanceHours(45), '<1m')
})

test('CSV has UTF-8 BOM and escaped Unicode, commas and quotes with one physical line per learner', () => {
  const row = learner(); row.name = 'متعلم, "Example"\nSecond line'
  const text = directoryCsv([row])
  assert.deepEqual([...new TextEncoder().encode(text).slice(0, 3)], [239, 187, 191])
  const parsed = parseCsv(text)
  assert.equal(parsed.length, 2)
  assert.deepEqual(parsed[0], directoryCsvHeaders)
  assert.deepEqual(parsed[1]?.map(value => value.replaceAll('\u00a0', ' ')),
    directoryCsvCells(row).map(value => value.replaceAll('\n', ' ')))
  assert.equal(text.split('\r\n').length, 3) // Header, one learner, trailing terminator.
  assert.ok(parsed.flat().every(value => !/[\r\n\u2028\u2029]/.test(value)))
})

test('source line breaks are flattened without losing names, labels or full module titles', () => {
  const row = learner()
  row.name = 'Synthetic\r\nlearner\rSecond\nThird\u2028Fourth\u2029Fifth'
  row.enrollments[0]!.course = 'Planning, Controlling\r\n  and Leading a Project'
  row.enrollments[1]!.course = 'Advanced Project\n\tand Logistics Management'
  const original = structuredClone(row)
  const parsed = parseCsv(directoryCsv([row]))
  assert.equal(parsed[1]?.[0], 'Synthetic learner Second Third Fourth Fifth ID 1')
  assert.equal(parsed[1]?.[1], 'Planning, Controlling and Leading a Project | Advanced Project and Logistics Management')
  assert.ok(parsed.flat().every(value => !/[\r\n\u2028\u2029]/.test(value)))
  assert.deepEqual(row, original)
})

test('formula injection protection survives flattening names and module titles', () => {
  const row = learner()
  for (const [input, safe] of [
    ['=HYPERLINK("bad")', '=HYPERLINK("bad")'], ['+formula', '+formula'], ['-formula', '-formula'],
    ['@formula', '@formula'], ['  =formula', '  =formula'], ['\tformula', '\tformula'],
    ['\rformula', ' formula'], ['\nformula', ' formula'], ['\r\n  =formula', ' =formula'],
    ['\u2028=formula', ' =formula'],
  ]) {
    row.name = input!
    assert.equal(parseCsv(directoryCsv([row]))[1]?.[0], `'${safe} ID 1`)
    row.enrollments = [{ course_id: 83334, course: input!, progress_pct: 50 }]
    assert.equal(parseCsv(directoryCsv([row]))[1]?.[1], `'${safe}`)
  }
})

test('official attendance counts and percentage pass through from the API without recalculation', () => {
  for (const [attended, expected, percentage] of [[47, 58, 81], [0, 16, 0], [10, 30, 33.3], [38, 58, 65.5]]) {
    const row = learner()
    Object.assign(row.attendance, { attended, expected, pct: percentage })
    assert.equal(parseCsv(directoryCsv([row]))[1]?.[3], `${percentage}% ${attended} / ${expected} lectures`)
  }
})

test('assignment ratios stay text in Excel without formulas or visible apostrophes', () => {
  const row = learner(); row.assignments_submitted = 1; row.assignments_known = 3
  assert.equal(directoryCsvCells(row)[8], '1 / 3')
  assert.equal(parseCsv(directoryCsv([row]))[1]?.[8], '1\u00a0/\u00a03')
})

test('one download fetches every page with the selected course, search, status and ordering', async () => {
  const calls: string[] = []
  const filters = { search: '  Synthetic  ', engagement: 'active', ordering: '-progress', course: '154268', page: 2 }
  const request = (async (path, init) => {
    calls.push(String(path))
    const url = new URL(String(path), 'http://localhost')
    assert.equal(url.pathname, '/api/learners')
    assert.equal(url.searchParams.get('course'), '154268')
    assert.equal(url.searchParams.get('search'), 'Synthetic')
    assert.equal(url.searchParams.get('engagement'), 'active')
    assert.equal(url.searchParams.get('ordering'), '-progress')
    assert.equal(url.searchParams.get('page_size'), '100')
    assert.deepEqual(init, { credentials: 'same-origin', cache: 'no-store', headers: { Accept: 'application/json' } })
    const page = Number(url.searchParams.get('page'))
    return Response.json({ count: 137, pages: 2, page, results: Array.from({ length: page === 1 ? 100 : 37 }, (_, i) => learner((page - 1) * 100 + i + 1)) })
  }) as typeof fetch
  const result = await downloadDirectory(filters, request)
  assert.equal(calls.length, 2)
  assert.equal(result.filename, 'alfanar-learners.csv')
  assert.equal(result.blob.type, 'text/csv;charset=utf-8')
  const parsed = parseCsv(await result.blob.text())
  assert.equal(parsed.length, 138)
  assert.equal(parsed[1]?.[0], 'Synthetic learner ID 1')
  assert.equal(parsed[137]?.[0], 'Synthetic learner ID 137')
})

for (const [course, title] of [
  ['', ''],
  ['83334', 'Planning, Controlling and Leading a Project'],
  ['121642', 'Procurement Risk and Contract Management'],
  ['135945', 'Operations and Information Management for Project Managers'],
  ['154268', 'Advanced Project and Logistics Management'],
] as const) {
  test(`export preserves the API's displayed scope and values for ${course || 'All Modules'}`, async () => {
    const row = learner()
    if (course) {
      // Deliberately different from the all-module fixture: the API owns all metrics.
      Object.assign(row, {
        enrollments: [{ course_id: Number(course), course: title, progress_pct: 12.5 }],
        mean_progress: 12.5, attendance: { available: true, pct: 50, attended: 7, expected: 14 },
        verified_attendance_seconds: 3601, lessons_completed: 1, lessons_known: 8, lessons_completion_pct: 12.5,
        quizzes_attempted: 1, quizzes_known: 4, quiz_completion_pct: 25,
        mean_latest_score: 80, quizzes_passed_latest: 1, quizzes_graded: 1,
        assignments_submitted: 0, assignments_known: 0, last_activity: null,
      })
    }
    const result = await downloadDirectory({ course, search: ' Synthetic ', engagement: 'active', ordering: '-attendance' },
      (async path => {
        const params = new URL(String(path), 'http://localhost').searchParams
        assert.equal(params.get('course'), course || null)
        assert.equal(params.get('search'), 'Synthetic')
        assert.equal(params.get('engagement'), 'active')
        assert.equal(params.get('ordering'), '-attendance')
        return Response.json({ count: 1, pages: 1, page: 1, results: [row] })
      }) as typeof fetch)
    const parsed = parseCsv(await result.blob.text())
    assert.equal(parsed.length, 2)
    assert.deepEqual(parsed[0], directoryCsvHeaders)
    assert.deepEqual(parsed[1]?.map(value => value.replaceAll('\u00a0', ' ')), course ? [
      'Synthetic learner ID 1', title, '12.5%', '50% 7 / 14 lectures', '1h 0m',
      '1 / 8 (12.5%)', '1 / 4 (25%)', '80% 1 / 1 passed', 'N/A', 'No recorded activity',
    ] : directoryCsvCells(row))
  })
}

test('all 37 learners are exported in one request, while an empty filter result is header-only', async () => {
  for (const count of [37, 0]) {
    let calls = 0
    const result = await downloadDirectory({}, (async path => {
      calls++
      assert.equal(new URL(String(path), 'http://localhost').searchParams.has('course'), false)
      return Response.json({ count, pages: 1, page: 1, results: Array.from({ length: count }, (_, i) => learner(i + 1)) })
    }) as typeof fetch)
    assert.equal(calls, 1)
    assert.equal(parseCsv(await result.blob.text()).length, count + 1)
  }
})

test('failed/unauthorized responses never return a downloadable file', async () => {
  for (const status of [401, 403, 500]) {
    await assert.rejects(downloadDirectory({}, (async () => new Response('{}', { status })) as typeof fetch))
  }
  await assert.rejects(downloadDirectory({}, (async () => { throw new Error('offline') }) as typeof fetch))
})

test('duplicate, missing and changed pagination refuses partial or inconsistent exports', async () => {
  for (const results of [[learner(), learner()], [learner()]]) {
    await assert.rejects(downloadDirectory({}, (async () => Response.json({ count: 2, pages: 1, page: 1, results })) as typeof fetch))
  }
  let calls = 0
  await assert.rejects(downloadDirectory({}, (async () => {
    calls++
    return Response.json({ count: calls === 1 ? 2 : 3, pages: 2, page: calls, results: [learner(calls)] })
  }) as typeof fetch))
})

test('directory formatters retain existing percentages, duration and UTC date boundaries', () => {
  assert.equal(completionLabel(114, 150, 76), '114 / 150 (76%)')
  assert.equal(completionLabel(0, undefined, undefined), 'N/A')
  assert.equal(attendanceHours(null), 'N/A')
  assert.equal(dateTime('2030-09-16T13:30:00Z', false), '16 Sept 2030')
  assert.equal(pct(95), '95%')
  assert.equal(pct(95.2), '95.2%')
})
