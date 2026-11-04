<script setup lang="ts">
import { ref } from 'vue'

defineProps<{ text: string; label?: string }>()
const open = ref(false)
</script>

<template>
  <span class="relative inline-flex" @mouseenter="open = true" @mouseleave="open = false">
    <button
      type="button"
      class="focus-ring grid h-4 w-4 place-items-center rounded-full border border-gray-300 text-[10px] font-semibold leading-none text-ink-3 hover:border-ink-3 hover:text-ink"
      :aria-label="label ?? 'Metric definition'"
      :aria-expanded="open"
      @focus="open = true" @blur="open = false" @click.stop="open = !open"
    >i</button>
    <span
      v-if="open" role="tooltip"
      class="absolute left-1/2 top-6 z-40 w-72 -translate-x-1/2 rounded-lg border border-line bg-white p-3 text-left text-xs font-normal normal-case leading-relaxed tracking-normal text-ink-2 shadow-lg"
    >{{ text }}</span>
  </span>
</template>
