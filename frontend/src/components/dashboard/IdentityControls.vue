<script setup lang="ts">
import { computed, ref, nextTick } from 'vue'
import { api } from '@/api/client'

interface Original { source_id: number; name: string; modules: number[]; archived: boolean; in_source: boolean; primary_id: number; group_id: string | null }
interface Registry { ready: boolean; originals: Original[]; history: { id: string; kind: string; at: string; revision: number; primary_id?: number; source_ids: number[] }[] }
interface Preview { token: string; kind: string; originals: { source_id: number; name: string; modules: number[] }[]; resulting_dashboard: Record<string, number>; affected_source_enrollments: number; retained_timing_records: number; retained_matrix_records: number; matrix_conflicts_for_review: number; module_counts: Record<string, number>[]; rules: string }
const props = defineProps<{ selected: number[]; visibleIds: number[]; modules: { course_id: number; course: string }[] }>()
const emit = defineEmits<{ selection: [number[]]; changed: [] }>()
const open = ref(false), busy = ref(false), error = ref('')
const dialogElement = ref<HTMLElement | null>(null)
let previousFocus: HTMLElement | null = null
const registry = ref<Registry | null>(null)
const draft = ref<Preview | null>(null)
const kind = ref(''), ids = ref<number[]>([]), primary = ref<number | undefined>()
const selectedOriginals = computed(() => registry.value?.originals.filter(r => ids.value.includes(r.primary_id) && r.in_source) ?? [])
const merged = computed(() => registry.value?.originals.filter(r => r.group_id && r.source_id === r.primary_id && !r.archived) ?? [])
const archived = computed(() => registry.value?.originals.filter(r => r.archived && r.source_id === r.primary_id) ?? [])
function moduleName(cid: number) { return props.modules.find(m => m.course_id === cid)?.course ?? String(cid) }
async function manage(action = '', selection = props.selected) {
  if (!open.value) previousFocus = document.activeElement as HTMLElement | null
  open.value = true; busy.value = true; error.value = ''; draft.value = null
  await nextTick(); dialogElement.value?.focus()
  kind.value = action; ids.value = [...selection]; primary.value = undefined
  try {
    registry.value = await api.get<Registry>('identities')
    if (!registry.value.ready) throw new Error('Original identity restoration must be completed first.')
    primary.value = selectedOriginals.value[0]?.source_id
    if (action && action !== 'merge') await preview()
  } catch (e) { error.value = (e as Error).message }
  finally { busy.value = false }
}
async function preview() {
  busy.value = true; error.value = ''; draft.value = null
  try { draft.value = await api.post<Preview>('identities/preview', { kind: kind.value, learner_ids: ids.value, primary_id: primary.value }) }
  catch (e) { error.value = (e as Error).message }
  finally { busy.value = false }
}
async function confirm() {
  if (!draft.value) return
  busy.value = true; error.value = ''
  try {
    await api.post('identities/confirm', { token: draft.value.token })
    emit('selection', []); emit('changed'); open.value = false; previousFocus?.focus()
  } catch (e) { error.value = (e as Error).message }
  finally { busy.value = false }
}
function close() { if (!busy.value) { open.value = false; previousFocus?.focus() } }
function trapFocus(e: KeyboardEvent) {
  if (e.key !== 'Tab') return
  const controls = [...(dialogElement.value?.querySelectorAll<HTMLElement>('button:not(:disabled), select:not(:disabled), summary, [href], input:not(:disabled)') ?? [])]
    .filter(el => el.getClientRects().length > 0)
  const first = controls[0], last = controls[controls.length - 1]
  if (!first) { e.preventDefault(); return }
  if (e.shiftKey && (document.activeElement === first || document.activeElement === dialogElement.value)) { e.preventDefault(); last?.focus() }
  else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus() }
}
function identityLabel(id: number) { return `${registry.value?.originals.find(r => r.source_id === id)?.name ?? 'Original identity'} (#${id})` }
</script>

