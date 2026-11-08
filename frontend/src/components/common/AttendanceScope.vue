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
      <template v-else-if="module.delivered_lectures.source === 'owner_confirmed_plus_verified'">
        {{ module.delivered_lectures.count }} delivered lectures ·
      </template>
      {{ module.delivered_sessions ?? 'Not Available' }} sessions with attendance evidence.
    </p>
    <p class="text-ink-2">
      Module evidence coverage within dated sessions: {{ pct(module.attendance.coverage_pct) }}
      ({{ ratio(module.attendance.expected, module.attendance.possible_learner_sessions) }} known / possible learner-session observations).
    </p>
  </div>
</template>
