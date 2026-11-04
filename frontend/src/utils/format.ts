import type { Kind } from '@/api/types'

const nf = new Intl.NumberFormat('en-GB')
export const num = (v: number | null | undefined) => (v === null || v === undefined ? '—' : nf.format(v))
export const pct = (v: number | null | undefined, digits = 1) =>
  v === null || v === undefined ? '—' : `${Number(v).toFixed(digits).replace(/\.0$/, '')}%`
export const ratio = (a: number, b: number) => `${nf.format(a)} / ${nf.format(b)}`

export function dateTime(v: string | null | undefined, withTime = true) {
  if (!v) return '—'
  const d = new Date(v)
  return d.toLocaleString('en-GB', withTime
    ? { day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit', timeZone: 'UTC' }
    : { day: '2-digit', month: 'short', year: 'numeric', timeZone: 'UTC' }) + (withTime ? ' UTC' : '')
}

export function relative(v: string | null | undefined) {
  if (!v) return ''
  const diff = (Date.now() - new Date(v).getTime()) / 1000
  if (diff < 60) return 'just now'
  if (diff < 3600) return `${Math.round(diff / 60)} min ago`
  if (diff < 86400) return `${Math.round(diff / 3600)} h ago`
  return `${Math.round(diff / 86400)} d ago`
}

export function duration(seconds: number | null | undefined) {
  if (seconds === null || seconds === undefined) return '—'
  const h = Math.floor(seconds / 3600), m = Math.floor((seconds % 3600) / 60), s = seconds % 60
  if (h) return `${h}h ${m}m`
  if (m) return `${m}m ${s}s`
  return `${s}s`
}

/** Fixed categorical slot per activity kind (identity never depends on rank or filter). */
export const KIND_COLOR: Record<Kind, string> = {
  quiz: '#2a78d6', video: '#eb6834', pdf: '#1baf7a', audio: '#eda100', reading: '#e87ba4', assignment: '#008300',
}
export const KIND_LABEL: Record<Kind, string> = {
  quiz: 'Quiz', video: 'Video', pdf: 'PDF', audio: 'Audio', reading: 'Reading', assignment: 'Assignment',
}

/** Sequential blue ramp (light → dark) for magnitude 0..100. */
export const SEQ_RAMP = ['#cde2fb', '#b7d3f6', '#9ec5f4', '#86b6ef', '#6da7ec', '#5598e7', '#3987e5', '#2a78d6', '#256abf', '#1c5cab', '#184f95', '#104281']
export function rampColor(v: number) {
  const i = Math.min(SEQ_RAMP.length - 1, Math.max(0, Math.round((v / 100) * (SEQ_RAMP.length - 1))))
  return SEQ_RAMP[i]
}
export function rampText(v: number) { return v >= 55 ? '#ffffff' : '#0f172a' }

/** Seconds -> '1,339 h' (or minutes when under an hour). */
export function hours(seconds: number | null | undefined) {
  if (seconds === null || seconds === undefined) return '—'
  if (seconds < 3600) return `${Math.round(seconds / 60)} min`
  return `${nf.format(Math.round(seconds / 3600))} h`
}
