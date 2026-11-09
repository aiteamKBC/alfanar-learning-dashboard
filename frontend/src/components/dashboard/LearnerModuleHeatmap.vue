<script setup lang="ts">
import { computed, ref } from 'vue'
import type { Charts, Definitions } from '@/api/types'
import InfoTip from '@/components/common/InfoTip.vue'
import ProgressBar from '@/components/common/ProgressBar.vue'
import { attendanceHours, pct, rampColor, rampText, ratio } from '@/utils/format'
import { shortLabel } from './echarts'

const props = defineProps<{ matrix: Charts['learner_module_matrix']; selectedModule?: string; definitions?: Definitions }>()
const search = ref('')
const selected = computed(() => props.matrix.modules.find((m) => String(m.course_id) === props.selectedModule))
const rows = computed(() => {
  const q = search.value.trim().toLowerCase()
  const filtered = q ? props.matrix.rows.filter((r) => r.name.toLowerCase().includes(q) || String(r.learner_id).includes(q)) : props.matrix.rows
  return filtered.map((r) => ({ ...r, metrics: selected.value ? r.module_metrics?.[selected.value.course_id] : undefined }))
})
function completion(done: number | undefined, total: number | undefined) {
  return done === undefined || !total ? 'N/A' : ratio(done, total)
}
</script>

<template>
  <div>
    <div class="mb-3 flex flex-wrap items-center gap-3">
      <input v-model="search" type="search" placeholder="Filter learners…" class="focus-ring h-8 w-56 rounded-md border border-line px-2.5 text-sm" aria-label="Filter learners in matrix" />
      <span class="text-xs text-ink-3">{{ rows.length }} learners · cell = LMS-reported progress · blank = not enrolled</span>
    </div>
    <div class="max-h-[560px] overflow-auto rounded-lg border border-line">
      <table v-if="selected" class="w-full min-w-[1120px] border-separate border-spacing-0 text-[12px]" data-module-performance :data-course-id="selected.course_id">
        <thead class="sticky top-0 z-20 bg-white text-ink-3">
          <tr>
            <th scope="col" class="sticky left-0 z-30 border-b border-line bg-white px-3 py-2 text-left font-medium">Learner</th>
            <th scope="col" class="border-b border-line px-3 py-2 font-medium">Progress</th>
            <th scope="col" class="border-b border-line px-2 py-2 font-medium">Quizzes completed <InfoTip v-if="definitions?.quiz_completion" :text="definitions.quiz_completion" /><span class="block text-[10px]">Completed / total</span></th>
            <th scope="col" class="max-w-28 border-b border-line px-2 py-2 font-medium">Mean latest quiz score</th>
            <th scope="col" class="border-b border-line px-2 py-2 font-medium">Videos<span class="block text-[10px]">Completed / total</span></th>
            <th scope="col" class="border-b border-line px-2 py-2 font-medium">PDFs<span class="block text-[10px]">Completed / total</span></th>
            <th scope="col" class="border-b border-line px-2 py-2 font-medium">Readings<span class="block text-[10px]">Completed / total</span></th>
            <th scope="col" class="border-b border-line px-2 py-2 font-medium">Assignments<span class="block text-[10px]">Submitted / total</span></th>
            <th scope="col" class="border-b border-line px-3 py-2 font-medium">Attendance Hours</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in rows" :key="r.learner_id" :data-learner-id="r.learner_id" class="group hover:bg-gray-50">
            <th scope="row" class="sticky left-0 z-10 border-b border-line bg-white px-3 py-2 text-left font-normal group-hover:bg-gray-50">
              <RouterLink :to="`/learners/${r.learner_id}?module=${selected.course_id}`" class="focus-ring block max-w-[180px] truncate rounded text-ink hover:text-series-1 hover:underline" :title="r.name">{{ r.name }}</RouterLink>
            </th>
            <td class="border-b border-line px-3 py-2">
              <RouterLink :to="`/learners/${r.learner_id}?module=${selected.course_id}`" class="focus-ring block w-32 rounded" :aria-label="`Open ${r.name}, ${selected.course}`">
                <ProgressBar v-if="r.progress[selected.course_id] != null" :value="r.progress[selected.course_id]" />
                <span v-else class="block text-center text-ink-3">N/A</span>
              </RouterLink>
            </td>
            <td data-quizzes class="border-b border-line px-2 py-2 text-center tabular whitespace-nowrap">{{ completion(r.metrics?.quizzes_attempted, r.metrics?.quizzes_known) }}</td>
            <td data-quiz-score class="border-b border-line px-2 py-2 text-center tabular">{{ r.metrics?.mean_latest_score == null ? 'N/A' : pct(r.metrics.mean_latest_score) }}</td>
            <td v-for="kind in (['video', 'pdf', 'reading'] as const)" :key="kind" :data-material="kind" class="border-b border-line px-2 py-2 text-center tabular whitespace-nowrap">{{ completion(r.metrics?.lessons_by_type[kind]?.completed, r.metrics?.lessons_by_type[kind]?.known) }}</td>
            <td data-assignments class="border-b border-line px-2 py-2 text-center tabular whitespace-nowrap">{{ completion(r.metrics?.assignments_submitted, r.metrics?.assignments_known) }}</td>
            <td data-attendance-hours class="border-b border-line px-3 py-2 text-center tabular whitespace-nowrap">{{ attendanceHours(r.metrics?.verified_attendance_seconds) }}</td>
          </tr>
          <tr v-if="!rows.length"><td colspan="9" class="px-3 py-6 text-center text-ink-3">No learners match this filter.</td></tr>
        </tbody>
      </table>
      <table v-else class="w-full min-w-[640px] border-separate border-spacing-0 text-sm">
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
