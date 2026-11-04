<script setup lang="ts">
/** Loading skeleton, error, or empty state for a panel. */
withDefaults(defineProps<{
  loading?: boolean
  error?: string | null
  empty?: boolean
  emptyTitle?: string
  emptyText?: string
  height?: string
}>(), { height: '16rem', emptyTitle: 'No data for this selection', emptyText: 'Try clearing or changing the filters.' })
defineEmits<{ retry: [] }>()
</script>

<template>
  <div v-if="loading" class="skeleton w-full" :style="{ height }" aria-busy="true" aria-label="Loading" />
  <div v-else-if="error" class="grid w-full place-items-center rounded-lg border border-red-200 bg-red-50/60 p-6 text-center" :style="{ minHeight: height }" role="alert">
    <div>
      <p class="text-sm font-medium text-red-800">Could not load data</p>
      <p class="mt-1 text-sm text-red-700/80">{{ error }}</p>
      <button class="focus-ring mt-3 rounded-md border border-red-200 bg-white px-3 py-1.5 text-sm text-red-800 hover:bg-red-50" @click="$emit('retry')">Retry</button>
    </div>
  </div>
  <div v-else-if="empty" class="grid w-full place-items-center rounded-lg border border-dashed border-line p-6 text-center" :style="{ minHeight: height }">
    <div>
      <p class="text-sm font-medium text-ink-2">{{ emptyTitle }}</p>
      <p class="mt-1 text-sm text-ink-3">{{ emptyText }}</p>
    </div>
  </div>
  <slot v-else />
</template>