<template>
  <div class="mb-3 flex flex-wrap items-center gap-2 text-xs" data-identity-controls>
    <button class="focus-ring rounded border border-line px-2 py-1.5" @click="emit('selection', [...new Set([...selected, ...visibleIds])])">Select All</button>
    <button class="focus-ring rounded border border-line px-2 py-1.5" :disabled="!selected.length" @click="emit('selection', [])">Clear Selection</button>
    <span class="text-ink-3" aria-live="polite">{{ selected.length }} selected</span>
    <button class="focus-ring rounded border border-line px-2 py-1.5 disabled:opacity-40" :disabled="selected.length < 2" @click="manage('merge')">Merge</button>
    <button class="focus-ring rounded border border-line px-2 py-1.5 disabled:opacity-40" :disabled="!selected.length" @click="manage('archive')">Delete</button>
    <button class="focus-ring rounded border border-line px-2 py-1.5" @click="manage()">Manage identities</button>
  </div>
  <Teleport to="body">
    <div v-if="open" class="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-3" @click.self="close" @keydown.esc="close">
      <section ref="dialogElement" @keydown="trapFocus" role="dialog" aria-modal="true" aria-labelledby="identity-title" tabindex="-1" class="max-h-[90vh] w-full max-w-2xl overflow-auto rounded-xl bg-white p-5 shadow-xl" data-identity-dialog>
        <div class="mb-4 flex items-center justify-between gap-3">
          <h2 id="identity-title" class="text-lg font-semibold">{{ kind === 'archive' ? 'Delete learners' : kind ? `${kind.charAt(0).toUpperCase()}${kind.slice(1)} learners` : 'Manage identities' }}</h2>
          <button class="focus-ring rounded px-2 py-1" :disabled="busy" aria-label="Close identity dialog" @click="close">✕</button>
        </div>
        <p v-if="error" role="alert" class="mb-3 text-sm text-red-700">{{ error }}</p>
        <p v-if="busy" role="status" class="text-sm text-ink-3">Checking identity records…</p>
        <template v-if="kind && registry">
          <ul class="mb-4 space-y-2 text-sm">
            <li v-for="r in draft?.originals ?? selectedOriginals" :key="r.source_id">
              <span class="font-medium">{{ r.name }}</span> <span class="text-ink-3">#{{ r.source_id }}</span>
              <div class="text-xs text-ink-3">{{ r.modules.map(moduleName).join(' · ') }}</div>
            </li>
          </ul>
          <label v-if="kind === 'merge'" class="mb-3 block text-sm">Primary learner
            <select v-model="primary" class="focus-ring mt-1 w-full rounded border border-line p-2" :disabled="busy" @change="draft = null">
              <option v-for="r in selectedOriginals" :key="r.source_id" :value="r.source_id">{{ r.name }} (#{{ r.source_id }})</option>
            </select>
          </label>
          <template v-if="draft">
            <dl class="mb-3 grid grid-cols-2 gap-2 text-sm">
              <template v-for="(value, key) in draft.resulting_dashboard" :key="key"><dt>{{ String(key).replaceAll('_', ' ') }}</dt><dd class="tabular">{{ value }}</dd></template>
              <dt>Source enrollments affected</dt><dd>{{ draft.affected_source_enrollments }}</dd>
              <dt>Timing records retained</dt><dd>{{ draft.retained_timing_records }}</dd>
              <dt>Matrix records retained</dt><dd>{{ draft.retained_matrix_records }}</dd>
              <dt>Matrix conflicts for review</dt><dd>{{ draft.matrix_conflicts_for_review }}</dd>
            </dl>
            <details class="mb-3 text-xs"><summary class="cursor-pointer">Result by module</summary>
              <div v-for="m in draft.module_counts" :key="m.course_id" class="mt-2 rounded border border-line p-2">
                <strong>{{ moduleName(m.course_id!) }}</strong>
                <dl class="mt-1 grid grid-cols-2 gap-1"><template v-for="(value, field) in m" :key="field"><template v-if="field !== 'course_id'"><dt>{{ String(field).replaceAll('_', ' ') }}</dt><dd>{{ value }}</dd></template></template></dl>
              </div>
            </details>
            <p class="mb-4 text-xs text-ink-3">{{ draft.rules }}</p>
            <button class="focus-ring rounded bg-navy px-4 py-2 text-sm text-white disabled:opacity-50" :disabled="busy" @click="confirm">Confirm {{ kind === 'archive' ? 'Delete (archive)' : kind }}</button>
          </template>
          <button v-else-if="!busy" class="focus-ring rounded border border-line px-4 py-2 text-sm" @click="preview">Preview changes</button>
        </template>
        <template v-else-if="registry && !busy">
          <h3 class="mb-2 font-medium">Merged identities</h3>
          <div v-for="r in merged" :key="r.source_id" class="mb-2 flex items-center justify-between gap-3 text-sm"><span>{{ r.name }} (#{{ r.source_id }})</span><button class="focus-ring rounded border border-line px-2 py-1" @click="manage('unmerge', [r.primary_id])">Unmerge</button></div>
          <p v-if="!merged.length" class="text-sm text-ink-3">No active merges.</p>
          <h3 class="mb-2 mt-4 font-medium">Archived learners</h3>
          <div v-for="r in archived" :key="r.source_id" class="mb-2 flex items-center justify-between gap-3 text-sm"><span>{{ r.name }} (#{{ r.source_id }})</span><button class="focus-ring rounded border border-line px-2 py-1" @click="manage('restore', [r.primary_id])">Restore</button></div>
          <p v-if="!archived.length" class="text-sm text-ink-3">No archived learners.</p>
          <details class="mt-4 text-xs"><summary class="cursor-pointer">History</summary><ul class="mt-2 space-y-1"><li v-for="h in registry.history" :key="h.id"><div v-if="h.source_ids.length" class="text-ink-3">{{ h.source_ids.map(identityLabel).join(' / ') }}<span v-if="h.primary_id"> / Primary: {{ identityLabel(h.primary_id) }}</span></div>{{ h.kind.replaceAll('_', ' ') }} · {{ new Date(h.at).toLocaleString() }} · version {{ h.revision }}</li></ul></details>
        </template>
      </section>
    </div>
  </Teleport>
</template>
