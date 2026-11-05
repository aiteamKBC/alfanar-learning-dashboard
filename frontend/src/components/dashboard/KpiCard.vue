<script setup lang="ts">
import Icon from '@/components/common/Icon.vue'
import InfoTip from '@/components/common/InfoTip.vue'

withDefaults(defineProps<{
  label: string
  value: string
  sub?: string
  notice?: string
  info?: string
  meter?: number | null
  accent?: string
  icon?: string
  tone?: 'navy' | 'teal' | 'blue' | 'neutral'
  featured?: boolean
}>(), { tone: 'neutral', icon: 'pulse' })

const BUBBLE = {
  navy: 'bg-navy-soft text-navy',
  teal: 'bg-brand-soft text-brand',
  blue: 'bg-blue-50 text-[#1f62b5]',
  neutral: 'bg-subtle text-ink-2',
}
</script>

<template>
  <article
    class="fade-up relative flex min-w-0 flex-col overflow-hidden rounded-[14px] border p-4 shadow-[var(--shadow-card)]"
    :class="featured ? 'border-teal-200 bg-gradient-to-br from-white to-brand-soft/60' : 'border-line bg-white'"
  >
    <span v-if="featured" class="pointer-events-none absolute -right-8 -top-10 h-24 w-24 rounded-full bg-brand-soft/80" aria-hidden="true" />
    <div class="relative flex items-start justify-between gap-3">
      <div class="min-w-0">
        <div class="eyebrow flex items-center gap-1.5">
          <span class="truncate">{{ label }}</span>
          <InfoTip v-if="info" :text="info" :label="`About: ${label}`" />
        </div>
        <div class="font-heading mt-2 text-[27px] font-semibold leading-none tracking-tight text-ink tabular">{{ value }}</div>
      </div>
      <span class="grid h-9 w-9 shrink-0 place-items-center rounded-lg" :class="BUBBLE[tone]">
        <Icon :name="icon" :size="18" />
      </span>
    </div>
    <div v-if="meter !== undefined && meter !== null" class="relative mt-3 h-1.5 overflow-hidden rounded-full bg-subtle" aria-hidden="true">
      <div class="h-full rounded-full transition-[width] duration-700" :style="{ width: `${Math.min(100, Math.max(0, meter))}%`, background: accent ?? '#1baf7a' }" />
    </div>
    <p v-if="sub" class="relative mt-2 text-[11.5px] leading-snug text-ink-3">{{ sub }}</p>
    <p v-if="notice" class="relative mt-2 rounded-md border border-amber-200 bg-amber-50 px-2 py-1.5 text-[11.5px] leading-relaxed text-amber-900" data-attendance-notice>{{ notice }}</p>
  </article>
</template>
