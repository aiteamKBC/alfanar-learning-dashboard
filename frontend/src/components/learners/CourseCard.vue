<script setup lang="ts">
import type { CourseProgress, ModuleProgress } from '@/api/types'
import AttendanceScope from '@/components/common/AttendanceScope.vue'
import Icon from '@/components/common/Icon.vue'
import ProgressRing from '@/components/dashboard/ProgressRing.vue'
import { dateTime, pct } from '@/utils/format'

defineProps<{ course: CourseProgress; selected: boolean; module?: ModuleProgress }>()
defineEmits<{ select: [] }>()
</script>

<template>
  <button
    type="button"
    class="focus-ring fade-up group w-full rounded-[14px] border bg-white p-4 text-left shadow-[var(--shadow-card)] transition hover:-translate-y-0.5"
    :class="selected ? 'border-transparent ring-2 ring-navy' : 'border-line hover:border-line-strong'"
    :aria-pressed="selected"
    @click="$emit('select')"
  >
    <div class="flex items-start gap-3">
      <ProgressRing :value="course.progress_pct" :size="64" :stroke="6" :label="`LMS progress ${pct(course.progress_pct)}`" />
      <div class="min-w-0 flex-1">
        <div class="eyebrow">LMS progress</div>
        <h3 class="mt-0.5 text-[13.5px] font-semibold leading-snug text-ink">{{ course.course }}</h3>
        <span v-if="!course.has_activity" class="mt-1 inline-block rounded-full bg-subtle px-2 py-0.5 text-[10.5px] text-ink-3">No recorded activity</span>
      </div>
    </div>
    <dl class="mt-3 grid grid-cols-3 gap-2 border-t border-line pt-3 text-[11.5px]">
      <div>
        <dt class="text-ink-3">Materials</dt>
        <dd class="tabular font-semibold text-ink">{{ course.lessons_completed }}<span class="font-normal text-ink-3"> / {{ course.lessons_known }}</span></dd>
      </div>
      <div>
        <dt class="text-ink-3">Quizzes</dt>
        <dd class="tabular font-semibold text-ink">{{ course.quizzes_attempted }}<span class="font-normal text-ink-3"> / {{ course.quizzes_known }}</span></dd>
      </div>
      <div>
        <dt class="text-ink-3">Latest score</dt>
        <dd class="tabular font-semibold text-ink">{{ pct(course.mean_latest_score) }}</dd>
      </div>
    </dl>
    <div class="mt-3">
      <div class="flex justify-between text-[11px] text-ink-3">
        <span>Attendance evidence</span>
        <span class="tabular">
          <template v-if="course.attendance.available">{{ course.attendance.attended }} / {{ course.attendance.measured ?? course.attendance.delivered }} known · {{ pct(course.attendance.pct) }}</template>
          <template v-else>Not Available</template>
        </span>
      </div>
      <p v-if="course.attendance.missing" class="mt-1 text-[10.5px] text-amber-800">{{ course.attendance.missing }} sessions have no attendance evidence</p>
      <div class="mt-1 h-1.5 overflow-hidden rounded-full bg-subtle"><div class="h-full rounded-full bg-navy" :style="{ width: `${course.attendance.pct ?? 0}%` }" /></div>
      <p class="mt-1 text-[10.5px] text-ink-3">Learner rate: known observations only; missing attendance is excluded.</p>
      <AttendanceScope v-if="module" :module="module" />
    </div>
    <div class="mt-3 flex items-center justify-between text-[11.5px]">
      <span class="text-ink-3">Last activity {{ dateTime(course.last_activity, false) }}</span>
      <span class="inline-flex items-center gap-1 font-semibold" :class="selected ? 'text-navy' : 'text-series-1'">
        <Icon name="matrix" :size="13" />{{ selected ? 'Viewing matrix' : 'Open matrix' }}
      </span>
    </div>
  </button>
</template>
