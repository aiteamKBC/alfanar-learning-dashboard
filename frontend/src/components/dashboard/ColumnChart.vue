<script setup lang="ts">
import { computed } from 'vue'
import VChart from 'vue-echarts'
import { baseTooltip, escapeHtml, INK, textStyle } from './echarts'

const props = withDefaults(defineProps<{
  categories: string[]
  data: number[]
  color?: string
  seriesName: string
  total?: number
  height?: number
}>(), { color: '#2a78d6', height: 260 })

const option = computed(() => ({
  textStyle,
  animationDuration: 400,
  grid: { left: 8, right: 8, top: 24, bottom: 8, containLabel: true },
  tooltip: {
    ...baseTooltip, trigger: 'axis', axisPointer: { type: 'shadow', shadowStyle: { color: 'rgba(15,23,42,0.04)' } },
    formatter: (p: { dataIndex: number; value: number }[]) => {
      const i = p[0].dataIndex
      const share = props.total ? ` (${((100 * p[0].value) / props.total).toFixed(1)}%)` : ''
      return `<div style="font-weight:600">${escapeHtml(props.categories[i])}</div>${escapeHtml(props.seriesName)}: <b>${p[0].value}</b>${share}`
    },
  },
  xAxis: { type: 'category', data: props.categories, axisTick: { show: false }, axisLine: { lineStyle: { color: INK.axis } }, axisLabel: { color: INK.secondary, fontSize: 12 } },
  yAxis: { type: 'value', minInterval: 1, axisLabel: { color: INK.muted, fontSize: 11 }, splitLine: { lineStyle: { color: INK.grid } } },
  series: [{
    name: props.seriesName, type: 'bar', data: props.data, barMaxWidth: 44,
    itemStyle: { color: props.color, borderRadius: [4, 4, 0, 0] },
    label: { show: true, position: 'top', color: INK.secondary, fontSize: 11 },
  }],
}))
</script>

<template>
  <VChart :option="option" :style="{ height: `${height}px`, width: '100%' }" autoresize />
</template>
