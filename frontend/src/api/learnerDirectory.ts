import type { LearnerRow, Paginated } from './types.ts'
import { attendanceHours, dateTime, num, pct } from '../utils/format.ts'

export function completionLabel(completed: number, known: number | null | undefined, percentage: number | null | undefined) {
  if (!known || percentage === null || percentage === undefined) return 'N/A'
  return `${num(completed)} / ${num(known)} (${pct(percentage)})`
}

// Same columns as LearnerTable: name and ID share its first cell.
export const directoryCsvHeaders = [
  'Learner', 'Enrolled modules', 'Mean LMS progress', 'Attendance evidence',
  'Total attendance hours', 'Materials completed', 'Quizzes attempted', 'Average Quiz Score', 'Assignments', 'Last activity',
] as const

export function directoryCsvCells(row: LearnerRow): string[] {
  return [
    `${row.name}\nID ${row.learner_id}`,
    row.enrollments.map(enrollment => enrollment.course).join('\n'),
    pct(row.mean_progress),
    row.attendance.available
      ? `${pct(row.attendance.pct)}\n${row.attendance.attended} / ${row.attendance.expected} lectures`
      : 'N/A',
    attendanceHours(row.verified_attendance_seconds),
    completionLabel(row.lessons_completed, row.lessons_known, row.lessons_completion_pct),
    completionLabel(row.quizzes_attempted, row.quizzes_known, row.quiz_completion_pct),
    (row.mean_latest_score === null ? 'N/A' : pct(row.mean_latest_score)) +
      (row.quizzes_graded ? `\n${row.quizzes_passed_latest} / ${row.quizzes_graded} passed` : ''),
    row.assignments_known ? `${row.assignments_submitted} / ${row.assignments_known}` : 'N/A',
    row.last_activity ? dateTime(row.last_activity, false) : 'No recorded activity',
  ]
}

function csvCell(value: string) {
  // Excel parses a bare "1 / 3" as a date even inside CSV quotes. Visually
  // identical nonbreaking spaces keep displayed assignment ratios as text.
  const text = /^\d+ \/ \d+$/.test(value) ? value.replaceAll(' ', '\u00a0') : value
  // CSV quoting does not prevent Excel formula execution in untrusted names.
  const literal = /^[\t\r\n]/.test(text) || /^[=+\-@]/.test(text.trimStart()) ? `'${text}` : text
  return `"${literal.replace(/"/g, '""')}"`
}

export function directoryCsv(rows: LearnerRow[]) {
  return '\uFEFF' + [directoryCsvHeaders, ...rows.map(directoryCsvCells)]
    .map(cells => cells.map(csvCell).join(',')).join('\r\n') + '\r\n'
}

export interface DirectoryExportFilters { search?: string; engagement?: string; ordering?: string }

export async function downloadDirectory(filters: DirectoryExportFilters, request: typeof fetch = fetch) {
  // Deliberately omit course and the screen's page: exports always use All Modules.
  const params = new URLSearchParams({ ordering: filters.ordering || 'name', page_size: '100' })
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
