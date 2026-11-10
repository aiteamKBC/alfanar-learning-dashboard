export interface LectureSummary {
  id: string
  course_id: number | null
  course: string
  date: string | null
  starts_at: string | null
  ends_at: string | null
  source: string
  recording_session: boolean
  learners: number
  present: number
  recorded: number
  recordings: number
  playable_recordings: number
  transcripts: number
  readable_transcripts: number
  verified_speakers: number
  speaking_covered: number
  updated_at: string
}

export interface LectureList {
  count: number
  cohort_count: number
  total_recordings: number
  playable_recordings: number
  page: number
  pages: number
  results: LectureSummary[]
}

export interface LectureParticipant {
  learner_id: number
  name: string
  email: string
  attendance: 'present' | 'absent' | 'review' | 'not_available'
  first_join: string | null
  last_leave: string | null
  duration_seconds: number | null
  speaking_seconds: number | null
  speaking_match: 'verified_identity' | 'meeting_name' | null
  speaking_partial: boolean
  camera_on: boolean | null
  microphone_on: boolean | null
  needs_review: boolean
}

export interface LectureAsset {
  id: number
  kind: 'recording' | 'transcript'
  available: boolean
  created_at: string | null
  url: string | null
}

export interface LectureDetail extends LectureSummary {
  participants: LectureParticipant[]
  resources: LectureAsset[]
  recording_folder_url: string | null
  timezone: string
}

export function lectureDate(value: string | null, compact = false): string {
  if (!value) return 'Date not recorded'
  return new Date(value.length === 10 ? `${value}T12:00:00Z` : value).toLocaleDateString('en-GB', {
    day: '2-digit', month: compact ? 'short' : 'long', year: 'numeric', timeZone: 'Africa/Cairo',
  })
}

export function lectureTime(value: string | null): string {
  if (!value) return 'Not available'
  return new Date(value).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', timeZone: 'Africa/Cairo' })
}

export function lectureDuration(seconds: number | null): string {
  if (seconds === null) return 'Not available'
  if (seconds > 0 && seconds < 1) return '<1s'
  const whole = Math.round(seconds)
  const hours = Math.floor(whole / 3600)
  const minutes = Math.floor(whole / 60) % 60
  const rest = whole % 60
  return hours ? `${hours}h ${minutes}m ${rest}s` : `${minutes}m ${rest}s`
}
