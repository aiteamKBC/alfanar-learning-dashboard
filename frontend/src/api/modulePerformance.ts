import type { Charts, Matrix, MatrixAttendance, ModuleLearnerMetrics } from './types'

/** Sum existing merged session evidence, including brief visits; unknown is not zero. */
export function verifiedAttendanceSeconds(attendance: Pick<MatrixAttendance, 'sessions'>): number | null {
  const durations = attendance.sessions
    .filter((session) => session.timing.available && session.timing.duration_seconds != null)
    .map((session) => session.timing.duration_seconds as number)
  return durations.length ? durations.reduce((sum, seconds) => sum + seconds, 0) : null
}

function fromMatrix(matrix: Matrix): ModuleLearnerMetrics {
  const footer = matrix.footer
  return {
    quizzes_attempted: footer.quiz.recorded,
    quizzes_known: footer.quiz.known,
    mean_latest_score: footer.quiz.mean_latest_score,
    lessons_by_type: Object.fromEntries((['video', 'pdf', 'audio', 'reading'] as const)
      .map((kind) => [kind, { completed: footer[kind].recorded, known: footer[kind].known }])),
    assignments_submitted: footer.assignment.submitted,
    assignments_known: footer.assignment.known,
    verified_attendance_seconds: verifiedAttendanceSeconds(matrix.attendance),
  }
}

function complete(metrics: ModuleLearnerMetrics | undefined): boolean {
  return metrics != null && (['quizzes_attempted', 'quizzes_known', 'mean_latest_score',
    'lessons_by_type', 'assignments_submitted', 'assignments_known', 'verified_attendance_seconds'] as const)
    .every((key) => metrics[key] !== undefined)
}

/** Older running APIs omit the new aggregates but already expose the authoritative matrix. */
export async function withModulePerformance(
  charts: Charts,
  selectedCourse: string,
  loadMatrix: (learnerId: number, courseId: number) => Promise<Matrix>,
): Promise<Charts> {
  if (!selectedCourse) return charts
  const courseId = Number(selectedCourse)
  if (!Number.isSafeInteger(courseId)) throw new Error('Invalid selected module.')
  const rows = await Promise.all(charts.learner_module_matrix.rows.map(async (row) => {
    if (!(courseId in row.progress)) throw new Error('Learner metrics do not match the selected module.')
    if (complete(row.module_metrics?.[courseId])) return row
    const matrix = await loadMatrix(row.learner_id, courseId)
    if (matrix.learner.learner_id !== row.learner_id || matrix.course.course_id !== courseId) {
      throw new Error('Activity matrix does not match the selected learner and module.')
    }
    return { ...row, module_metrics: { ...row.module_metrics, [courseId]: fromMatrix(matrix) } }
  }))
  return { ...charts, learner_module_matrix: { ...charts.learner_module_matrix, rows } }
}
