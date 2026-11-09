<script setup lang="ts">
import { computed, ref } from 'vue'
import { downloadModules, exportModules, type ExportFormat } from '@/api/learnerDirectory'

const props = defineProps<{ course: string }>()
const dialog = ref<HTMLDialogElement | null>(null)
const selected = ref<number[]>([])
const format = ref<ExportFormat>('xlsx')
const busy = ref(false)
const error = ref('')
const canDownload = computed(() => selected.value.length > 0 && !busy.value &&
  (format.value === 'xlsx' || selected.value.length === 1))

function open() {
  selected.value = exportModules.some(m => m.id === Number(props.course))
    ? [Number(props.course)] : exportModules.map(m => m.id)
  format.value = 'xlsx'; error.value = ''
  dialog.value?.showModal()
}
function selectionChanged() {
  if (selected.value.length !== 1) format.value = 'xlsx'
  error.value = ''
}
function selectAll(all: boolean) {
  selected.value = all ? exportModules.map(m => m.id) : []
  selectionChanged()
}
async function download() {
  if (!canDownload.value) return
  busy.value = true; error.value = ''
  try {
    const { blob, filename } = await downloadModules(selected.value, format.value)
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url; link.download = filename
    document.body.appendChild(link); link.click(); link.remove()
    window.setTimeout(() => URL.revokeObjectURL(url), 1000)
    dialog.value?.close()
  } catch (e) { error.value = (e as Error).message }
  finally { busy.value = false }
}
</script>

<template>
  <button class="focus-ring rounded-md bg-navy px-4 py-2 text-sm font-semibold text-white hover:opacity-90" @click="open">Export</button>
  <Teleport to="body">
    <dialog ref="dialog" aria-labelledby="learner-export-title" class="m-auto max-h-[90vh] w-[calc(100%_-_2rem)] max-w-md overflow-y-auto rounded-xl border border-line bg-white p-6 text-ink shadow-xl backdrop:bg-slate-900/40" @cancel="busy && $event.preventDefault()">
      <h2 id="learner-export-title" class="text-lg font-semibold">Export learners by module</h2>
      <fieldset :disabled="busy" class="mt-4 space-y-3">
        <legend class="sr-only">Modules</legend>
        <div class="flex gap-3 text-sm">
          <button class="focus-ring rounded text-navy underline" @click="selectAll(true)">Select All</button>
          <button class="focus-ring rounded text-ink-2 underline" @click="selectAll(false)">Clear Selection</button>
        </div>
        <label v-for="module in exportModules" :key="module.id" class="flex items-center gap-2 text-sm">
          <input v-model="selected" type="checkbox" :value="module.id" class="accent-blue-800" @change="selectionChanged" />
          {{ module.label }}
        </label>
        <label class="flex flex-col gap-1 pt-2 text-sm">
          <span class="eyebrow">Format</span>
          <select v-model="format" aria-label="Format" class="focus-ring rounded-md border border-line bg-white p-2">
            <option value="xlsx">Excel (.xlsx)</option>
            <option value="csv" :disabled="selected.length !== 1">CSV (.csv) — one module</option>
          </select>
        </label>
      </fieldset>
      <p v-if="error" role="alert" class="mt-3 text-sm text-red-700">{{ error }}</p>
      <div class="mt-6 flex justify-end gap-2">
        <button class="focus-ring rounded-md border border-line px-3 py-2 text-sm disabled:opacity-50" :disabled="busy" @click="dialog?.close()">Cancel</button>
        <button class="focus-ring rounded-md bg-navy px-3 py-2 text-sm font-semibold text-white disabled:opacity-50" :disabled="!canDownload" @click="download">{{ busy ? 'Preparing…' : format === 'csv' ? 'Download CSV' : 'Download Excel' }}</button>
      </div>
    </dialog>
  </Teleport>
</template>
