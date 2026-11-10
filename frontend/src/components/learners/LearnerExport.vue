<script setup lang="ts">
import { ref } from 'vue'
import { downloadDirectory, type DirectoryExportFilters } from '@/api/learnerDirectory'

const props = defineProps<DirectoryExportFilters>()
const busy = ref(false)
const error = ref('')
async function download() {
  if (busy.value) return
  busy.value = true; error.value = ''
  try {
    const { blob, filename } = await downloadDirectory({ ...props })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url; link.download = filename
    document.body.appendChild(link); link.click(); link.remove()
    window.setTimeout(() => URL.revokeObjectURL(url), 1000)
  } catch (e) { error.value = (e as Error).message }
  finally { busy.value = false }
}
</script>

<template>
  <button type="button" class="focus-ring rounded-md bg-navy px-4 py-2 text-sm font-semibold text-white hover:opacity-90 disabled:opacity-50" :disabled="busy" :aria-busy="busy" @click="download">{{ busy ? 'Preparing…' : 'Export' }}</button>
  <p v-if="error" role="alert" class="text-sm text-red-700">{{ error }}</p>
</template>
