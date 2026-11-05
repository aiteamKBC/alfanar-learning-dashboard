<script setup lang="ts">
import { computed } from 'vue'
import type { ModuleProgress, QuizByModule } from '@/api/types'
import InfoTip from '@/components/common/InfoTip.vue'
import AttendanceScope from '@/components/common/AttendanceScope.vue'
import ProgressRing from './ProgressRing.vue'
import { pct } from '@/utils/format'

const props = defineProps<{ progress: ModuleProgress[]; quiz: QuizByModule[]; selected?: string; deliveredInfo?: string }>()
defineEmits<{ select: [courseId: number] }>()

const cards = computed(() => props.progress.map((m) => ({ ...m, quiz: props.quiz.find((q) => q.course_id === m.course_id) })))
</script>

<template>
  <div class="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
    <button
      v-for="(m, i) in cards" :key="m.course_id" type="button"
      class="fade-up focus-ring group rounded-[14px] border bg-white p-4 text-left shadow-[var(--shadow-card)] transition hover:-translate-y-0.5 hover:border-line-strong"
      :class="selected === String(m.course_id) ? 'border-transparent ring-2 ring-navy' : 'border-line'"
      :style="{ animationDelay: `${i * 50}ms` }"
      :aria-pressed="selected === String(m.course_id)"
      :title="selected === String(m.course_id) ? 'Showing this module — click to clear' : 'Filter the overview to this module'"
      @click="$emit('select', m.course_id)"
    >
      <div class="flex items-start gap-4">
        <ProgressRing
          :count="m.delivered_lectures.count" :caption="m.delivered_lectures.count === null ? 'no data' : 'lectures'"
          :color="m.delivered_lectures.count === null ? '#d6d3cb' : '#1baf7a'"
          :label="m.delivered_lectures.count === null ? 'Delivered lectures not available' : `${m.delivered_lectures.count} delivered lectures`" />
        <div class="min-w-0 flex-1">
          <div class="eyebrow flex items-center gap-1">Delivered Lectures
            <InfoTip v-if="deliveredInfo" :text="[deliveredInfo, m.delivered_lectures.note, m.delivered_lectures.source !== 'owner_confirmed' ? 'Lecture count comes from attendance evidence; total delivery is not independently confirmed.' : '', 'Attendance uses known observations only. Missing attendance is excluded.'].filter(Boolean).join(' ')" label="About delivered lectures" />
          </div>
          <h3 class="mt-0.5 text-[14px] font-semibold leading-snug text-ink">{{ m.course }}</h3>        </div>
      </div>
      <dl class="mt-4 grid grid-cols-3 gap-2 border-t border-line pt-3 text-[11.5px]">
        <div>
          <dt class="text-ink-3">Enrolled</dt>
          <dd class="font-heading text-[15px] font-semibold text-ink tabular">{{ m.enrollments }}</dd>
        </div>
        <div>
          <dt class="text-ink-3">Active in LMS</dt>
          <dd class="font-heading text-[15px] font-semibold text-ink tabular">{{ m.active }}<span class="ml-0.5 text-[11px] font-normal text-ink-3">/ {{ m.enrollments }}</span></dd>
        </div>
        <div>
          <dt class="text-ink-3">Latest quiz avg</dt>
          <dd class="font-heading text-[15px] font-semibold text-ink tabular">{{ pct(m.quiz?.mean_latest_score) }}</dd>
        </div>
      </dl>
      <p class="mt-2 text-[12px] font-semibold text-ink">
        Attendance {{ m.attendance.available ? pct(m.attendance.pct) : 'Not Available' }} · {{ m.attendance.attended }}/{{ m.attendance.expected }} present / known observations
      </p>
      <AttendanceScope :module="m" />
      <div class="mt-3">
        <div class="flex justify-between text-[11px] text-ink-3"><span>Mean LMS progress</span><span class="tabular">{{ pct(m.mean_progress) }}</span></div>
        <div class="mt-1 h-1.5 overflow-hidden rounded-full bg-subtle"><div class="h-full rounded-full bg-series-3" :style="{ width: `${m.mean_progress ?? 0}%` }" /></div>
      </div>
    </button>
  </div>
</template>
