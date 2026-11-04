<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue'
import { api } from '@/api/client'
import type { SyncStatus } from '@/api/types'
import { useAsync } from '@/api/useAsync'
import { dateTime, relative } from '@/utils/format'
import Icon from './Icon.vue'
import InfoTip from './InfoTip.vue'

/** Data freshness panel. Refreshes every minute so a live Google sync is reflected without reload. */
const { data, loading, error, reload } = useAsync(() => api.get<SyncStatus>('sync/status'))
const timer = window.setInterval(() => reload(), 60_000)
onBeforeUnmount(() => window.clearInterval(timer))

const showDetails = ref(false)
const ok = computed(() => data.value?.last_success)
const last = computed(() => data.value?.last_run)
const failedSinceOk = computed(() => Boolean(last.value && last.value.status === 'failed' && last.value.id !== ok.value?.id))
const isGoogle = computed(() => ok.value?.source_type === 'google')
const sourceLabel = computed(() => (!ok.value ? 'No successful import yet' : isGoogle.value ? 'Google Sheets · live, read-only' : `Excel workbook · ${ok.value.source_ref}`))
const liveLabel = computed(() => {
  const g = data.value?.google_sheets
  if (!g) return '—'
  if (!g.enabled) return 'Live sync disabled'
  return `Live sync every ${Math.round(g.interval_seconds / 60)} min`
})
</script>

<template>
  <section class="panel overflow-hidden" aria-label="Data freshness">
    <div v-if="loading && !data" class="skeleton m-4 h-10" />
    <p v-else-if="error && !data" class="p-4 text-sm text-red-700">Sync status unavailable: {{ error }}</p>
    <template v-else>
      <div class="grid gap-px bg-line sm:grid-cols-2 xl:grid-cols-5">
        <div class="flex items-center gap-3 bg-white px-4 py-3">
          <span class="grid h-8 w-8 shrink-0 place-items-center rounded-lg" :class="isGoogle ? 'bg-brand-soft text-brand' : 'bg-navy-soft text-navy'"><Icon :name="isGoogle ? 'sheet' : 'database'" :size="16" /></span>
          <div class="min-w-0">
            <div class="eyebrow">Data source</div>
            <div class="truncate text-[13px] font-semibold text-ink">{{ sourceLabel }}</div>
          </div>
        </div>
        <div class="bg-white px-4 py-3">
          <div class="eyebrow">Last successful sync</div>
          <div class="text-[13px] font-semibold text-ink tabular">{{ dateTime(ok?.finished_at) }}</div>
          <div class="text-[11px] text-ink-3">{{ relative(ok?.finished_at) }}</div>
        </div>
        <div class="bg-white px-4 py-3">
          <div class="eyebrow flex items-center gap-1">Latest recorded activity
            <InfoTip text="Most recent lesson start or quiz attempt timestamp in the imported records — the real freshness of learner data (interpreted as UTC)." />
          </div>
          <div class="text-[13px] font-semibold text-ink tabular">{{ dateTime(ok?.latest_activity_at) }}</div>
          <div class="text-[11px] text-ink-3">{{ relative(ok?.latest_activity_at) }}</div>
        </div>
        <div class="bg-white px-4 py-3">
          <div class="eyebrow flex items-center gap-1">Source file timestamp
            <InfoTip text="Last-modified time of the Excel workbook. Not reported for Google Sheets, which would require an extra Drive permission; use 'Latest recorded activity' instead." />
          </div>
          <div class="text-[13px] font-semibold text-ink tabular">{{ isGoogle ? 'Not reported by Sheets' : dateTime(ok?.source_modified_at) }}</div>
        </div>
        <div class="flex items-center justify-between gap-3 bg-white px-4 py-3">
          <div>
            <div class="eyebrow">Sync status</div>
            <div class="mt-0.5 flex items-center gap-1.5 text-[13px] font-semibold" :class="failedSinceOk ? 'text-red-700' : 'text-emerald-700'">
              <span class="relative flex h-2 w-2">
                <span v-if="!failedSinceOk && data?.google_sheets.enabled" class="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-60" />
                <span class="relative inline-flex h-2 w-2 rounded-full" :class="failedSinceOk ? 'bg-red-600' : 'bg-emerald-600'" />
              </span>
              {{ failedSinceOk ? 'Last attempt failed' : 'Healthy' }}
            </div>
            <div class="text-[11px] text-ink-3">{{ liveLabel }}</div>
          </div>
          <button v-if="ok?.warnings?.length || failedSinceOk" class="focus-ring shrink-0 rounded-md px-2 py-1 text-[11.5px] font-medium text-series-1 hover:bg-blue-50" :aria-expanded="showDetails" @click="showDetails = !showDetails">
            {{ showDetails ? 'Hide notes' : 'Import notes' }}
          </button>
        </div>
      </div>
      <div v-if="failedSinceOk" class="border-t border-red-100 bg-red-50/60 px-4 py-2 text-[12px] text-red-800">
        The latest sync attempt failed ({{ dateTime(last?.finished_at) }}). The dashboard continues to show the last successful dataset.
      </div>
      <ul v-if="showDetails" class="space-y-1 border-t border-line bg-subtle/60 px-4 py-3 text-[12px] text-ink-2">
        <li v-for="w in ok?.warnings" :key="w">• {{ w }}</li>
        <li v-for="e in failedSinceOk ? last?.errors : []" :key="e" class="text-red-700">• {{ e }}</li>
      </ul>
    </template>
  </section>
</template>
