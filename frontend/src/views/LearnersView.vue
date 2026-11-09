<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, qs } from '@/api/client'
import type { FiltersResponse, LearnerRow, Paginated } from '@/api/types'
import { useAsync } from '@/api/useAsync'
import FilterSelect from '@/components/common/FilterSelect.vue'
import Icon from '@/components/common/Icon.vue'
import StateBlock from '@/components/common/StateBlock.vue'
import LearnerTable from '@/components/learners/LearnerTable.vue'
import LearnerExport from '@/components/learners/LearnerExport.vue'

const route = useRoute()
const router = useRouter()
const q = (key: string, d = '') => (typeof route.query[key] === 'string' ? (route.query[key] as string) : d)

function setQuery(patch: Record<string, string | undefined>, resetPage = true) {
  const next: Record<string, string | undefined> = { ...(route.query as Record<string, string>), ...patch }
  if (resetPage) delete next.page
  for (const key of Object.keys(next)) if (!next[key]) delete next[key]
  router.replace({ query: next })
}

const course = computed({ get: () => q('course'), set: (v: string) => setQuery({ course: v }) })
const engagement = computed({ get: () => q('engagement'), set: (v: string) => setQuery({ engagement: v }) })
const ordering = computed(() => q('ordering', 'name'))
const page = computed(() => Number(q('page', '1')) || 1)

// Debounced search box bound to the URL
const searchInput = ref(q('search'))
const search = computed(() => q('search'))
let timer: number | undefined
watch(searchInput, (v) => {
  window.clearTimeout(timer)
  if (v.trim() === search.value) return
  timer = window.setTimeout(() => setQuery({ search: v.trim() || undefined }), 250)
})
watch(search, (v) => {
  window.clearTimeout(timer)
  searchInput.value = v
})
onBeforeUnmount(() => window.clearTimeout(timer))

const filters = useAsync(() => api.get<FiltersResponse>('filters'))
const list = useAsync(
  () => api.get<Paginated<LearnerRow>>(`learners${qs({ search: search.value, course: course.value, engagement: engagement.value, ordering: ordering.value, page: page.value, page_size: 20 })}`),
  [search, course, engagement, ordering, page],
)

function sortBy(key: string) {
  const cur = ordering.value
  const next = cur === key ? `-${key}` : cur === `-${key}` ? key : (key === 'name' ? key : `-${key}`)
  setQuery({ ordering: next === 'name' ? undefined : next })
}
function goPage(p: number) { setQuery({ page: p > 1 ? String(p) : undefined }, false) }
</script>

<template>
  <div class="space-y-5">
    <div class="flex flex-wrap items-end gap-3">
      <div class="mr-auto">
        <p class="eyebrow">Learners</p>
        <h1 class="mt-1 text-[26px] font-semibold leading-tight text-ink">Learner directory</h1>
        <p class="mt-1 text-sm text-ink-3">Search enrolled learners, then open a profile for module progress and the detailed activity matrix.</p>
      </div>
      <span v-if="list.data.value" class="rounded-full bg-navy-soft px-3 py-1 text-[12px] font-semibold text-navy tabular">{{ list.data.value.count }} learners</span>
      <LearnerExport :course="course" />
    </div>

    <div class="panel flex flex-wrap items-end gap-3 p-4">
      <label class="flex min-w-60 flex-1 flex-col gap-1">
        <span class="eyebrow">Search</span>
        <span class="relative">
          <Icon name="search" :size="15" class="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-ink-3" />
          <input v-model="searchInput" type="search" placeholder="Learner name or ID" class="focus-ring h-9 w-full rounded-md border border-line bg-white pl-9 pr-3 text-sm" />
        </span>
      </label>
      <FilterSelect v-model="course" label="Module" all-label="All modules" :options="(filters.data.value?.courses ?? []).map((x) => ({ value: x.course_id, label: x.title }))" />
      <FilterSelect v-model="engagement" label="Learner status" all-label="All learners" :options="filters.data.value?.engagement ?? []" />
    </div>

    <p v-if="course" class="text-xs text-ink-3">Figures in each row describe the selected module only.</p>

    <StateBlock :loading="list.loading.value && !list.data.value" :error="list.error.value" :empty="list.data.value?.count === 0" empty-title="No learners match" height="24rem" @retry="list.reload">
      <div :class="{ 'opacity-60 transition-opacity': list.loading.value }">
        <LearnerTable :rows="list.data.value?.results ?? []" :ordering="ordering" @sort="sortBy" />
        <div v-if="list.data.value" class="mt-3 flex flex-wrap items-center gap-3 text-sm text-ink-2">
          <span>
            Showing {{ (list.data.value.page - 1) * list.data.value.page_size + 1 }}–{{ Math.min(list.data.value.count, list.data.value.page * list.data.value.page_size) }}
            of {{ list.data.value.count }} learners
          </span>
          <div class="ml-auto flex items-center gap-1">
            <button class="focus-ring rounded-md border border-line px-3 py-1.5 hover:bg-gray-50 disabled:opacity-40" :disabled="list.data.value.page <= 1" @click="goPage(list.data.value.page - 1)">Previous</button>
            <span class="px-2 tabular">Page {{ list.data.value.page }} / {{ list.data.value.pages }}</span>
            <button class="focus-ring rounded-md border border-line px-3 py-1.5 hover:bg-gray-50 disabled:opacity-40" :disabled="list.data.value.page >= list.data.value.pages" @click="goPage(list.data.value.page + 1)">Next</button>
          </div>
        </div>
      </div>
    </StateBlock>
  </div>
</template>
