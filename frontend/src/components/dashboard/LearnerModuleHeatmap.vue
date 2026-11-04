<script setup lang="ts">
import { computed, ref } from 'vue'
import type { Charts } from '@/api/types'
import { pct, rampColor, rampText } from '@/utils/format'
import { shortLabel } from './echarts'

const props = defineProps<{ matrix: Charts['learner_module_matrix'] }>()
const search = ref('')
const rows = computed(() => {
  const q = search.value.trim().toLowerCase()
  return q ? props.matrix.rows.filter((r) => r.name.toLowerCase().includes(q) || String(r.learner_id).includes(q)) : props.matrix.rows
})
</script>

<template>
  <div>
    <div class="mb-3 flex flex-wrap items-center gap-3">
      <input v-model="search" type="search" placeholder="Filter learners…" class="focus-ring h-8 w-56 rounded-md border border-line px-2.5 text-sm" aria-label="Filter learners in matrix" />
      <span class="text-xs text-ink-3">{{ rows.length }} learners · cell = LMS-reported progress · blank = not enrolled</span>
    </div>
    <div class="max-h-[560px] overflow-auto rounded-lg border border-line">
      <table class="w-full min-w-[640px] border-separate border-spacing-0 text-sm">
        <thead class="sticky top-0 z-10 bg-white">
          <tr class="text-xs text-ink-3">
            <th class="sticky left-0 z-20 border-b border-line bg-white px-3 py-2 text-left font-medium">Learner</th>
            <th v-for="m in matrix.modules" :key="m.course_id" class="border-b border-line px-2 py-2 text-center font-medium" :title="m.course">{{ shortLabel(m.course, 22) }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in rows" :key="r.learner_id" class="group">
            <th scope="row" class="sticky left-0 z-10 border-b border-line bg-white px-3 py-1.5 text-left font-normal group-hover:bg-gray-50">
              <RouterLink :to="`/learners/${r.learner_id}`" class="focus-ring rounded text-ink hover:text-series-1 hover:underline">{{ r.name }}</RouterLink>
            </th>
            <td v-for="m in matrix.modules" :key="m.course_id" class="border-b border-line p-[3px]">
              <RouterLink
                v-if="m.course_id in r.progress"
                :to="`/learners/${r.learner_id}?module=${m.course_id}`"
                class="focus-ring tabular flex h-7 items-center justify-center rounded text-xs font-semibold"
                :style="r.progress[m.course_id] === null ? { background: '#f8f9fb', color: '#94a3b8' } : { background: rampColor(r.progress[m.course_id] as number), color: rampText(r.progress[m.course_id] as number) }"
                :title="`${r.name} · ${m.course}: ${pct(r.progress[m.course_id], 0)}`"
              >{{ pct(r.progress[m.course_id], 0) }}</RouterLink>
              <span v-else class="block h-7" aria-label="Not enrolled" />
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
