<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, qs } from '@/api/client'
import type { Charts, FiltersResponse, Matrix, Summary } from '@/api/types'
import { withModulePerformance } from '@/api/modulePerformance'
import { useAsync } from '@/api/useAsync'
import FilterSelect from '@/components/common/FilterSelect.vue'
import Icon from '@/components/common/Icon.vue'
import StateBlock from '@/components/common/StateBlock.vue'
import ChartCard from '@/components/dashboard/ChartCard.vue'
import CompletionHeatmap from '@/components/dashboard/CompletionHeatmap.vue'
import HBarChart from '@/components/dashboard/HBarChart.vue'
import KpiCard from '@/components/dashboard/KpiCard.vue'
import LearnerModuleHeatmap from '@/components/dashboard/LearnerModuleHeatmap.vue'
import ModuleSnapshot from '@/components/dashboard/ModuleSnapshot.vue'
import { shortLabel } from '@/components/dashboard/echarts'
import { num, pct, ratio } from '@/utils/format'

const route = useRoute()
const router = useRouter()

// Filters live in the URL so views are shareable and survive reloads.
function bind(key: string) {
  return computed<string>({
    get: () => (typeof route.query[key] === 'string' ? (route.query[key] as string) : ''),
    set: (v) => router.replace({ query: { ...route.query, [key]: v || undefined } }),
  })
}
const course = bind('course')
const engagement = bind('engagement')
const activityType = bind('activity_type')
const hasFilters = computed(() => Boolean(course.value || engagement.value || activityType.value))

const filters = useAsync(() => api.get<FiltersResponse>('filters'))
const summary = useAsync(
  () => api.get<Summary>(`dashboard/summary${qs({ course: course.value, engagement: engagement.value })}`),
  [course, engagement],
)
const charts = useAsync(
  async () => {
    // Capture the module before awaiting; a newer filter request owns its own result.
    const selectedCourse = course.value
    const result = await api.get<Charts>(`dashboard/charts${qs({ course: selectedCourse, engagement: engagement.value, activity_type: activityType.value })}`)
    return withModulePerformance(result, selectedCourse,
      (learnerId, courseId) => api.get<Matrix>(`learners/${learnerId}/courses/${courseId}/matrix`))
  },
  [course, engagement, activityType],
)
// Module snapshot always shows every module (unfiltered by module) so it can be used to drill in.
const allModules = useAsync(
  () => api.get<Charts>(`dashboard/charts${qs({ engagement: engagement.value })}`),
  [engagement],
)

const k = computed(() => summary.data.value?.kpis)
const defs = computed(() => summary.data.value?.definitions ?? charts.data.value?.definitions ?? {})
const courseName = computed(() => filters.data.value?.courses.find((c) => String(c.course_id) === course.value)?.title)

const kpiCards = computed(() => {
  const x = k.value
  if (!x) return []
  return [
    { label: 'Active in LMS', icon: 'users', tone: 'neutral' as const, value: `${num(x.active_learners.value)} / ${num(x.unique_learners.value)}`, sub: `${num(x.enrollments.value)} enrollments · ${pct(x.active_learners.pct)} active`, info: defs.value.active_learners },
    { label: 'Mean LMS progress', icon: 'gauge', tone: 'teal' as const, value: pct(x.mean_progress.value), meter: x.mean_progress.value, sub: `Median ${pct(x.mean_progress.median)} · ${num(x.mean_progress.n)} enrollments`, info: defs.value.mean_progress },
    x.attendance.available
      ? { label: 'Attendance (known evidence)', icon: 'users', tone: 'navy' as const, accent: '#142c52', value: pct(x.attendance.pct), meter: x.attendance.pct,
          sub: `${ratio(x.attendance.attended, x.attendance.expected)} present / known learner-session observations · ${pct(x.attendance.coverage_pct)} evidence coverage (${ratio(x.attendance.expected, x.attendance.possible_learner_sessions)} known / possible within dated sessions)`,
          info: `${defs.value.attendance} ${x.attendance.enrollments_without_teams_identity} of ${x.attendance.enrollments_covered} covered enrollments (${x.attendance.learners_without_teams_identity} learners) have no matched Teams identity: attendance unknown, excluded from this figure.` }
      : { label: 'Attendance (known evidence)', icon: 'users', tone: 'neutral' as const, value: 'Not Available', sub: 'No measurable attendance evidence for this selection', info: defs.value.attendance },
    { label: 'Mean latest quiz score', icon: 'target', tone: 'blue' as const, accent: '#2a78d6', value: pct(x.mean_latest_score.value), meter: x.mean_latest_score.value, sub: `Latest graded attempt across ${num(x.mean_latest_score.n)} learner–quiz results`, info: defs.value.mean_latest_score },
    { label: 'Quiz pass rate', icon: 'award', tone: 'blue' as const, accent: '#2a78d6', value: pct(x.pass_rate.pct), meter: x.pass_rate.pct, sub: `${ratio(x.pass_rate.value ?? 0, x.pass_rate.denominator ?? 0)} latest attempts passed`, info: defs.value.pass_rate },
    { label: 'Assignment submissions', icon: 'upload', tone: 'neutral' as const, accent: '#6b7489', value: num(x.submissions.value), meter: x.assignment_coverage.pct, sub: `${ratio(x.assignment_coverage.value ?? 0, x.assignment_coverage.denominator ?? 0)} pairs · ${pct(x.assignment_coverage.pct)} coverage`, info: `${defs.value.submissions} ${defs.value.assignment_coverage}` },
  ]
})

