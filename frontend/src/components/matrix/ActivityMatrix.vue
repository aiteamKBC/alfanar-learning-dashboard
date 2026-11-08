<script setup lang="ts">
import { computed, ref } from 'vue'
import type { Kind, Matrix, MatrixRow } from '@/api/types'
import Icon from '@/components/common/Icon.vue'
import InfoTip from '@/components/common/InfoTip.vue'
import StatusPill from '@/components/common/StatusPill.vue'
import { dateTime, duration, KIND_COLOR, KIND_LABEL, pct } from '@/utils/format'

const props = defineProps<{ matrix: Matrix }>()

const kindFilter = ref<Kind | ''>('')
const recordedOnly = ref(false)
const search = ref('')
const expanded = ref<Set<string>>(new Set())

const has = (k: Kind) => props.matrix.kinds.includes(k)
const lessonKinds = computed(() => (['video', 'audio', 'pdf', 'reading'] as Kind[]).filter(has))
const rowKey = (r: MatrixRow) => `${r.kind}-${r.activity_id}`

const rows = computed(() => {
  const q = search.value.trim().toLowerCase()
  return props.matrix.rows.filter((r) =>
    (!kindFilter.value || r.kind === kindFilter.value)
    && (!recordedOnly.value || r.recorded)
    && (!q || r.title.toLowerCase().includes(q) || String(r.activity_id).includes(q)))
})

// Footer aggregates exactly the rows currently represented in the table.
const foot = computed(() => {
  const rs = rows.value
  const of = (k: Kind) => {
    const items = rs.filter((r) => r.kind === k)
    return { known: items.length, recorded: items.filter((r) => r.recorded).length }
  }
  const quizzes = rs.filter((r) => r.kind === 'quiz' && r.quiz)
  const scores = quizzes.map((r) => r.quiz!.latest_score).filter((v): v is number => v !== null)
  const assignments = rs.filter((r) => r.kind === 'assignment')
  const timed = rs.filter((r) => r.lesson && r.lesson.recorded_seconds !== null)
  return {
    all: { known: rs.length, recorded: rs.filter((r) => r.recorded).length },
    quiz: of('quiz'),
    attempts: quizzes.reduce((n, r) => n + r.quiz!.history.length, 0),
    passed: quizzes.filter((r) => r.quiz!.latest_status === 'passed').length,
    graded: scores.length,
    meanScore: scores.length === props.matrix.footer.quiz.graded ? props.matrix.footer.quiz.mean_latest_score
      : (scores.length ? scores.reduce((a, b) => a + b, 0) / scores.length : null),
    lesson: Object.fromEntries((['video', 'audio', 'pdf', 'reading'] as Kind[]).map((k) => [k, of(k)])) as Record<string, { known: number; recorded: number }>,
    assignment: of('assignment'),
    submitted: assignments.filter((r) => r.assignment?.has_submitted).length,
    graded_assignments: assignments.filter((r) => r.assignment?.grade !== null && r.assignment?.grade !== undefined).length,
    seconds: timed.reduce((n, r) => n + (r.lesson!.recorded_seconds ?? 0), 0),
    timed: timed.length,
  }
})

function toggle(r: MatrixRow) {
  if (!r.recorded) return
  const s = new Set(expanded.value)
  const key = rowKey(r)
  if (s.has(key)) { s.delete(key) } else { s.add(key) }
  expanded.value = s
}
function expandAll(open: boolean) {
  expanded.value = open ? new Set(rows.value.filter((r) => r.recorded).map(rowKey)) : new Set()
}
const ratioPct = (a: number, b: number) => (b ? pct((100 * a) / b, 0) : '—')

const colCount = computed(() => 2 + (has('quiz') ? 3 : 0) + lessonKinds.value.length + (has('assignment') ? 2 : 0) + 1)
const TH1 = 'sticky top-0 z-20 h-10 border-b border-line bg-[#f1f0eb] px-3 text-center align-middle'
const TH2 = 'sticky top-10 z-20 h-9 border-b border-line-strong bg-[#f8f7f4] px-3 text-left align-middle text-[10.5px] font-semibold uppercase tracking-[0.1em] text-ink-3 whitespace-nowrap'
const TD = 'border-b border-line px-3 py-2.5 align-middle'
const TF = 'border-t-2 border-line-strong bg-[#f6f5f1] px-3 py-3 align-top text-[12px]'
</script>

