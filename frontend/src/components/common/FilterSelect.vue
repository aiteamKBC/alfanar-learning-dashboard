<script setup lang="ts">
defineProps<{ label: string; options: { value: string | number; label: string }[]; allLabel?: string }>()
const model = defineModel<string>({ default: '' })
</script>

<template>
  <label class="flex w-full min-w-0 flex-col gap-1 sm:w-auto">
    <span class="eyebrow">{{ label }}</span>
    <select
      v-model="model"
      :aria-label="label"
      class="focus-ring h-9 w-full rounded-md sm:w-auto sm:min-w-44 sm:max-w-72 border border-line bg-white px-2.5 pr-8 text-sm text-ink hover:border-gray-300"
    >
      <option value="">{{ allLabel ?? 'All' }}</option>
      <option v-for="o in options" :key="o.value" :value="String(o.value)">{{ o.label }}</option>
    </select>
  </label>
</template>
