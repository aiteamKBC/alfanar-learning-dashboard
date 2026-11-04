<script setup lang="ts">
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { login } from '@/api/client'
import Icon from '@/components/common/Icon.vue'

const route = useRoute()
const router = useRouter()
const username = ref('')
const password = ref('')
const error = ref<string | null>(null)
const busy = ref(false)

async function submit() {
  busy.value = true
  error.value = null
  try {
    await login(username.value, password.value)
    const next = typeof route.query.next === 'string' && route.query.next.startsWith('/') && !route.query.next.startsWith('//') ? route.query.next : '/'
    router.replace(next)
  } catch (e) {
    error.value = e instanceof Error ? e.message : 'Sign-in failed'
  } finally {
    busy.value = false
    password.value = ''
  }
}
</script>

<template>
  <div class="grid min-h-screen lg:grid-cols-[1.1fr_1fr]">
    <aside class="hero-band relative hidden flex-col justify-between overflow-hidden p-12 text-white lg:flex">
      <div class="flex items-center gap-3">
        <span class="grid h-10 w-10 place-items-center rounded-lg bg-white/10 ring-1 ring-white/20">
          <svg viewBox="0 0 24 24" class="h-5 w-5" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" aria-hidden="true"><path d="M6 18v-6M12 18V7M18 18v-4" /></svg>
        </span>
        <div class="leading-tight">
          <div class="font-heading text-[15px] font-semibold">Alfanar Learning</div>
          <div class="text-[10.5px] font-semibold uppercase tracking-[0.16em] text-blue-100/70">Kent Business College</div>
        </div>
      </div>
      <div class="max-w-md">
        <p class="text-[10.5px] font-semibold uppercase tracking-[0.16em] text-teal-200/90">Learning analytics</p>
        <h1 class="mt-3 text-[34px] font-semibold leading-tight">Engagement and achievement, straight from the LMS.</h1>
        <p class="mt-4 text-[14px] leading-relaxed text-blue-100/80">Executive overview, learner profiles and activity matrices built only from recorded learning data — synchronised read-only from the source.</p>
      </div>
      <p class="flex items-center gap-2 text-[12px] text-blue-100/70"><Icon name="shield" :size="14" />Confidential learner records · authorised staff only</p>
    </aside>

    <div class="grid place-items-center px-4 py-10">
      <div class="w-full max-w-sm">
        <div class="mb-8 flex items-center gap-3 lg:hidden">
          <span class="grid h-10 w-10 place-items-center rounded-lg bg-navy text-white">
            <svg viewBox="0 0 24 24" class="h-5 w-5" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" aria-hidden="true"><path d="M6 18v-6M12 18V7M18 18v-4" /></svg>
          </span>
          <div>
            <div class="font-heading text-[16px] font-semibold text-ink">Alfanar Learning</div>
            <div class="text-[11px] text-ink-3">Kent Business College</div>
          </div>
        </div>
        <h2 class="text-[24px] font-semibold text-ink">Sign in</h2>
        <p class="mt-1 text-sm text-ink-3">Use the dashboard account provided to you.</p>
        <form class="mt-6 space-y-4" @submit.prevent="submit">
          <label class="block">
            <span class="eyebrow">Username</span>
            <input v-model="username" autocomplete="username" required class="focus-ring mt-1.5 h-11 w-full rounded-lg border border-line bg-white px-3 text-sm shadow-sm" />
          </label>
          <label class="block">
            <span class="eyebrow">Password</span>
            <input v-model="password" type="password" autocomplete="current-password" required class="focus-ring mt-1.5 h-11 w-full rounded-lg border border-line bg-white px-3 text-sm shadow-sm" />
          </label>
          <p v-if="error" class="flex items-start gap-2 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-800 ring-1 ring-red-200" role="alert"><Icon name="info" :size="15" class="mt-0.5 shrink-0" />{{ error }}</p>
          <button type="submit" :disabled="busy" class="focus-ring h-11 w-full rounded-lg bg-navy text-sm font-semibold text-white shadow-sm transition hover:bg-navy-2 disabled:opacity-60">
            {{ busy ? 'Signing in…' : 'Sign in' }}
          </button>
        </form>
      </div>
    </div>
  </div>
</template>
