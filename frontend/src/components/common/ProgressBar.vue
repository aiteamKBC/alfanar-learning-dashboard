<script setup lang="ts">
import { computed } from 'vue'
import { pct } from '@/utils/format'

const props = withDefaults(defineProps<{ value: number | null | undefined; showLabel?: boolean; color?: string }>(), {
  showLabel: true, color: '#1baf7a',
})
const width = computed(() => (props.value === null || props.value === undefined ? 0 : Math.min(100, Math.max(0, props.value))))
</script>

<template>
  <div class="flex items-center gap-2">
    <div class="h-1.5 min-w-16 flex-1 overflow-hidden rounded-full bg-gray-100" role="progressbar" :aria-valuenow="value ?? undefined" aria-valuemin="0" aria-valuemax="100">
      <div class="h-full rounded-full" :style="{ width: `${width}%`, background: color }" />
    </div>
    <span v-if="showLabel" class="tabular w-14 text-right text-sm text-ink" :class="{ 'text-ink-3': value === null || value === undefined }">{{ pct(value) }}</span>
  </div>
</template>
