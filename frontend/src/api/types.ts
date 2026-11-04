export type Kind = 'quiz' | 'video' | 'pdf' | 'audio' | 'reading' | 'assignment'
export type Definitions = Record<string, string>

export interface Kpi {
  value: number | null
  denominator?: number
  pct?: number | null
  median?: number | null
  n?: number
  unit?: string
  total_records?: number
  by_status?: Record<string, number>
  available?: boolean
}

export interface Summary {
  kpis: Record<string, Kpi> & { attendance: AttendanceAggregate }
  definitions: Definitions
}

export interface ModuleProgress { course_id: number; course: string; enrollments: number; active: number; mean_progress: number | null; median_progress: number | null; delivered_sessions: number | null; lecture_recordings: number; attendance: AttendanceAggregate }
export interface QuizByModule { course_id: number; course: string; known_quizzes: number; attempted_pairs: number; possible_pairs: number; completion_pct: number | null; mean_latest_score: number | null; pass_rate_pct: number | null }
export interface CompletionCell { completed: number; possible: number; known_items: number; pct: number | null }
export interface ActivityCompletion { course_id: number; course: string; by_type: Record<string, CompletionCell> }
export interface MatrixLearnerRow { learner_id: number; name: string; progress: Record<string, number | null> }
export interface Charts {
  module_progress: ModuleProgress[]
  quiz_by_module: QuizByModule[]
  activity_completion: ActivityCompletion[]
  activity_by_type: { type: Kind; label: string; count: number }[]
  progress_distribution: { buckets: { bucket: string; count: number }[]; missing: number; total: number }
  learner_module_matrix: { modules: { course_id: number; course: string }[]; rows: MatrixLearnerRow[] }
  definitions: Definitions
}

export interface FiltersResponse {
  courses: { course_id: number; title: string }[]
  activity_types: { value: Kind; label: string }[]
  engagement: { value: string; label: string }[]
}

export interface Rollup {
  attendance: RollupAttendance
  modules: number
  mean_progress: number | null
  lessons_completed: number
  quizzes_attempted: number
  quizzes_known: number
  quiz_attempts: number
  mean_latest_score: number | null
  quizzes_passed_latest: number
  quizzes_graded: number
  assignments_submitted: number
  assignments_known: number
  recorded_seconds: number
  has_activity: boolean
  last_activity: string | null
}

export interface LearnerRow extends Rollup {
  learner_id: number
  name: string
  enrollments: { course_id: number; course: string; progress_pct: number | null }[]
}

export interface Paginated<T> { count: number; page: number; page_size: number; pages: number; ordering: string; results: T[] }

export interface CourseProgress {
  course_id: number
  course: string
  progress_pct: number | null
  has_activity: boolean
  lessons_completed: number
  lessons_known: number
  lessons_by_type: Record<string, { completed: number; known: number }>
  quizzes_attempted: number
  quizzes_known: number
  quiz_attempts: number
  mean_latest_score: number | null
  quizzes_passed_latest: number
  quizzes_graded: number
  assignments_submitted: number
  assignments_known: number
  submission_status: Record<string, number>
  last_activity: string | null
  last_login: string | null
  registered_at: string | null
  attendance: EnrollmentAttendance
}

export interface LearnerDetail {
  learner: { learner_id: number; name: string; email: string }
  summary: Rollup
  courses: CourseProgress[]
  definitions: Definitions
}

export interface QuizCell {
  questions: number | null
  attempts: number | null
  latest_attempt: number | null
  latest_score: number | null
  latest_status: string
  best_score: number | null
  ever_passed: boolean | null
  history: { attempt: number; score: number | null; status: string; created_at: string | null }[]
}

export interface MatrixRow {
  activity_id: number
  kind: Kind
  kind_label: string
  title: string
  recorded: boolean
  lesson?: { recorded_seconds: number | null; started_at: string | null; ended_at: string | null; time_source: string } | null
  quiz?: QuizCell | null
  quiz_questions?: number | null
  assignment?: { status: string; grade: number | null; updated_at: string | null; submissions: number } | null
}

export interface ColTotal { known: number; recorded: number }
export interface Matrix {
  learner: { learner_id: number; name: string }
  course: { course_id: number; title: string; progress_pct: number | null }
  matrix_basis: string
  matrix_note: string
  attendance: MatrixAttendance
  kinds: Kind[]
  rows: MatrixRow[]
  footer: {
    activities: ColTotal
    quiz: ColTotal & { passed_latest: number; mean_latest_score: number | null; graded: number; attempts: number }
    video: ColTotal
    audio: ColTotal
    pdf: ColTotal
    reading: ColTotal
    assignment: ColTotal & { submitted: number }
    recorded_seconds: number
  }
  definitions: Definitions
}

export interface SyncRunInfo {
  id: number
  source_type: string
  source_ref: string
  status: string
  started_at: string
  finished_at: string | null
  source_modified_at: string | null
  latest_activity_at: string | null
  changed: boolean
  counts: Record<string, number>
  warnings: string[]
  errors: string[]
}
export interface SyncStatus {
  last_run: SyncRunInfo | null
  last_success: SyncRunInfo | null
  google_sheets: { enabled: boolean; configured: boolean; interval_seconds: number; scope: string }
  teams_attendance: { last_status: string; last_success_at: string | null; coverage_start: string | null; coverage_end: string | null; source_ref: string; warnings: string[] } | null
}

// ---- Microsoft Teams attendance ----------------------------------------------
export interface AttendanceAggregate {
  available: boolean
  attended: number
  expected: number
  pct: number | null
  live_seconds: number
  delivered_sessions: number
  enrollments_covered: number
  enrollments_total: number
  enrollments_without_teams_identity: number
  modules_without_data: number[]
}
export interface EnrollmentAttendance {
  available: boolean
  delivered: number | null
  attended: number | null
  pct: number | null
  live_seconds: number | null
  teams_identity?: boolean
  reason?: string
}
export interface RollupAttendance {
  available: boolean
  attended: number | null
  expected: number | null
  pct: number | null
  live_seconds: number | null
  modules_covered: number
  modules_total: number
}
export interface LiveSession {
  number: number
  date: string
  starts_at: string
  ends_at: string
  subject: string
  learners_attended: number
  status: 'attended' | 'brief' | 'not_recorded'
  presence_seconds: number | null
  first_join: string | null
  last_leave: string | null
  joins: number | null
}
export interface MatrixAttendance extends EnrollmentAttendance {
  sessions: LiveSession[]
}
