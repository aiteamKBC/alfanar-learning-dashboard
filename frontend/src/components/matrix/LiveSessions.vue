<script setup lang="ts">
import { computed, ref } from 'vue'
import type { MatrixAttendance, ModuleProgress } from '@/api/types'
import AttendanceScope from '@/components/common/AttendanceScope.vue'
import Icon from '@/components/common/Icon.vue'
import InfoTip from '@/components/common/InfoTip.vue'
import { dateTime, pct } from '@/utils/format'

/** Delivered Teams sessions for one learner in one module, with a summary footer. */
const props = defineProps<{ attendance: MatrixAttendance; definition?: string; module?: ModuleProgress }>()
const open = ref(true)

const counts = computed(() => {
  const s = props.attendance.sessions
  return {
    attended: s.filter((x) => x.status === 'attended').length,
    brief: s.filter((x) => x.status === 'brief').length,
    none: s.filter((x) => x.status === 'not_recorded' || x.status === 'unavailable').length,
    absent: s.filter((x) => x.status === 'absent').length,
    timed: s.filter((x) => x.timing.available).length,
  }
})
const STATUS = {
  absent: { label: 'Recorded absent', cls: 'bg-white text-ink-3 ring-gray-200', icon: '–' },
  unavailable: { label: 'Not Available', cls: 'bg-amber-50 text-amber-800 ring-amber-200', icon: '–' },
  attended: { label: 'Attended', cls: 'bg-emerald-50 text-emerald-800 ring-emerald-200', icon: '✓' },
  brief: { label: 'Brief join', cls: 'bg-amber-50 text-amber-800 ring-amber-200', icon: '◔' },
  not_recorded: { label: 'Not recorded', cls: 'bg-white text-ink-3 ring-gray-200', icon: '–' },
} as const
function timeOnly(v: string | null) {
  return v ? new Date(v).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', second: '2-digit', timeZone: 'UTC' }) : 'Not Available'
}
function timingDuration(seconds: number | null) {
  if (seconds === null) return 'Not Available'
  return [Math.floor(seconds / 3600), Math.floor(seconds / 60) % 60, seconds % 60]
    .map((value) => String(value).padStart(2, '0')).join(':')
}
</script>

