<script setup lang="ts">
import { computed } from 'vue'

/** Status label with icon + text (never colour alone). */
const props = defineProps<{ status: string | null | undefined }>()

const meta = computed(() => {
  const s = (props.status ?? '').toLowerCase()
  if (s === 'passed' || s === 'success' || s === 'recorded' || s === 'submitted')
    return { cls: 'bg-emerald-50 text-emerald-800 ring-emerald-200', icon: '✓', label: s.charAt(0).toUpperCase() + s.slice(1) }
  if (s === 'failed')
    return { cls: 'bg-red-50 text-red-800 ring-red-200', icon: '✕', label: 'Failed' }
  if (s === 'pending' || s === 'running')
    return { cls: 'bg-amber-50 text-amber-800 ring-amber-200', icon: '◷', label: s === 'pending' ? 'Pending' : 'Running' }
  if (s === 'draft')
    return { cls: 'bg-gray-100 text-gray-700 ring-gray-200', icon: '✎', label: 'Draft' }
  if (!s) return { cls: 'bg-white text-ink-3 ring-gray-200', icon: '–', label: 'No record' }
  return { cls: 'bg-gray-100 text-gray-700 ring-gray-200', icon: '•', label: s }
})
</script>

<template>
  <span class="inline-flex items-center gap-1 whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset" :class="meta.cls">
    <span aria-hidden="true">{{ meta.icon }}</span>{{ meta.label }}
  </span>
</template>
