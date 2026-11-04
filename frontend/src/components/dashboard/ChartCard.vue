<script setup lang="ts">
import { ref } from 'vue'
import InfoTip from '@/components/common/InfoTip.vue'

/** Card wrapper for a chart with a definition tooltip and an accessible table view toggle. */
defineProps<{
  title: string
  subtitle?: string
  info?: string
  table?: { columns: string[]; rows: (string | number)[][] }
}>()
const showTable = ref(false)
</script>

<template>
  <section class="card fade-up flex min-w-0 flex-col p-5">
    <header class="mb-4 flex items-start gap-3">
      <div class="min-w-0">
        <h3 class="flex items-center gap-2 text-[15px] font-semibold text-ink">
          {{ title }}
          <InfoTip v-if="info" :text="info" :label="`About: ${title}`" />
        </h3>
        <p v-if="subtitle" class="mt-0.5 text-sm text-ink-3">{{ subtitle }}</p>
      </div>
      <div class="ml-auto flex shrink-0 items-center gap-2">
        <slot name="actions" />
        <button
          v-if="table"
          class="focus-ring rounded-md border border-line px-2 py-1 text-xs font-medium text-ink-2 hover:bg-gray-50"
          :aria-pressed="showTable" @click="showTable = !showTable"
        >{{ showTable ? 'Chart' : 'Table' }}</button>
      </div>
    </header>
    <div v-if="showTable && table" class="max-h-96 overflow-auto rounded-lg border border-line">
      <table class="w-full text-sm">
        <thead class="sticky top-0 bg-gray-50 text-left text-xs font-medium text-ink-3">
          <tr><th v-for="c in table.columns" :key="c" class="px-3 py-2">{{ c }}</th></tr>
        </thead>
        <tbody class="divide-y divide-line">
          <tr v-for="(r, i) in table.rows" :key="i">
            <td v-for="(v, j) in r" :key="j" class="tabular px-3 py-2" :class="j === 0 ? 'text-ink' : 'text-ink-2'">{{ v }}</td>
          </tr>
        </tbody>
      </table>
    </div>
    <div v-else class="min-w-0 flex-1"><slot /></div>
    <footer v-if="$slots.footer" class="mt-3 border-t border-line pt-3 text-xs text-ink-3"><slot name="footer" /></footer>
  </section>
</template>
