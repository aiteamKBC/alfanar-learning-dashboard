<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import type { LearnerRow } from '@/api/types'
import Avatar from '@/components/common/Avatar.vue'
import Icon from '@/components/common/Icon.vue'
import ProgressBar from '@/components/common/ProgressBar.vue'
import { attendanceHours, dateTime, pct } from '@/utils/format'
import { completionLabel } from '@/api/learnerDirectory'
import { shortLabel } from '@/components/dashboard/echarts'
import { scrollEdges } from './tableScroll'

defineProps<{ rows: LearnerRow[]; ordering: string }>()
const emit = defineEmits<{ sort: [key: string] }>()
const router = useRouter()
const scroller = ref<HTMLDivElement | null>(null)
const table = ref<HTMLTableElement | null>(null)
const edges = ref({ left: false, right: false })
let observer: ResizeObserver | undefined

function updateScroll() {
  if (scroller.value) edges.value = scrollEdges(scroller.value)
}
function scrollTable(direction: number) {
  const element = scroller.value
  if (element) element.scrollBy({ left: direction * element.clientWidth * 0.75 })
}
onMounted(() => {
  observer = new ResizeObserver(updateScroll)
  if (scroller.value) observer.observe(scroller.value)
  if (table.value) observer.observe(table.value)
  window.addEventListener('resize', updateScroll)
  window.visualViewport?.addEventListener('resize', updateScroll)
  updateScroll()
})
onBeforeUnmount(() => {
  observer?.disconnect()
  window.removeEventListener('resize', updateScroll)
  window.visualViewport?.removeEventListener('resize', updateScroll)
})

const cols = [
  { key: 'name', label: 'Learner', sortable: true },
  { key: 'modules', label: 'Enrolled modules', sortable: true },
  { key: 'progress', label: 'Mean LMS progress', sortable: true },
  { key: 'attendance', label: 'Attendance evidence', sortable: true },
  { key: 'attendance_hours', label: 'Total attendance hours', sortable: true },
  { key: 'lessons', label: 'Materials completed', sortable: true },
  { key: 'quizzes', label: 'Quizzes attempted', sortable: true },
  { key: 'quiz_score', label: 'Average Quiz Score', sortable: true },
  { key: 'assignments', label: 'Assignments', sortable: false },
  { key: 'last', label: 'Last activity', sortable: false },
]
function arrow(ordering: string, key: string) {
  if (ordering === key) return '↑'
  if (ordering === `-${key}`) return '↓'
  return ''
}
function open(id: number) { router.push(`/learners/${id}`) }
</script>

