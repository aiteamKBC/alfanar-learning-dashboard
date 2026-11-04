import { ref, shallowRef, watch, type WatchSource } from 'vue'

/** Re-runs `fn` whenever `deps` change; exposes loading / error / data and ignores stale responses. */
export function useAsync<T>(fn: () => Promise<T>, deps: WatchSource[] = []) {
  const data = shallowRef<T | null>(null)
  const loading = ref(true)
  const error = ref<string | null>(null)
  let seq = 0
  async function run() {
    const id = ++seq
    loading.value = true
    error.value = null
    try {
      const result = await fn()
      if (id === seq) data.value = result
    } catch (e) {
      if (id === seq) error.value = e instanceof Error ? e.message : String(e)
    } finally {
      if (id === seq) loading.value = false
    }
  }
  watch(deps, run, { immediate: true, deep: true })
  return { data, loading, error, reload: run }
}
