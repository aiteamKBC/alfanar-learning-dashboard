import { num, pct } from '../utils/format.ts'

export const exportModules = [
  { id: 83334, label: 'Planning' },
  { id: 121642, label: 'Procurement Risk' },
  { id: 135945, label: 'Operations' },
  { id: 154268, label: 'Advanced' },
] as const
export type ExportFormat = 'xlsx' | 'csv'

export function completionLabel(completed: number, known: number | null | undefined, percentage: number | null | undefined) {
  if (!known || percentage === null || percentage === undefined) return 'N/A'
  return `${num(completed)} / ${num(known)} (${pct(percentage)})`
}

export function exportPath(selected: number[], format: ExportFormat) {
  const modules = exportModules.filter(m => selected.includes(m.id)).map(m => m.id)
  if (!modules.length || selected.some(id => !modules.includes(id as typeof modules[number]))) {
    throw new Error('Select at least one valid module.')
  }
  if (format === 'csv' && modules.length !== 1) throw new Error('CSV requires one selected module.')
  return `learners/export?${new URLSearchParams({ courses: modules.join(','), file_type: format })}`
}

export async function downloadModules(selected: number[], format: ExportFormat, request: typeof fetch = fetch) {
  const response = await request(`/api/${exportPath(selected, format)}`, { credentials: 'same-origin', cache: 'no-store' })
  if (!response.ok) {
    const body = await response.json().catch(() => null)
    throw new Error(response.status === 401 || response.status === 403
      ? 'Your session cannot export learners. Refresh and sign in again.'
      : body?.error?.message ?? 'Unable to export learners. Please try again.')
  }
  return { blob: await response.blob(), filename: `alfanar-learners.${format}` }
}
