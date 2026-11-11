import type { LearnerRow, Paginated } from './types.ts'
import { attendanceHours, dateTime, num, pct } from '../utils/format.ts'

export function completionLabel(completed: number, known: number | null | undefined, percentage: number | null | undefined) {
  if (!known || percentage === null || percentage === undefined) return 'N/A'
  return `${num(completed)} / ${num(known)} (${pct(percentage)})`
}

// Keep LearnerTable's metric order, with combined values in adjacent CSV cells.
export const directoryCsvHeaders = [
  'Learner', 'Enrolled modules', 'Mean LMS progress', 'Attendance Count', 'Attendance Percentage',
  'Total attendance hours', 'Materials completed', 'Materials Completion (%)',
  'Quizzes attempted', 'Quiz Completion (%)', 'Average Quiz Score', 'Quizzes Passed / Graded',
  'Assignments', 'Last activity',
] as const

function completionCsvCells(completed: number, known: number | null | undefined, percentage: number | null | undefined) {
  // Preserve completionLabel's availability rules and formatting without recalculating.
  if (!known || percentage === null || percentage === undefined) return ['N/A', 'N/A']
  return [`${num(completed)} / ${num(known)}`, pct(percentage)]
}

export function directoryCsvCells(row: LearnerRow): string[] {
  return [
    `${row.name} ID ${row.learner_id}`,
    row.enrollments.map(enrollment => enrollment.course).join(' | '),
    pct(row.mean_progress),
    row.attendance.available ? `${row.attendance.attended} / ${row.attendance.expected}` : 'N/A',
    row.attendance.available ? pct(row.attendance.pct) : 'N/A',
    attendanceHours(row.verified_attendance_seconds),
    ...completionCsvCells(row.lessons_completed, row.lessons_known, row.lessons_completion_pct),
    ...completionCsvCells(row.quizzes_attempted, row.quizzes_known, row.quiz_completion_pct),
    row.mean_latest_score === null ? 'N/A' : pct(row.mean_latest_score),
    row.quizzes_graded ? `${row.quizzes_passed_latest} / ${row.quizzes_graded}` : 'N/A',
    row.assignments_known ? `${row.assignments_submitted} / ${row.assignments_known}` : 'N/A',
    row.last_activity ? dateTime(row.last_activity, false) : 'No recorded activity',
  ]
}

function csvCell(value: string) {
  // Keep one physical CSV line per learner, including source names with line breaks.
  // Check the original value before flattening so leading control characters
  // cannot bypass Excel formula protection.
  const unsafe = /^[\t\r\n]/.test(value) || /^[=+\-@]/.test(value.trimStart())
  const singleLine = value.replace(/[ \t]*[\r\n\u2028\u2029]+[ \t]*/g, ' ')
  // Excel parses a bare "1 / 3" as a date even inside CSV quotes. Visually
  // identical nonbreaking spaces keep all exported count ratios as text.
  const text = /^[\d,]+ \/ [\d,]+$/.test(singleLine) ? singleLine.replaceAll(' ', '\u00a0') : singleLine
  // CSV quoting does not prevent Excel formula execution in untrusted names.
  const literal = unsafe ? `'${text}` : text
  return `"${literal.replace(/"/g, '""')}"`
}

export function directoryCsv(rows: LearnerRow[]) {
  return '\uFEFF' + [directoryCsvHeaders, ...rows.map(directoryCsvCells)]
    .map(cells => cells.map(csvCell).join(',')).join('\r\n') + '\r\n'
}

export interface DirectoryExportFilters { course?: string; search?: string; engagement?: string; ordering?: string }

export async function downloadDirectory(filters: DirectoryExportFilters, request: typeof fetch = fetch) {
  // Use the table's filters, but fetch every matching page for the download.
  const params = new URLSearchParams({ ordering: filters.ordering || 'name', page_size: '100' })
  if (filters.course) params.set('course', filters.course)
  if (filters.search?.trim()) params.set('search', filters.search.trim())
  if (filters.engagement) params.set('engagement', filters.engagement)
  const rows: LearnerRow[] = []
  const ids = new Set<number>()
  let count: number | undefined
  let pages = 1
  for (let page = 1; page <= pages; page++) {
    params.set('page', String(page))
    const response = await request(`/api/learners?${params}`, {
      credentials: 'same-origin', cache: 'no-store', headers: { Accept: 'application/json' },
    })
    if (!response.ok) {
      const body = await response.json().catch(() => null)
      throw new Error(response.status === 401 || response.status === 403
        ? 'Your session cannot export learners. Refresh and sign in again.'
        : body?.error?.message ?? 'Unable to export learners. Please try again.')
    }
    const data: Paginated<LearnerRow> = await response.json()
    if (!Number.isInteger(data.count) || data.count < 0 || !Number.isInteger(data.pages) || data.pages < 1 ||
        data.page !== page || !Array.isArray(data.results) ||
        (count !== undefined && (data.count !== count || data.pages !== pages)) ||
        (page < data.pages && !data.results.length)) {
      throw new Error('The learner list changed or could not be read completely. Please export again.')
    }
    count = data.count; pages = data.pages
    for (const row of data.results) {
      if (ids.has(row.learner_id)) throw new Error('The learner list changed during export. Please export again.')
      ids.add(row.learner_id); rows.push(row)
    }
  }
  if (rows.length !== count) throw new Error('The complete learner list was not received. Please export again.')
  return { blob: new Blob([directoryCsv(rows)], { type: 'text/csv;charset=utf-8' }), filename: 'alfanar-learners.csv' }
}
