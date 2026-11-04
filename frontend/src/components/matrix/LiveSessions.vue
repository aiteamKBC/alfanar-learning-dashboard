<script setup lang="ts">
import { computed, ref } from 'vue'
import type { MatrixAttendance } from '@/api/types'
import Icon from '@/components/common/Icon.vue'
import InfoTip from '@/components/common/InfoTip.vue'
import { dateTime, duration, hours, pct } from '@/utils/format'

/** Delivered Teams sessions for one learner in one module, with a summary footer. */
const props = defineProps<{ attendance: MatrixAttendance; definition?: string }>()
const open = ref(true)

const counts = computed(() => {
  const s = props.attendance.sessions
  return {
    attended: s.filter((x) => x.status === 'attended').length,
    brief: s.filter((x) => x.status === 'brief').length,
    none: s.filter((x) => x.status === 'not_recorded').length,
  }
})
const STATUS = {
  attended: { label: 'Attended', cls: 'bg-emerald-50 text-emerald-800 ring-emerald-200', icon: '✓' },
  brief: { label: 'Brief join', cls: 'bg-amber-50 text-amber-800 ring-amber-200', icon: '◔' },
  not_recorded: { label: 'Not recorded', cls: 'bg-white text-ink-3 ring-gray-200', icon: '–' },
} as const
function timeOnly(v: string | null) {
  return v ? new Date(v).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', timeZone: 'UTC' }) : '—'
}
</script>

<template>
  <section v-if="!attendance.available" class="flex items-start gap-2 rounded-xl border border-amber-200 bg-amber-50/70 px-4 py-3 text-[13px] text-amber-900">
    <Icon name="info" :size="15" class="mt-0.5 shrink-0" />
    <span><span class="font-semibold">Live attendance is not available.</span> {{ attendance.reason }}</span>
  </section>

  <section v-else class="card overflow-hidden">
    <button type="button" class="focus-ring flex w-full flex-wrap items-center gap-x-6 gap-y-2 border-b border-line px-4 py-3 text-left" :aria-expanded="open" @click="open = !open">
      <span class="flex items-center gap-2">
        <span class="grid h-8 w-8 place-items-center rounded-lg bg-navy-soft text-navy"><Icon name="users" :size="16" /></span>
        <span>
          <span class="eyebrow flex items-center gap-1">Live sessions · Microsoft Teams <InfoTip v-if="definition" :text="definition" /></span>
          <span class="font-heading text-[15px] font-semibold text-ink">{{ attendance.attended }} / {{ attendance.delivered }} delivered sessions attended</span>
        </span>
      </span>
      <span class="font-heading text-[22px] font-semibold text-ink tabular">{{ pct(attendance.pct) }}</span>
      <span class="text-[12px] text-ink-3">{{ hours(attendance.live_seconds) }} recorded live presence</span>
      <span v-if="attendance.teams_identity === false" class="rounded-full bg-amber-50 px-2 py-0.5 text-[11px] font-medium text-amber-800 ring-1 ring-amber-200">
        No matched Teams identity for this learner
      </span>
      <Icon :name="open ? 'down' : 'chevron'" :size="16" class="ml-auto text-ink-3" />
    </button>

    <div v-if="open" class="matrix-scroll max-h-[420px] overflow-auto">
      <table class="w-full min-w-[760px] border-separate border-spacing-0 text-[13px]">
        <thead class="sticky top-0 z-10 bg-[#f6f5f1] text-left">
          <tr>
            <th class="eyebrow border-b border-line px-4 py-2.5">Session</th>
            <th class="eyebrow border-b border-line px-3 py-2.5">Date (UTC)</th>
            <th class="eyebrow border-b border-line px-3 py-2.5">Attendance</th>
            <th class="eyebrow border-b border-line px-3 py-2.5">Presence</th>
            <th class="eyebrow border-b border-line px-3 py-2.5">Joined – left (UTC)</th>
            <th class="eyebrow border-b border-line px-3 py-2.5">Learners attended</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="s in attendance.sessions" :key="s.number" class="hover:bg-navy-soft/30">
            <td class="border-b border-line px-4 py-2 font-medium text-ink tabular">Session {{ s.number }}</td>
            <td class="border-b border-line px-3 py-2 text-ink-2 tabular">{{ dateTime(s.date, false) }}</td>
            <td class="border-b border-line px-3 py-2">
              <span class="inline-flex items-center gap-1 whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset" :class="STATUS[s.status].cls">
                <span aria-hidden="true">{{ STATUS[s.status].icon }}</span>{{ STATUS[s.status].label }}
              </span>
            </td>
            <td class="border-b border-line px-3 py-2 tabular" :class="s.presence_seconds === null ? 'text-ink-3' : 'text-ink'">{{ s.presence_seconds === null ? '—' : duration(s.presence_seconds) }}</td>
            <td class="border-b border-line px-3 py-2 text-ink-2 tabular">
              <template v-if="s.first_join">{{ timeOnly(s.first_join) }} – {{ timeOnly(s.last_leave) }}<span v-if="(s.joins ?? 0) > 1" class="ml-1 text-[11px] text-ink-3">({{ s.joins }} joins)</span></template>
              <template v-else>—</template>
            </td>
            <td class="border-b border-line px-3 py-2 text-ink-2 tabular">{{ s.learners_attended }}</td>
          </tr>
        </tbody>
        <tfoot class="sticky bottom-0 bg-[#f6f5f1] text-[12px]">
          <tr class="text-ink">
            <td class="border-t-2 border-line-strong px-4 py-2.5 font-semibold">{{ attendance.delivered }} delivered</td>
            <td class="border-t-2 border-line-strong px-3 py-2.5" />
            <td class="border-t-2 border-line-strong px-3 py-2.5 tabular"><b>{{ counts.attended }}</b> attended · {{ counts.brief }} brief · {{ counts.none }} not recorded</td>
            <td class="border-t-2 border-line-strong px-3 py-2.5 font-semibold tabular">{{ hours(attendance.live_seconds) }}</td>
            <td class="border-t-2 border-line-strong px-3 py-2.5" />
            <td class="border-t-2 border-line-strong px-3 py-2.5 font-semibold tabular">{{ pct(attendance.pct) }}</td>
          </tr>
        </tfoot>
      </table>
    </div>
  </section>
</template>
