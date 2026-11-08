<script setup lang="ts">
import { computed } from 'vue'
import VChart from 'vue-echarts'
import { baseTooltip, escapeHtml, INK, textStyle } from './echarts'

/**
 * Horizontal bar chart (single axis). One or more series share the same unit.
 * `tooltipRows` adds context lines per category.
 */
const props = withDefaults(defineProps<{
  categories: string[]
  fullLabels?: string[]
  series: { name: string; color: string; data: (number | null)[] }[]
  max?: number
  unit?: string
  tooltipRows?: string[][]
  height?: number
  labelWidth?: number
}>(), { unit: '', height: 0, labelWidth: 150 })

const h = computed(() => props.height || Math.max(180, props.categories.length * (props.series.length > 1 ? 18 * props.series.length + 22 : 40) + 60))

const option = computed(() => ({
  textStyle,
  animationDuration: 400,
  grid: { left: 8, right: 48, top: props.series.length > 1 ? 34 : 8, bottom: 8, containLabel: true },
  legend: props.series.length > 1
    ? { top: 0, left: 0, icon: 'roundRect', itemWidth: 10, itemHeight: 10, textStyle: { color: INK.secondary, fontSize: 12 } }
    : undefined,
  tooltip: {
    ...baseTooltip,
    trigger: 'axis',
    axisPointer: { type: 'shadow', shadowStyle: { color: 'rgba(15,23,42,0.04)' } },
    position: (point: number[], _params: unknown, dom: HTMLElement, _rect: unknown, size: { viewSize: number[] }) => {
      // A wide chart can be scrolled on mobile. Keep the tooltip within the
      // currently visible part, rather than the full SVG plotting width.
      const scroll = dom.closest<HTMLElement>('.chart-scroll')
      const left = scroll?.scrollLeft ?? 0
      const width = scroll?.clientWidth ?? size.viewSize[0] ?? 0
      dom.style.maxWidth = `${Math.max(1, width - 16)}px`
      return [
        Math.max(left + 8, Math.min((point[0] ?? 0) + 12, left + width - dom.offsetWidth - 8)),
        Math.max(8, Math.min((point[1] ?? 0) + 12, (size.viewSize[1] ?? 0) - dom.offsetHeight - 8)),
      ]
    },
    formatter: (params: { dataIndex: number; seriesName: string; value: number | null; color: string }[]) => {
      const i = params[0]?.dataIndex ?? 0
      const title = escapeHtml(props.fullLabels?.[i] ?? props.categories[i])
      const lines = params.map((p) =>
        `<div style="display:flex;gap:8px;align-items:center"><span style="flex:none;width:8px;height:8px;border-radius:2px;background:${p.color}"></span>${escapeHtml(p.seriesName)}<b style="margin-left:auto;padding-left:12px">${p.value === null || p.value === undefined ? 'n/a' : p.value + props.unit}</b></div>`)
      const extra = (props.tooltipRows?.[i] ?? []).map((t) => `<div style="color:${INK.muted}">${escapeHtml(t)}</div>`)
      return `<div style="font-weight:600;margin-bottom:4px">${title}</div>${lines.join('')}${extra.length ? '<div style="margin-top:4px">' + extra.join('') + '</div>' : ''}`
    },
  },
  xAxis: {
    type: 'value', max: props.max, min: 0,
    axisLabel: { color: INK.muted, fontSize: 11, formatter: (v: number) => `${v}${props.unit}` },
    splitLine: { lineStyle: { color: INK.grid } },
  },
  yAxis: {
    type: 'category', inverse: true, data: props.categories,
    axisTick: { show: false }, axisLine: { lineStyle: { color: INK.axis } },
    axisLabel: { color: INK.secondary, fontSize: 12, width: props.labelWidth, overflow: 'truncate' },
  },
  series: props.series.map((s) => ({
    name: s.name, type: 'bar', data: s.data,
    barMaxWidth: props.series.length > 1 ? 14 : 22, barGap: '20%',
    itemStyle: { color: s.color, borderRadius: [0, 4, 4, 0] },
    label: { show: true, position: 'right', color: INK.secondary, fontSize: 11, formatter: (p: { value: number | null }) => (p.value === null || p.value === undefined ? 'n/a' : `${p.value}${props.unit}`) },
    emphasis: { focus: 'none' },
  })),
}))
</script>

<template>
  <div class="chart-scroll overflow-x-auto" role="region" aria-label="Module comparison chart; scroll horizontally on small screens" tabindex="0">
    <VChart :option="option" :style="{ height: `${h}px`, width: '100%', minWidth: '520px' }" autoresize />
  </div>
</template>
