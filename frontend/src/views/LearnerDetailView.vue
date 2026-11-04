<script setup lang="ts">
import { computed, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '@/api/client'
import type { LearnerDetail, Matrix } from '@/api/types'
import { useAsync } from '@/api/useAsync'
import Avatar from '@/components/common/Avatar.vue'
import Icon from '@/components/common/Icon.vue'
import StateBlock from '@/components/common/StateBlock.vue'
import KpiCard from '@/components/dashboard/KpiCard.vue'
import CourseCard from '@/components/learners/CourseCard.vue'
import ActivityMatrix from '@/components/matrix/ActivityMatrix.vue'
import LiveSessions from '@/components/matrix/LiveSessions.vue'
import { dateTime, hours, num, pct } from '@/utils/format'

const route = useRoute()
const router = useRouter()
const learnerId = computed(() => String(route.params.id))
const moduleId = computed(() => (typeof route.query.module === 'string' ? route.query.module : ''))

const detail = useAsync(() => api.get<LearnerDetail>(`learners/${learnerId.value}`), [learnerId])
const matrix = useAsync(
  () => (moduleId.value ? api.get<Matrix>(`learners/${learnerId.value}/courses/${moduleId.value}/matrix`) : Promise.resolve(null)),
  [learnerId, moduleId],
)

// Default to the first module so the matrix is visible immediately.
watch(() => detail.data.value, (d) => {
  if (d && !moduleId.value && d.courses.length) selectModule(d.courses[0].course_id, true)
})
function selectModule(id: number, replace = false) {
  const to = { query: { ...route.query, module: String(id) } }
  if (replace) { router.replace(to) } else { router.push(to) }
}

const s = computed(() => detail.data.value?.summary)
const defs = computed(() => detail.data.value?.definitions ?? {})
const lessonsByType = computed(() => {
  const totals: Record<string, { completed: number; known: number }> = {}
  for (const c of detail.data.value?.courses ?? [])
    for (const [k, v] of Object.entries(c.lessons_by_type)) {
      totals[k] ??= { completed: 0, known: 0 }
      totals[k].completed += v.completed
      totals[k].known += v.known
    }
  return totals
})
const materialsSub = computed(() => Object.entries(lessonsByType.value).filter(([, v]) => v.known).map(([k, v]) => `${k} ${v.completed}/${v.known}`).join(' · '))
</script>

<template>
  <div class="space-y-6">
    <nav class="flex items-center gap-1.5 text-[13px] text-ink-3" aria-label="Breadcrumb">
      <RouterLink to="/learners" class="focus-ring rounded hover:text-ink">Learners</RouterLink>
      <Icon name="chevron" :size="13" />
      <span class="truncate text-ink-2">{{ detail.data.value?.learner.name ?? `Learner ${learnerId}` }}</span>
    </nav>

    <StateBlock :loading="detail.loading.value && !detail.data.value" :error="detail.error.value" height="12rem" @retry="detail.reload">
      <template v-if="detail.data.value && s">
        <header class="hero-band fade-up relative overflow-hidden rounded-2xl px-5 py-6 text-white shadow-[var(--shadow-card)] sm:px-8">
          <div class="flex flex-wrap items-center gap-5">
            <div class="rounded-full ring-4 ring-white/15"><Avatar :name="detail.data.value.learner.name" :size="64" /></div>
            <div class="mr-auto min-w-0">
              <p class="text-[10.5px] font-semibold uppercase tracking-[0.16em] text-teal-200/90">Learner profile</p>
              <h1 class="mt-1 break-words text-[24px] font-semibold leading-tight sm:text-[28px]">{{ detail.data.value.learner.name }}</h1>
              <p class="mt-1 text-sm text-blue-100/80 tabular">Learner ID {{ detail.data.value.learner.learner_id }} · {{ s.modules }} enrolled module{{ s.modules === 1 ? '' : 's' }}</p>
            </div>
            <dl class="grid grid-cols-2 gap-x-8 gap-y-2 text-sm sm:grid-cols-3">
              <div>
                <dt class="text-[10.5px] font-semibold uppercase tracking-[0.14em] text-blue-100/70">Mean progress</dt>
                <dd class="font-heading text-[22px] font-semibold tabular">{{ pct(s.mean_progress) }}</dd>
              </div>
              <div>
                <dt class="text-[10.5px] font-semibold uppercase tracking-[0.14em] text-blue-100/70">Latest quiz avg</dt>
                <dd class="font-heading text-[22px] font-semibold tabular">{{ pct(s.mean_latest_score) }}</dd>
              </div>
              <div class="col-span-2 sm:col-span-1">
                <dt class="text-[10.5px] font-semibold uppercase tracking-[0.14em] text-blue-100/70">Last activity</dt>
                <dd class="mt-1 inline-flex items-center gap-1.5 text-[13px] font-medium"><Icon name="clock" :size="14" />{{ dateTime(s.last_activity, false) }}</dd>
              </div>
            </dl>
          </div>
        </header>

        <div class="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          <KpiCard label="Mean LMS progress" icon="gauge" tone="teal" :value="pct(s.mean_progress)" :meter="s.mean_progress" sub="Across enrolled modules" :info="defs.mean_progress" />
          <KpiCard label="Quizzes attempted" icon="quiz" tone="blue" :value="`${num(s.quizzes_attempted)} / ${num(s.quizzes_known)}`" :meter="s.quizzes_known ? (100 * s.quizzes_attempted) / s.quizzes_known : null" accent="#2a78d6" :sub="`${num(s.quiz_attempts)} attempts in total`" :info="defs.quiz_completion" />
          <KpiCard label="Latest quiz score" icon="target" tone="blue" :value="pct(s.mean_latest_score)" :meter="s.mean_latest_score" accent="#2a78d6" :sub="s.quizzes_graded ? `${s.quizzes_passed_latest} / ${s.quizzes_graded} latest attempts passed` : 'No graded attempts recorded'" :info="defs.mean_latest_score" />
          <KpiCard label="Completed materials" icon="book" tone="navy" :value="num(s.lessons_completed)" :sub="materialsSub" :info="defs.materials_completed" />
          <KpiCard label="Assignments" icon="upload" tone="neutral" :value="`${num(s.assignments_submitted)} / ${num(s.assignments_known)}`" sub="Non-draft submissions / known assignments" :info="defs.assignment_coverage" />
          <KpiCard v-if="s.attendance.available" label="Live attendance" icon="users" tone="navy" accent="#142c52"
            :value="pct(s.attendance.pct)" :meter="s.attendance.pct"
            :sub="`${s.attendance.attended} / ${s.attendance.expected} delivered sessions · ${hours(s.attendance.live_seconds)} live · ${s.attendance.modules_covered} of ${s.attendance.modules_total} modules have Teams data`"
            :info="defs.attendance" />
          <KpiCard v-else label="Live attendance" icon="users" tone="neutral" value="Not available" sub="No mapped Microsoft Teams meeting for this learner's modules" :info="defs.attendance" />
        </div>

        <section>
          <h2 class="text-[16px] font-semibold text-ink">Enrolled modules</h2>
          <p class="mb-3 text-[12.5px] text-ink-3">Select a module to open its learning activity matrix.</p>
          <div class="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            <CourseCard v-for="c in detail.data.value.courses" :key="c.course_id" :course="c" :selected="String(c.course_id) === moduleId" @select="selectModule(c.course_id)" />
          </div>
        </section>

        <section v-if="moduleId">
          <StateBlock :loading="matrix.loading.value && !matrix.data.value" :error="matrix.error.value" height="20rem" @retry="matrix.reload">
            <template v-if="matrix.data.value">
              <div class="mb-3 flex flex-wrap items-end gap-3">
                <div class="mr-auto">
                  <p class="eyebrow">Learning activity matrix</p>
                  <h2 class="mt-0.5 text-[18px] font-semibold text-ink">{{ matrix.data.value.course.title }}</h2>
                  <p class="mt-1 max-w-3xl text-[12.5px] leading-relaxed text-ink-3">{{ matrix.data.value.matrix_note }} Missing records show as “No record”, never as a failure. Select a recorded row to view its evidence.</p>
                </div>
                <div class="panel px-4 py-2 text-right">
                  <div class="eyebrow">LMS-reported module progress</div>
                  <div class="font-heading text-[22px] font-semibold text-ink tabular">{{ pct(matrix.data.value.course.progress_pct) }}</div>
                </div>
              </div>
              <LiveSessions :attendance="matrix.data.value.attendance" :definition="defs.attendance" class="mb-4" />
              <StateBlock :empty="matrix.data.value.rows.length === 0" empty-title="No activities recorded for this module" empty-text="The export contains no quizzes, lessons or assignments for this module.">
                <ActivityMatrix :matrix="matrix.data.value" />
              </StateBlock>
            </template>
          </StateBlock>
        </section>
      </template>
    </StateBlock>
  </div>
</template>
