<script setup lang="ts">
import { computed } from 'vue'

/**
 * Circular ring. Percentage mode (default) fills by `value` (0–100).
 * Count mode (`count` set) shows a whole number with a caption and a full ring,
 * for quantities that have no meaningful denominator.
 */
const props = withDefaults(defineProps<{
  value?: number | null
  count?: number | null
  caption?: string
  size?: number
  stroke?: number
  color?: string
  label?: string
}>(), { value: null, count: undefined, size: 76, stroke: 7, color: '#1baf7a' })

const isCount = computed(() => props.count !== undefined)
const r = computed(() => (props.size - props.stroke) / 2)
const c = computed(() => 2 * Math.PI * r.value)
const fill = computed(() => {
  if (isCount.value) return props.count ? 100 : 0
  return props.value === null || props.value === undefined ? 0 : Math.max(0, Math.min(100, props.value))
})
const text = computed(() => {
  if (isCount.value) return props.count === null || props.count === undefined ? '—' : String(props.count)
  return props.value === null || props.value === undefined ? '—' : `${Math.round(props.value)}%`
})
</script>

<template>
  <div class="relative inline-grid place-items-center" :style="{ width: `${size}px`, height: `${size}px` }" role="img" :aria-label="label ?? text">
    <svg :width="size" :height="size" class="-rotate-90">
      <circle :cx="size / 2" :cy="size / 2" :r="r" fill="none" stroke="#eeece6" :stroke-width="stroke" />
      <circle :cx="size / 2" :cy="size / 2" :r="r" fill="none" :stroke="color" :stroke-width="stroke" stroke-linecap="round"
        :stroke-dasharray="c" :stroke-dashoffset="c * (1 - fill / 100)" style="transition: stroke-dashoffset .8s cubic-bezier(.2,.7,.2,1)" />
    </svg>
    <span class="absolute flex flex-col items-center leading-none">
      <span class="font-heading font-semibold text-ink tabular" :class="isCount ? 'text-[19px]' : 'text-[15px]'">{{ text }}</span>
      <span v-if="isCount && caption" class="mt-0.5 text-[8.5px] font-semibold uppercase tracking-[0.12em] text-ink-3">{{ caption }}</span>
    </span>
  </div>
</template>