const c = computed(() => charts.data.value)
const quizModule = computed(() => {
  const rows = c.value?.quiz_by_module ?? []
  return {
    categories: rows.map((r) => shortLabel(r.course)), full: rows.map((r) => r.course),
    series: [
      { name: 'Quiz completion', color: '#2a78d6', data: rows.map((r) => r.completion_pct) },
      { name: 'Mean latest score', color: '#eb6834', data: rows.map((r) => r.mean_latest_score) },
      { name: 'Pass rate (latest)', color: '#1baf7a', data: rows.map((r) => r.pass_rate_pct) },
    ],
    tooltip: rows.map((r) => [`${r.attempted_pairs} of ${r.possible_pairs} learner-quiz pairs attempted`, `${r.known_quizzes} quizzes known for module`]),
    table: { columns: ['Module', 'Known quizzes', 'Attempted / possible', 'Completion', 'Mean latest score', 'Pass rate'], rows: rows.map((r) => [r.course, r.known_quizzes, `${r.attempted_pairs} / ${r.possible_pairs}`, pct(r.completion_pct), pct(r.mean_latest_score), pct(r.pass_rate_pct)]) },
  }
})
const completionTable = computed(() => {
  const rows = c.value?.activity_completion ?? []
  const kinds = rows.length ? Object.keys(rows[0].by_type) : []
  return { columns: ['Module', ...kinds], rows: rows.map((r) => [r.course, ...kinds.map((kk) => r.by_type[kk].pct === null ? 'n/a' : `${pct(r.by_type[kk].pct)} (${r.by_type[kk].completed}/${r.by_type[kk].possible})`)]) }
})

function selectModule(id: number) { course.value = course.value === String(id) ? '' : String(id) }
function clearFilters() { router.replace({ query: {} }) }
</script>