<template>
  <section v-if="!attendance.sessions.length" class="flex items-start gap-2 rounded-xl border border-amber-200 bg-amber-50/70 px-4 py-3 text-[13px] text-amber-900">
    <Icon name="info" :size="15" class="mt-0.5 shrink-0" />
    <span><span class="font-semibold">Attendance: Not Available.</span> {{ attendance.reason }}</span>
  </section>

  <section v-else class="card overflow-hidden" data-live-sessions>
    <button type="button" class="focus-ring flex w-full flex-wrap items-center gap-x-6 gap-y-2 border-b border-line px-4 py-3 text-left" :aria-expanded="open" @click="open = !open">
      <span class="flex items-center gap-2">
        <span class="grid h-8 w-8 place-items-center rounded-lg bg-navy-soft text-navy"><Icon name="users" :size="16" /></span>
        <span>
          <span class="eyebrow flex items-center gap-1">Attendance evidence <InfoTip v-if="definition" :text="definition" /></span>
          <span v-if="attendance.available" class="font-heading text-[15px] font-semibold text-ink">{{ attendance.attended }} / {{ attendance.measured ?? attendance.delivered }} known sessions attended · {{ attendance.delivered }} dated sessions</span>
          <span v-else class="font-heading text-[15px] font-semibold text-ink">Attendance: Not Available · {{ attendance.delivered }} dated sessions</span>
        </span>
      </span>
      <span class="font-heading text-[22px] font-semibold text-ink tabular">{{ pct(attendance.pct) }}</span>
      <span class="text-[12px] text-ink-3">{{ counts.timed ? `${counts.timed} / ${attendance.sessions.length} sessions with timing` : 'Timing: Not Available' }}</span>
      <span v-if="attendance.teams_identity === false && !attendance.timing_available" class="rounded-full bg-amber-50 px-2 py-0.5 text-[11px] font-medium text-amber-800 ring-1 ring-amber-200">
        No matched Teams identity for this learner
      </span>
      <Icon :name="open ? 'down' : 'chevron'" :size="16" class="ml-auto text-ink-3" />
    </button>
    <div class="border-b border-line px-4 py-3">
      <p class="text-[12px] text-ink-2">Learner attendance uses known observations only. Missing attendance is Not Available and excluded from the rate.</p>
      <AttendanceScope v-if="module" :module="module" />
    </div>

    <div v-if="open" class="matrix-scroll max-h-[420px] overflow-auto">
      <table class="w-full min-w-[760px] border-separate border-spacing-0 text-[13px]">
        <thead class="sticky top-0 z-10 bg-[#f6f5f1] text-left">
          <tr>
            <th class="eyebrow border-b border-line px-4 py-2.5">Session</th>
            <th class="eyebrow border-b border-line px-3 py-2.5">Date (UTC)</th>
            <th class="eyebrow border-b border-line px-3 py-2.5">Attendance</th>
            <th class="eyebrow border-b border-line px-3 py-2.5">First Join (UTC)</th>
            <th class="eyebrow border-b border-line px-3 py-2.5">Last Leave (UTC)</th>
            <th class="eyebrow border-b border-line px-3 py-2.5">Total Attended Duration <InfoTip text="Hours:minutes:seconds from merged Teams intervals. Overlaps count once; gaps are excluded." /></th>
            <th class="eyebrow border-b border-line px-3 py-2.5">Learners attended</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="s in attendance.sessions" :key="s.number" :data-session-date="s.date" class="hover:bg-navy-soft/30">
            <td class="border-b border-line px-4 py-2 font-medium text-ink tabular">Session {{ s.number }}</td>
            <td class="border-b border-line px-3 py-2 text-ink-2 tabular">{{ dateTime(s.date, false) }}</td>
            <td class="border-b border-line px-3 py-2">
              <span class="inline-flex items-center gap-1 whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset" :class="STATUS[s.status].cls">
                <span aria-hidden="true">{{ STATUS[s.status].icon }}</span>{{ STATUS[s.status].label }}
              </span>
              <div v-if="s.reconciliation?.corrected" class="mt-1 flex items-center gap-1 text-[11px] text-ink-2" data-attendance-reconciliation>
                <span>Verified Teams<span v-if="s.reconciliation.matrix_status === 'absent'"> · Matrix: absent</span></span>
              </div>
              <div v-else-if="s.reconciliation?.needs_review" class="mt-1 flex items-center gap-1 text-[11px] text-amber-800" data-attendance-review>
                <span>Review</span>
              </div>
              <div v-else-if="s.reconciliation?.reason_code === 'short_timing_unknown'" class="mt-1 flex items-center gap-1 text-[11px] text-ink-3" data-short-timing>
                <span>Below {{ s.reconciliation.minimum_minutes }} min</span>
              </div>
            </td>
            <td data-first-join class="border-b border-line px-3 py-2 text-ink-2 tabular">{{ timeOnly(s.timing.first_join) }}</td>
            <td data-last-leave class="border-b border-line px-3 py-2 text-ink-2 tabular">{{ timeOnly(s.timing.last_leave) }}</td>
            <td data-attended-duration class="border-b border-line px-3 py-2 tabular" :class="s.timing.available ? 'text-ink' : 'text-ink-3'">
              {{ timingDuration(s.timing.duration_seconds) }}
              <span v-if="(s.timing.visits ?? 0) > 1" class="ml-1 text-[11px] text-ink-3">({{ s.timing.visits }} visits)</span>
            </td>
            <td class="border-b border-line px-3 py-2 text-ink-2 tabular">{{ s.learners_attended }}</td>
          </tr>
        </tbody>
        <tfoot class="sticky bottom-0 bg-[#f6f5f1] text-[12px]">
          <tr class="text-ink">
            <td class="border-t-2 border-line-strong px-4 py-2.5 font-semibold">{{ attendance.delivered }} sessions with evidence</td>
            <td class="border-t-2 border-line-strong px-3 py-2.5" />
            <td class="border-t-2 border-line-strong px-3 py-2.5 tabular"><b>{{ counts.attended }}</b> attended · {{ counts.absent }} absent · {{ counts.brief }} brief · {{ counts.none }} Not Available</td>
            <td class="border-t-2 border-line-strong px-3 py-2.5" />
            <td class="border-t-2 border-line-strong px-3 py-2.5" />
            <td class="border-t-2 border-line-strong px-3 py-2.5" />
            <td class="border-t-2 border-line-strong px-3 py-2.5 font-semibold tabular">{{ pct(attendance.pct) }}</td>
          </tr>
        </tfoot>
      </table>
    </div>
  </section>
</template>