<template>
  <div class="min-w-0 max-w-full">
    <div v-if="edges.left || edges.right" class="mb-2 flex justify-end gap-2 text-xs text-ink-2">
      <button v-if="edges.left" type="button" class="focus-ring inline-flex min-h-8 items-center gap-1 rounded-md px-2 hover:bg-navy-soft" aria-label="Scroll table left" @click="scrollTable(-1)">
        <Icon name="chevron" :size="14" class="rotate-180" /> Scroll back
      </button>
      <button v-if="edges.right" type="button" class="focus-ring inline-flex min-h-8 items-center gap-1 rounded-md px-2 hover:bg-navy-soft" aria-label="Scroll table right" @click="scrollTable(1)">
        Scroll to see more <Icon name="chevron" :size="14" />
      </button>
    </div>
    <div ref="scroller" class="card directory-scroll focus-ring max-w-full overflow-x-auto" tabindex="0" role="region" aria-label="Learner directory table" @scroll.passive="updateScroll">
    <table ref="table" class="w-full min-w-[1540px] text-sm" data-learner-directory>
      <thead class="border-b border-line bg-subtle/70 text-left">
        <tr>
          <th v-for="c in cols" :key="c.key" class="eyebrow whitespace-nowrap px-4 py-3" :class="{ 'sticky left-0 z-10 w-40 min-w-40 max-w-40 bg-[#f6f5f1] sm:w-auto sm:min-w-0 sm:max-w-none': c.key === 'name' }" :aria-sort="ordering === c.key ? 'ascending' : ordering === `-${c.key}` ? 'descending' : undefined">
            <button v-if="c.sortable" class="focus-ring inline-flex items-center gap-1 rounded uppercase hover:text-ink" :class="ordering.replace('-', '') === c.key ? 'text-navy' : ''" @click="emit('sort', c.key)">
              {{ c.label }} <span class="w-3 text-navy">{{ arrow(ordering, c.key) }}</span>
            </button>
            <span v-else>{{ c.label }}</span>
          </th>
        </tr>
      </thead>
      <tbody class="divide-y divide-line">
        <tr v-for="r in rows" :key="r.learner_id" :data-learner-id="r.learner_id" class="group cursor-pointer transition-colors hover:bg-navy-soft/40" @click="open(r.learner_id)">
          <td class="sticky left-0 z-10 w-40 min-w-40 max-w-40 bg-white px-4 py-3 group-hover:bg-[#f2f5fa] sm:w-auto sm:min-w-0 sm:max-w-none">
            <div class="flex items-center gap-3">
              <span class="hidden sm:block"><Avatar :name="r.name" :size="34" /></span>
              <div class="min-w-0">
                <RouterLink :to="`/learners/${r.learner_id}`" class="focus-ring block max-w-32 truncate rounded font-semibold text-ink hover:text-navy sm:max-w-56" :title="r.name" @click.stop>{{ r.name }}</RouterLink>
                <div class="text-[11.5px] text-ink-3 tabular">ID {{ r.learner_id }}</div>
              </div>
            </div>
          </td>
          <td class="px-4 py-3">
            <div class="grid max-w-[250px] grid-cols-2 gap-1">
              <span v-for="e in r.enrollments" :key="e.course_id" class="truncate rounded-md border border-line bg-white px-1.5 py-0.5 text-[11px] text-ink-2" :title="`${e.course}: ${pct(e.progress_pct, 0)}`">{{ shortLabel(e.course, 15) }}</span>
            </div>
          </td>
          <td class="w-48 px-4 py-3" data-progress><ProgressBar :value="r.mean_progress" /></td>
          <td class="w-44 px-4 py-3" data-attendance-evidence>
            <template v-if="r.attendance.available">
              <ProgressBar :value="r.attendance.pct" color="#142c52" />
              <div class="mt-0.5 text-[11px] text-ink-3 tabular" data-attendance-counts :title="`Confirmed attendance / officially delivered lectures. ${r.attendance.unknown ?? 0} unknown observations.`">
                {{ r.attendance.attended }} / {{ r.attendance.expected }} lectures
              </div>
            </template>
            <span v-else class="text-[12px] text-ink-3" title="No reliable nonzero official lecture total is available">N/A</span>
          </td>
          <td class="whitespace-nowrap px-4 py-3 tabular" data-attendance-hours>{{ attendanceHours(r.verified_attendance_seconds) }}</td>
          <td class="whitespace-nowrap px-4 py-3 tabular font-medium text-ink" data-materials>{{ completionLabel(r.lessons_completed, r.lessons_known, r.lessons_completion_pct) }}</td>
          <td class="whitespace-nowrap px-4 py-3 tabular" data-quizzes>{{ completionLabel(r.quizzes_attempted, r.quizzes_known, r.quiz_completion_pct) }}</td>
          <td class="px-4 py-3 tabular" data-average-score>
            <span v-if="r.mean_latest_score !== null" class="font-medium text-ink">{{ pct(r.mean_latest_score) }}</span>
            <span v-else class="text-ink-3" title="No graded quiz attempts recorded">N/A</span>
            <div v-if="r.quizzes_graded" class="text-[11px] text-ink-3" data-quiz-passed>{{ r.quizzes_passed_latest }} / {{ r.quizzes_graded }} passed</div>
          </td>
          <td class="px-4 py-3 tabular" data-assignments><template v-if="r.assignments_known"><span class="font-medium text-ink">{{ r.assignments_submitted }}</span><span class="text-ink-3"> / {{ r.assignments_known }}</span></template><span v-else class="text-ink-3">N/A</span></td>
          <td class="px-4 py-3 text-ink-2" data-last-activity>
            <div class="flex items-center justify-between gap-2">
              <span v-if="r.last_activity" class="whitespace-nowrap">{{ dateTime(r.last_activity, false) }}</span>
              <span v-else class="whitespace-nowrap text-ink-3">No recorded activity</span>
              <Icon name="chevron" :size="15" class="text-ink-3 opacity-0 transition group-hover:opacity-100" />
            </div>
          </td>
        </tr>
      </tbody>
    </table>
    </div>
  </div>
</template>

<style scoped>
.directory-scroll {
  scroll-behavior: smooth;
  overscroll-behavior-x: contain;
  scrollbar-width: auto;
  scrollbar-color: #8a95a6 #f3f2ee;
}
.directory-scroll::-webkit-scrollbar { height: 12px; }
.directory-scroll::-webkit-scrollbar-track { background: #f3f2ee; }
.directory-scroll::-webkit-scrollbar-thumb {
  background: #8a95a6;
  border: 3px solid #f3f2ee;
  border-radius: 6px;
}
@media (prefers-reduced-motion: reduce) {
  .directory-scroll { scroll-behavior: auto; }
}
</style>
