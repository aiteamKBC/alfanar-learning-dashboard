<script setup lang="ts">
import { useRouter } from 'vue-router'
import type { LearnerRow } from '@/api/types'
import Avatar from '@/components/common/Avatar.vue'
import Icon from '@/components/common/Icon.vue'
import ProgressBar from '@/components/common/ProgressBar.vue'
import { dateTime, hours, num, pct } from '@/utils/format'
import { shortLabel } from '@/components/dashboard/echarts'

defineProps<{ rows: LearnerRow[]; ordering: string }>()
const emit = defineEmits<{ sort: [key: string] }>()
const router = useRouter()

const cols = [
  { key: 'name', label: 'Learner', sortable: true },
  { key: 'modules', label: 'Enrolled modules', sortable: true },
  { key: 'progress', label: 'Mean LMS progress', sortable: true },
  { key: 'attendance', label: 'Live attendance', sortable: true },
  { key: 'lessons', label: 'Materials completed', sortable: true },
  { key: 'quizzes', label: 'Quizzes attempted', sortable: true },
  { key: 'quiz_score', label: 'Latest quiz score', sortable: true },
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
  <div class="card matrix-scroll overflow-x-auto">
    <table class="w-full min-w-[1280px] text-sm">
      <thead class="border-b border-line bg-subtle/70 text-left">
        <tr>
          <th v-for="c in cols" :key="c.key" class="eyebrow whitespace-nowrap px-4 py-3" :class="{ 'sticky left-0 z-10 bg-[#f6f5f1]': c.key === 'name' }" :aria-sort="ordering === c.key ? 'ascending' : ordering === `-${c.key}` ? 'descending' : undefined">
            <button v-if="c.sortable" class="focus-ring inline-flex items-center gap-1 rounded uppercase hover:text-ink" :class="ordering.replace('-', '') === c.key ? 'text-navy' : ''" @click="emit('sort', c.key)">
              {{ c.label }} <span class="w-3 text-navy">{{ arrow(ordering, c.key) }}</span>
            </button>
            <span v-else>{{ c.label }}</span>
          </th>
        </tr>
      </thead>
      <tbody class="divide-y divide-line">
        <tr v-for="r in rows" :key="r.learner_id" class="group cursor-pointer transition-colors hover:bg-navy-soft/40" @click="open(r.learner_id)">
          <td class="sticky left-0 z-10 bg-white px-4 py-3 group-hover:bg-[#f2f5fa]">
            <div class="flex items-center gap-3">
              <Avatar :name="r.name" :size="34" />
              <div class="min-w-0">
                <RouterLink :to="`/learners/${r.learner_id}`" class="focus-ring block max-w-56 truncate rounded font-semibold text-ink hover:text-navy" :title="r.name" @click.stop>{{ r.name }}</RouterLink>
                <div class="text-[11.5px] text-ink-3 tabular">ID {{ r.learner_id }}</div>
              </div>
            </div>
          </td>
          <td class="px-4 py-3">
            <div class="grid max-w-[250px] grid-cols-2 gap-1">
              <span v-for="e in r.enrollments" :key="e.course_id" class="truncate rounded-md border border-line bg-white px-1.5 py-0.5 text-[11px] text-ink-2" :title="`${e.course}: ${pct(e.progress_pct, 0)}`">{{ shortLabel(e.course, 15) }}</span>
            </div>
          </td>
          <td class="w-48 px-4 py-3"><ProgressBar :value="r.mean_progress" /></td>
          <td class="w-44 px-4 py-3">
            <template v-if="r.attendance.available">
              <ProgressBar :value="r.attendance.pct" color="#142c52" />
              <div class="mt-0.5 text-[11px] text-ink-3 tabular" :title="`${r.attendance.modules_covered} of ${r.attendance.modules_total} modules have Teams data`">
                {{ r.attendance.attended }} / {{ r.attendance.expected }} sessions · {{ hours(r.attendance.live_seconds) }}
              </div>
            </template>
            <span v-else-if="r.attendance.modules_without_teams_identity" class="text-[12px] text-amber-800" title="No matched Microsoft Teams identity for this learner: attendance unknown, not counted as absent">Unknown · no Teams match</span>
            <span v-else class="text-[12px] text-ink-3" title="No mapped Teams meeting for this learner's modules">Not available</span>
          </td>
          <td class="px-4 py-3 tabular font-medium text-ink">{{ num(r.lessons_completed) }}</td>
          <td class="px-4 py-3 tabular"><span class="font-medium text-ink">{{ num(r.quizzes_attempted) }}</span><span class="text-ink-3"> / {{ num(r.quizzes_known) }}</span></td>
          <td class="px-4 py-3 tabular">
            <span v-if="r.mean_latest_score !== null" class="font-medium text-ink">{{ pct(r.mean_latest_score) }}</span>
            <span v-else class="text-ink-3" title="No graded quiz attempts recorded">No attempts</span>
            <div v-if="r.quizzes_graded" class="text-[11px] text-ink-3">{{ r.quizzes_passed_latest }} / {{ r.quizzes_graded }} passed</div>
          </td>
          <td class="px-4 py-3 tabular"><span class="font-medium text-ink">{{ r.assignments_submitted }}</span><span class="text-ink-3"> / {{ r.assignments_known }}</span></td>
          <td class="px-4 py-3 text-ink-2">
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
</template>