<template>
  <div class="card overflow-hidden">
    <!-- Toolbar -->
    <div class="flex flex-wrap items-center gap-2 border-b border-line px-4 py-3">
      <button
        class="focus-ring rounded-full border px-3 py-1 text-[12px] font-medium transition"
        :class="kindFilter === '' ? 'border-navy bg-navy text-white' : 'border-line bg-white text-ink-2 hover:bg-subtle'"
        @click="kindFilter = ''"
      >All · {{ matrix.rows.length }}</button>
      <button
        v-for="k in matrix.kinds" :key="k"
        class="focus-ring inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-[12px] font-medium transition"
        :class="kindFilter === k ? 'border-navy bg-navy text-white' : 'border-line bg-white text-ink-2 hover:bg-subtle'"
        @click="kindFilter = kindFilter === k ? '' : k"
      >
        <span class="h-2 w-2 rounded-full" :style="{ background: KIND_COLOR[k] }" aria-hidden="true" />
        {{ KIND_LABEL[k] }} · {{ matrix.rows.filter((r) => r.kind === k).length }}
      </button>
      <div class="ml-auto flex flex-wrap items-center gap-3">
        <span class="relative">
          <Icon name="search" :size="14" class="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-ink-3" />
          <input v-model="search" type="search" placeholder="Find activity…" class="focus-ring h-8 w-48 rounded-md border border-line pl-8 pr-2 text-[13px]" aria-label="Find activity" />
        </span>
        <label class="inline-flex items-center gap-2 text-[13px] text-ink-2">
          <input v-model="recordedOnly" type="checkbox" class="h-4 w-4 rounded border-line-strong accent-[#142c52]" />
          Recorded only
        </label>
        <button class="focus-ring rounded-md px-2 py-1 text-[12px] font-medium text-series-1 hover:bg-blue-50" @click="expandAll(expanded.size === 0)">
          {{ expanded.size ? 'Collapse all' : 'Expand evidence' }}
        </button>
      </div>
    </div>

    <div class="matrix-scroll overflow-auto" style="max-height: 72vh">
      <table class="w-full min-w-[1180px] border-separate border-spacing-0 text-[13px]">
        <thead>
          <tr>
            <th rowspan="2" class="sticky left-0 top-0 z-30 h-[76px] w-[340px] min-w-[300px] border-b border-r border-line-strong bg-[#f1f0eb] px-4 text-left align-bottom">
              <span class="eyebrow block">Learning activity</span>
              <span class="mb-2 block text-[11px] font-normal text-ink-3">LMS item · in source order</span>
            </th>
            <th rowspan="2" class="sticky top-0 z-20 h-[76px] border-b border-line-strong bg-[#f1f0eb] px-3 pb-2 text-left align-bottom">
              <span class="eyebrow">Status</span>
            </th>
            <th v-if="has('quiz')" colspan="3" :class="TH1">
              <span class="inline-flex items-center gap-1.5 text-[10.5px] font-semibold uppercase tracking-[0.14em] text-[#1f62b5]"><Icon name="quiz" :size="14" />Quiz</span>
            </th>
            <th v-if="lessonKinds.length" :colspan="lessonKinds.length" :class="TH1">
              <span class="inline-flex items-center gap-1.5 text-[10.5px] font-semibold uppercase tracking-[0.14em] text-brand"><Icon name="book" :size="14" />Learning materials</span>
            </th>
            <th v-if="has('assignment')" colspan="2" :class="TH1">
              <span class="inline-flex items-center gap-1.5 text-[10.5px] font-semibold uppercase tracking-[0.14em] text-[#3f6212]"><Icon name="assignment" :size="14" />Assignment</span>
            </th>
            <th rowspan="2" class="sticky top-0 z-20 h-[76px] border-b border-line-strong bg-[#f1f0eb] px-3 pb-2 text-left align-bottom">
              <span class="eyebrow inline-flex items-center gap-1">Evidence <InfoTip text="When the LMS recorded the item. Lesson spans are open-to-close times and can include idle time; '—' means the LMS recorded no duration." /></span>
            </th>
          </tr>
          <tr>
            <template v-if="has('quiz')">
              <th :class="TH2">Attempts</th>
              <th :class="TH2"><span class="inline-flex items-center gap-1">Latest score <InfoTip :text="matrix.definitions.mean_latest_score" /></span></th>
              <th :class="TH2">Result</th>
            </template>
            <th v-for="k in lessonKinds" :key="k" :class="TH2">
              <span class="inline-flex items-center gap-1.5"><span class="h-1.5 w-1.5 rounded-full" :style="{ background: KIND_COLOR[k] }" />{{ KIND_LABEL[k] }}</span>
            </th>
            <template v-if="has('assignment')">
              <th :class="TH2">Submission</th>
              <th :class="TH2"><span class="inline-flex items-center gap-1">Grade <InfoTip text="Grade recorded on the assignment submission — not an awarded module grade." /></span></th>
            </template>
          </tr>
        </thead>

        <tbody>
          <template v-for="r in rows" :key="rowKey(r)">
            <tr
              class="group transition-colors"
              :class="[r.recorded ? 'cursor-pointer hover:bg-navy-soft/40' : 'text-ink-3', expanded.has(rowKey(r)) ? 'bg-navy-soft/40' : '']"
              :aria-expanded="r.recorded ? expanded.has(rowKey(r)) : undefined"
              @click="toggle(r)"
            >
              <td :class="TD" class="sticky left-0 z-10 border-r border-line bg-white group-hover:bg-[#f2f5fa]">
                <div class="flex items-start gap-2.5">
                  <span class="mt-1 grid h-6 w-6 shrink-0 place-items-center rounded-md" :style="{ background: `${KIND_COLOR[r.kind]}1a`, color: KIND_COLOR[r.kind] }"><Icon :name="r.kind" :size="13" :stroke="2" /></span>
                  <div class="min-w-0 flex-1">
                    <div class="line-clamp-2 leading-snug" :class="r.recorded ? 'font-medium text-ink' : 'text-ink-2'" :title="r.title">{{ r.title }}</div>
                    <div class="text-[11px] text-ink-3 tabular">{{ r.kind_label }} · item {{ r.activity_id }}</div>
                  </div>
                  <Icon v-if="r.recorded" :name="expanded.has(rowKey(r)) ? 'down' : 'chevron'" :size="14" class="mt-1 text-ink-3" />
                </div>
              </td>
              <td :class="TD"><StatusPill :status="r.recorded ? 'recorded' : ''" /></td>
              <template v-if="has('quiz')">
                <td :class="TD" class="tabular">
                  <span v-if="r.quiz" class="font-medium text-ink">{{ r.quiz.attempts ?? '—' }}</span>
                  <span v-else-if="r.kind === 'quiz'" class="text-[12px]">—</span>
                </td>
                <td :class="TD" class="tabular">
                  <template v-if="r.quiz">
                    <span class="font-heading font-semibold text-ink">{{ pct(r.quiz.latest_score) }}</span>
                    <span v-if="r.quiz.best_score !== null && r.quiz.best_score !== r.quiz.latest_score" class="ml-1.5 text-[11px] text-ink-3">best {{ pct(r.quiz.best_score) }}</span>
                  </template>
                </td>
                <td :class="TD"><StatusPill v-if="r.quiz" :status="r.quiz.latest_status" /></td>
              </template>
              <td v-for="k in lessonKinds" :key="k" :class="TD">
                <template v-if="r.kind === k">
                  <span v-if="r.lesson" class="inline-flex items-center gap-1 rounded-md bg-emerald-50 px-1.5 py-0.5 text-[11.5px] font-medium text-emerald-800"><Icon name="check" :size="12" :stroke="2.4" />Completed</span>
                  <span v-else class="text-[12px] text-ink-3">No record</span>
                </template>
              </td>
              <template v-if="has('assignment')">
                <td :class="TD"><StatusPill v-if="r.kind === 'assignment'" :status="r.assignment?.status ?? ''" /></td>
                <td :class="TD" class="tabular">
                  <template v-if="r.assignment">{{ r.assignment.grade === null ? 'Not graded' : r.assignment.grade }}</template>
                </td>
              </template>
              <td :class="TD" class="whitespace-nowrap text-[12px] text-ink-2 tabular">
                <template v-if="r.lesson">{{ dateTime(r.lesson.started_at, false) }} <span class="text-ink-3">· {{ duration(r.lesson.recorded_seconds) }}</span></template>
                <template v-else-if="r.quiz && r.quiz.history.length">{{ dateTime(r.quiz.history[r.quiz.history.length - 1].created_at, false) }}</template>
                <template v-else-if="r.assignment">{{ dateTime(r.assignment.updated_at, false) }}</template>
              </td>
            </tr>

            <!-- Evidence detail -->
            <tr v-if="r.recorded && expanded.has(rowKey(r))">
              <td :colspan="colCount" class="border-b border-line bg-[#f8f9fc] px-4 py-3">
                <div class="sticky left-4 max-w-[min(1100px,calc(100vw-6rem))] pl-8">
                  <div v-if="r.quiz" class="space-y-2">
                    <p class="eyebrow">Attempt history · {{ r.quiz.history.length }} attempt{{ r.quiz.history.length === 1 ? '' : 's' }}<template v-if="r.quiz.questions"> · {{ r.quiz.questions }} questions</template></p>
                    <div class="flex flex-wrap gap-2">
                      <div v-for="a in r.quiz.history" :key="a.attempt" class="panel min-w-40 px-3 py-2" :class="a.attempt === r.quiz.latest_attempt ? 'ring-1 ring-series-1' : ''">
                        <div class="flex items-center justify-between gap-3 text-[11px] text-ink-3">
                          <span>Attempt {{ a.attempt }}<span v-if="a.attempt === r.quiz.latest_attempt" class="ml-1 font-semibold text-series-1">latest</span></span>
                          <StatusPill :status="a.status" />
                        </div>
                        <div class="font-heading mt-1 text-[18px] font-semibold text-ink tabular">{{ pct(a.score) }}</div>
                        <div class="text-[11px] text-ink-3 tabular">{{ dateTime(a.created_at) }}</div>
                      </div>
                    </div>
                  </div>
                  <dl v-else-if="r.lesson" class="grid gap-x-8 gap-y-2 text-[12px] sm:grid-cols-4">
                    <div><dt class="eyebrow">Started</dt><dd class="mt-0.5 text-ink tabular">{{ dateTime(r.lesson.started_at) }}</dd></div>
                    <div><dt class="eyebrow">Ended</dt><dd class="mt-0.5 text-ink tabular">{{ dateTime(r.lesson.ended_at) }}</dd></div>
                    <div><dt class="eyebrow">Recorded span</dt><dd class="mt-0.5 text-ink tabular">{{ r.lesson.recorded_seconds === null ? 'Not recorded by LMS' : duration(r.lesson.recorded_seconds) }}</dd></div>
                    <div><dt class="eyebrow">Time source</dt><dd class="mt-0.5 text-ink">{{ r.lesson.time_source === 'start_end_difference' ? 'LMS start/end timestamps' : 'Unavailable' }}</dd></div>
                  </dl>
                  <dl v-else-if="r.assignment" class="grid gap-x-8 gap-y-2 text-[12px] sm:grid-cols-4">
                    <div><dt class="eyebrow">Status</dt><dd class="mt-1"><StatusPill :status="r.assignment.status" /></dd></div>
                    <div><dt class="eyebrow">Assignment grade</dt><dd class="mt-0.5 text-ink tabular">{{ r.assignment.grade === null ? 'Not graded' : r.assignment.grade }}</dd></div>
                    <div><dt class="eyebrow">Submissions</dt><dd class="mt-0.5 text-ink tabular">{{ r.assignment.submissions }}</dd></div>
                    <div><dt class="eyebrow">Last updated</dt><dd class="mt-0.5 text-ink tabular">{{ dateTime(r.assignment.updated_at) }}</dd></div>
                  </dl>
                </div>
              </td>
            </tr>
          </template>
          <tr v-if="rows.length === 0">
            <td :colspan="colCount" class="px-4 py-14 text-center">
              <div class="mx-auto grid h-11 w-11 place-items-center rounded-full bg-subtle text-ink-3"><Icon name="search" :size="18" /></div>
              <p class="font-heading mt-2 text-sm font-semibold text-ink">No activities match</p>
              <p class="text-[12px] text-ink-3">Clear the type filter or search to bring activities back.</p>
            </td>
          </tr>
        </tbody>

        <tfoot class="sticky bottom-0 z-20">
          <tr class="text-ink">
            <td :class="TF" class="sticky left-0 z-30 border-r border-line-strong">
              <div class="eyebrow">Summary · {{ rows.length }} rows shown</div>
              <div class="font-heading mt-0.5 text-[15px] font-semibold tabular">{{ foot.all.recorded }} / {{ foot.all.known }} <span class="font-sans text-[12px] font-normal text-ink-3">activities recorded</span></div>
            </td>
            <td :class="TF" class="font-semibold tabular">{{ ratioPct(foot.all.recorded, foot.all.known) }}</td>
            <template v-if="has('quiz')">
              <td :class="TF" class="tabular"><b>{{ foot.quiz.recorded }} / {{ foot.quiz.known }}</b><div class="text-ink-3">quizzes · {{ foot.attempts }} attempts</div></td>
              <td :class="TF" class="tabular"><b class="font-heading text-[14px]">{{ pct(foot.meanScore) }}</b><div class="text-ink-3">mean of {{ foot.graded }} latest</div></td>
              <td :class="TF" class="tabular"><b>{{ foot.passed }} / {{ foot.graded }}</b><div class="text-ink-3">passed</div></td>
            </template>
            <td v-for="k in lessonKinds" :key="k" :class="TF" class="tabular">
              <b>{{ foot.lesson[k].recorded }} / {{ foot.lesson[k].known }}</b>
              <div class="text-ink-3">{{ ratioPct(foot.lesson[k].recorded, foot.lesson[k].known) }} completed</div>
            </td>
            <template v-if="has('assignment')">
              <td :class="TF" class="tabular"><b>{{ foot.submitted }} / {{ foot.assignment.known }}</b><div class="text-ink-3">submitted</div></td>
              <td :class="TF" class="tabular"><b>{{ foot.graded_assignments }}</b><div class="text-ink-3">graded</div></td>
            </template>
            <td :class="TF" class="tabular"><b>{{ foot.timed ? duration(foot.seconds) : 'Not Available' }}</b><div class="text-ink-3">{{ foot.timed }} LMS timers · unverified study time</div></td>
          </tr>
        </tfoot>
      </table>
    </div>
  </div>
</template>
