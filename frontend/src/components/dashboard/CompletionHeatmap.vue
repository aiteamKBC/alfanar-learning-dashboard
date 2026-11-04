<script setup lang="ts">
import { computed } from 'vue'
import type { ActivityCompletion, Kind } from '@/api/types'
import { KIND_LABEL, num, pct, rampColor, rampText } from '@/utils/format'

/** Module × activity-type completion rates as an HTML heatmap (sequential blue). */
const props = defineProps<{ rows: ActivityCompletion[] }>()
const kinds = computed(() => {
  const ks = new Set<string>()
  props.rows.forEach((r) => Object.keys(r.by_type).forEach((k) => ks.add(k)))
  return [...ks] as Kind[]
})
</script>

<template>
  <div class="overflow-x-auto">
    <table class="w-full min-w-[520px] border-separate border-spacing-[3px] text-sm">
      <thead>
        <tr class="text-xs font-medium text-ink-3">
          <th class="px-2 py-1 text-left font-medium">Module</th>
          <th v-for="k in kinds" :key="k" class="px-2 py-1 text-center font-medium">{{ KIND_LABEL[k] }}</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="r in rows" :key="r.course_id">
          <th scope="row" class="max-w-56 truncate px-2 py-1 text-left font-normal text-ink" :title="r.course">{{ r.course }}</th>
          <td
            v-for="k in kinds" :key="k"
            class="h-11 rounded-md px-2 text-center tabular"
            :style="r.by_type[k]?.pct === null || !r.by_type[k] ? { background: '#f8f9fb', color: '#94a3b8' } : { background: rampColor(r.by_type[k].pct as number), color: rampText(r.by_type[k].pct as number) }"
            :title="r.by_type[k]?.known_items ? `${num(r.by_type[k].completed)} of ${num(r.by_type[k].possible)} possible (${r.by_type[k].known_items} known ${KIND_LABEL[k].toLowerCase()} items × enrolled learners)` : `No ${KIND_LABEL[k].toLowerCase()} items recorded for this module`"
          >
            <span v-if="r.by_type[k]?.pct !== null && r.by_type[k]" class="text-[13px] font-semibold">{{ pct(r.by_type[k].pct, 0) }}</span>
            <span v-else class="text-xs">n/a</span>
          </td>
        </tr>
      </tbody>
    </table>
    <div class="mt-3 flex items-center gap-2 text-xs text-ink-3">
      <span>0%</span>
      <span class="h-2 w-40 rounded-full" style="background: linear-gradient(90deg, #cde2fb, #6da7ec, #2a78d6, #104281)" />
      <span>100%</span>
      <span class="ml-3">n/a = no items of that type appear for the module</span>
    </div>
  </div>
</template>
