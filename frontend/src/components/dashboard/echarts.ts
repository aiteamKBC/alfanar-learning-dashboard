import { BarChart } from 'echarts/charts'
import { GridComponent, LegendComponent, TooltipComponent } from 'echarts/components'
import { use } from 'echarts/core'
import { SVGRenderer } from 'echarts/renderers'

use([BarChart, GridComponent, LegendComponent, TooltipComponent, SVGRenderer])

export const INK = { primary: '#0f172a', secondary: '#475569', muted: '#64748b', grid: '#eef0f3', axis: '#d1d5db' }

export const baseTooltip = {
  backgroundColor: '#ffffff',
  borderColor: '#e5e7eb',
  borderWidth: 1,
  padding: [8, 12],
  textStyle: { color: INK.primary, fontSize: 12 },
  // Keep the tooltip inside the chart (never cut off by card edges or the viewport) and let long
  // module names wrap instead of widening the box past small screens.
  confine: true,
  extraCssText: 'box-shadow: 0 8px 24px rgba(15,23,42,.08); border-radius: 8px; max-width: min(320px, calc(100vw - 32px)); white-space: normal; overflow-wrap: anywhere;',
}

export const textStyle = { fontFamily: 'Inter, "Segoe UI", system-ui, sans-serif', color: INK.secondary }

export function escapeHtml(s: string) {
  return s.replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c] as string)
}

export function shortLabel(s: string, max = 26) {
  const t = s.replace(/^Al Fanar\s*[-–]\s*/i, '').replace(/^Al Fanar-/i, '')
  return t.length > max ? `${t.slice(0, max - 1)}…` : t
}
