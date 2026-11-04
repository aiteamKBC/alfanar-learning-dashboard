<script setup lang="ts">
import { computed } from 'vue'

const props = withDefaults(defineProps<{ value: number | null | undefined; size?: number; stroke?: number; color?: string; label?: string }>(), {
  size: 76, stroke: 7, color: '#1baf7a',
})
const r = computed(() => (props.size - props.stroke) / 2)
const c = computed(() => 2 * Math.PI * r.value)
const v = computed(() => (props.value === null || props.value === undefined ? 0 : Math.max(0, Math.min(100, props.value))))
</script>

<template>
  <div class="relative inline-grid place-items-center" :style="{ width: `${size}px`, height: `${size}px` }" role="img" :aria-label="label ?? `${value ?? 'No data'}%`">
    <svg :width="size" :height="size" class="-rotate-90">
      <circle :cx="size / 2" :cy="size / 2" :r="r" fill="none" stroke="#eeece6" :stroke-width="stroke" />
      <circle :cx="size / 2" :cy="size / 2" :r="r" fill="none" :stroke="color" :stroke-width="stroke" stroke-linecap="round"
        :stroke-dasharray="c" :stroke-dashoffset="c * (1 - v / 100)" style="transition: stroke-dashoffset .8s cubic-bezier(.2,.7,.2,1)" />
    </svg>
    <span class="font-heading absolute text-[15px] font-semibold text-ink tabular">{{ value === null || value === undefined ? '—' : `${Math.round(value)}%` }}</span>
  </div>
</template>
