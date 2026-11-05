<script setup lang="ts">
import type { ModuleProgress } from '@/api/types'
import { pct, ratio } from '@/utils/format'

defineProps<{ module: ModuleProgress }>()
</script>

<template>
  <div class="mt-2 space-y-1 text-[11px] leading-relaxed" data-attendance-scope>
    <p class="text-ink-2">
      <template v-if="module.delivered_lectures.source === 'owner_confirmed'">
        {{ module.delivered_lectures.count }} owner-confirmed delivered lectures ·
      </template>
      {{ module.delivered_sessions ?? 'Not Available' }} sessions with attendance evidence.
    </p>
    <p v-if="module.delivered_lectures.source !== 'owner_confirmed'" class="text-ink-3">
      Lecture count comes from attendance evidence; total delivery is not independently confirmed.
    </p>
    <p class="text-ink-2">
      Module evidence coverage within dated sessions: {{ pct(module.attendance.coverage_pct) }}
      ({{ ratio(module.attendance.expected, module.attendance.possible_learner_sessions) }} known / possible learner-session observations).
    </p>
    <p v-if="module.delivered_lectures.differs_from_teams" class="rounded-md border border-amber-200 bg-amber-50 px-2 py-1.5 font-medium text-amber-900">
      Only {{ module.delivered_sessions ?? 'Not Available' }} of {{ module.delivered_lectures.count }} confirmed lectures have attendance data.
      The attendance percentage applies only to known observations, not all {{ module.delivered_lectures.count }} lectures.
    </p>
    <p v-else class="text-ink-3">Attendance uses known observations only. Missing attendance is excluded.</p>
  </div>
</template>
