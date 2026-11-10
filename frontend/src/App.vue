<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { logout, session } from './api/client'
import Icon from './components/common/Icon.vue'

const route = useRoute()
const router = useRouter()
const isPublic = computed(() => Boolean(route.meta.public))
const section = computed(() => route.meta.section)
const mobileOpen = ref(false)
watch(() => route.fullPath, () => { mobileOpen.value = false })

const nav = [
  { to: '/', label: 'Executive Overview', key: 'overview' },
  { to: '/learners', label: 'Learners', key: 'learners' },
  { to: '/lectures', label: 'Lectures', key: 'lectures' },
]
const initials = computed(() => (session.user?.name ?? '?').replace(/[^A-Za-z ]/g, ' ').trim().split(/\s+/).map((p) => p[0]).slice(0, 2).join('').toUpperCase() || 'U')

async function signOut() {
  await logout()
  router.replace({ name: 'login' })
}
</script>

<template>
  <div v-if="isPublic" class="min-h-screen">
    <RouterView />
  </div>
  <div v-else class="min-h-screen">
    <header class="sticky top-0 z-40 border-b border-line bg-white/90 backdrop-blur-md">
      <div class="mx-auto flex h-16 max-w-[1480px] items-center gap-3 px-4 sm:px-6 lg:px-8">
        <RouterLink to="/" class="focus-ring flex items-center gap-3 rounded-md">
          <span class="grid h-9 w-9 place-items-center rounded-lg bg-navy text-white shadow-sm">
            <svg viewBox="0 0 24 24" class="h-5 w-5" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" aria-hidden="true"><path d="M6 18v-6M12 18V7M18 18v-4" /></svg>
          </span>
          <span class="hidden leading-tight sm:flex sm:flex-col">
            <span class="font-heading text-[13.5px] font-semibold text-ink">Alfanar Learning</span>
            <span class="text-[10px] font-semibold uppercase tracking-[0.14em] text-ink-3">Kent Business College</span>
          </span>
        </RouterLink>

        <nav class="ml-4 hidden items-center gap-1 md:flex" aria-label="Main">
          <RouterLink
            v-for="item in nav" :key="item.key" :to="item.to"
            class="focus-ring rounded-md px-3 py-2 text-[13.5px] transition-colors"
            :class="section === item.key ? 'bg-navy-soft font-semibold text-navy' : 'font-medium text-ink-2 hover:bg-subtle hover:text-ink'"
            :aria-current="section === item.key ? 'page' : undefined"
          >{{ item.label }}</RouterLink>
        </nav>

        <div class="ml-auto flex items-center gap-2">
          <div class="flex items-center gap-2">
            <span class="font-heading grid h-8 w-8 place-items-center rounded-full bg-brand-soft text-[11px] font-semibold text-brand" aria-hidden="true">{{ initials }}</span>
            <span class="hidden max-w-40 truncate text-[13px] font-medium text-ink-2 lg:inline">{{ session.user?.name }}</span>
          </div>
          <button class="focus-ring inline-flex h-9 items-center gap-1.5 rounded-md border border-line bg-white px-2.5 text-[13px] text-ink-2 hover:bg-subtle" @click="signOut">
            <Icon name="logout" :size="15" /><span class="hidden sm:inline">Sign out</span>
          </button>
          <button class="focus-ring grid h-9 w-9 place-items-center rounded-md border border-line bg-white text-ink md:hidden" :aria-expanded="mobileOpen" aria-label="Toggle navigation" @click="mobileOpen = !mobileOpen">
            <Icon :name="mobileOpen ? 'x' : 'layers'" :size="17" />
          </button>
        </div>
      </div>
      <nav v-if="mobileOpen" class="border-t border-line bg-white px-4 py-2 md:hidden" aria-label="Main (mobile)">
        <RouterLink v-for="item in nav" :key="item.key" :to="item.to"
          class="block rounded-md px-3 py-2.5 text-sm" :class="section === item.key ? 'bg-navy-soft font-semibold text-navy' : 'text-ink-2'">{{ item.label }}</RouterLink>
      </nav>
    </header>
    <main class="mx-auto max-w-[1480px] px-4 py-6 sm:px-6 lg:px-8 lg:py-8">
      <RouterView />
    </main>
    <footer class="mx-auto flex max-w-[1480px] flex-wrap items-center gap-2 px-4 pb-8 text-[11.5px] text-ink-3 sm:px-6 lg:px-8">
      <Icon name="shield" :size="13" /> Confidential learner records · read-only view of LMS data · all figures are calculated from imported source records.
    </footer>
  </div>
</template>
