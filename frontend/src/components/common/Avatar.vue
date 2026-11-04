<script setup lang="ts">
import { computed } from 'vue'

const props = withDefaults(defineProps<{ name: string; size?: number }>(), { size: 36 })
const PALETTE = [['#dff5f1', '#0f766e'], ['#e9eef7', '#142c52'], ['#e8f1fc', '#1f62b5'], ['#f3efe6', '#7a5b14'], ['#eef3e8', '#3f6212']]
const initials = computed(() => {
  const parts = props.name.replace(/(alfanar)?[-_.@]*com$/i, '').replace(/[^A-Za-z؀-ۿ]+/g, ' ').trim().split(/\s+/)
  return (parts.map((p) => p[0]).slice(0, 2).join('') || '?').toUpperCase()
})
const colors = computed(() => PALETTE[[...props.name].reduce((a, ch) => a + ch.charCodeAt(0), 0) % PALETTE.length])
</script>

<template>
  <span class="font-heading inline-grid shrink-0 place-items-center rounded-full font-semibold"
    :style="{ width: `${size}px`, height: `${size}px`, fontSize: `${Math.round(size * 0.36)}px`, background: colors[0], color: colors[1] }" aria-hidden="true">{{ initials }}</span>
</template>