<template>
  <div class="space-y-6">
    <!-- Hero -->
    <section class="hero-band fade-up relative overflow-hidden rounded-2xl px-5 py-6 text-white shadow-[var(--shadow-card)] sm:px-8 sm:py-8">
      <div class="flex flex-wrap items-end gap-6">
        <div class="mr-auto max-w-2xl">
          <p class="text-[10.5px] font-semibold uppercase tracking-[0.16em] text-teal-200/90">Executive Overview · Alfanar programme</p>
          <h1 class="mt-2 text-[26px] font-semibold leading-tight sm:text-[30px]">{{ courseName ?? 'LMS activity at a glance' }}</h1>
          <p class="mt-2 text-sm leading-relaxed text-blue-100/80">
            LMS activity, learning-material completion and quiz achievement, calculated directly from imported LMS records.
            Progress figures are LMS-reported, not qualification outcomes.
          </p>
        </div>
        <dl class="grid grid-cols-2 gap-6 sm:gap-10">
          <div>
            <dt class="text-[10.5px] font-semibold uppercase tracking-[0.14em] text-blue-100/70">Learners</dt>
            <dd class="font-heading mt-1 text-[32px] font-semibold leading-none tabular">{{ k ? num(k.unique_learners.value) : '—' }}</dd>
          </div>
          <div>
            <dt class="text-[10.5px] font-semibold uppercase tracking-[0.14em] text-blue-100/70">Modules</dt>
            <dd class="font-heading mt-1 text-[32px] font-semibold leading-none tabular">{{ k ? num(k.modules.value) : '—' }}</dd>
          </div>
        </dl>
      </div>
    </section>

    <!-- Filters -->
    <section class="panel flex flex-wrap items-end gap-3 px-4 py-3">
      <span class="mr-1 hidden items-center gap-1.5 self-center text-[12px] font-semibold text-ink-2 lg:inline-flex"><Icon name="filter" :size="14" />Filters</span>
      <FilterSelect v-model="course" label="Module" all-label="All modules" :options="(filters.data.value?.courses ?? []).map((x) => ({ value: x.course_id, label: x.title }))" />
      <FilterSelect v-model="engagement" label="Learner status" all-label="All learners" :options="filters.data.value?.engagement ?? []" />
      <FilterSelect v-model="activityType" label="Activity type (charts)" all-label="All types" :options="filters.data.value?.activity_types ?? []" />
      <button v-if="hasFilters" class="focus-ring h-9 rounded-md px-3 text-sm font-medium text-series-1 hover:bg-blue-50" @click="clearFilters">Clear filters</button>
      <span v-if="summary.loading.value || charts.loading.value" class="ml-auto inline-flex items-center gap-1.5 self-center text-[12px] text-ink-3"><Icon name="refresh" :size="13" class="animate-spin" />Updating…</span>
    </section>

    <!-- Module snapshot -->
    <section>
      <div class="mb-3 flex items-end justify-between gap-3">
        <div>
          <h2 class="text-[16px] font-semibold text-ink">Module snapshot</h2>
          <p class="text-[12.5px] text-ink-3">Select a module to focus the whole overview on it.</p>
        </div>
      </div>
      <StateBlock :loading="allModules.loading.value && !allModules.data.value" :error="allModules.error.value" height="11rem" @retry="allModules.reload">
        <ModuleSnapshot :progress="allModules.data.value?.module_progress ?? []" :quiz="allModules.data.value?.quiz_by_module ?? []" :selected="course" :delivered-info="allModules.data.value?.definitions.delivered_lectures" @select="selectModule" />
      </StateBlock>
    </section>

    <!-- KPIs -->
    <StateBlock :loading="summary.loading.value && !summary.data.value" :error="summary.error.value" :empty="k?.enrollments.value === 0" height="9rem" @retry="summary.reload">
      <section :class="{ 'opacity-60 transition-opacity': summary.loading.value }">
        <h2 class="eyebrow mb-2">Key metrics</h2>
        <div class="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-3">
          <KpiCard v-for="card in kpiCards" :key="card.label" v-bind="card" />
        </div>
      </section>
    </StateBlock>

    <!-- Charts -->
    <StateBlock :loading="charts.loading.value && !charts.data.value" :error="charts.error.value" :empty="(c?.module_progress.length ?? 0) === 0" height="24rem" @retry="charts.reload">
      <div class="grid gap-4 lg:grid-cols-2" :class="{ 'opacity-60 transition-opacity': charts.loading.value }">
        <ChartCard title="Quiz achievement by module" subtitle="Completion, mean latest score and pass rate (%)" :info="`${defs.quiz_completion} ${defs.mean_latest_score} ${defs.pass_rate}`" :table="quizModule.table">
          <HBarChart :categories="quizModule.categories" :full-labels="quizModule.full" :series="quizModule.series" :max="100" unit="%" :tooltip-rows="quizModule.tooltip" />
        </ChartCard>

        <ChartCard title="Activity completion breakdown" subtitle="Completed ÷ possible (known items × enrolled learners), by module and type" info="Quizzes: attempted at least once. Assignments: non-draft submission. Lessons: recorded completion in Lesson Details. Known items are those that appear in the export for that module." :table="completionTable">
          <CompletionHeatmap :rows="c?.activity_completion ?? []" />
        </ChartCard>

        <ChartCard title="Learner-by-module progress" subtitle="LMS-reported progress — select a cell to open that learner's activity matrix" :info="defs.mean_progress" class="lg:col-span-2">
          <StateBlock :empty="(c?.learner_module_matrix.rows.length ?? 0) === 0" height="10rem">
            <LearnerModuleHeatmap v-if="c" :matrix="c.learner_module_matrix" :selected-module="course" :definitions="defs" />
          </StateBlock>
        </ChartCard>
      </div>
    </StateBlock>
  </div>
</template>
