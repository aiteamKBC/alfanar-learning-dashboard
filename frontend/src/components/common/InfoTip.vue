<script setup lang="ts">
import { nextTick, onBeforeUnmount, ref, useId, watch } from 'vue'

/**
 * Definition tooltip. The panel is teleported to <body> with fixed positioning so it is never
 * clipped by overflow-hidden / scrolling ancestors, and it is clamped inside the viewport
 * (flips above the trigger when there is no room below).
 */
defineProps<{ text: string; label?: string }>()

const MARGIN = 8 // min distance from viewport edges (px)
const GAP = 8 // distance from trigger (px)

const open = ref(false)
const placed = ref(false)
const trigger = ref<HTMLElement | null>(null)
const tip = ref<HTMLElement | null>(null)
const pos = ref({ top: 0, left: 0 })
const id = `infotip-${useId()}`
let lastPointer = 'mouse'
let pointerFocus = false // focus caused by a pointer press (the click handler decides), not the keyboard

function onPointerDown(e: PointerEvent) {
  lastPointer = e.pointerType
  pointerFocus = true
}
function onFocus() {
  if (!pointerFocus) open.value = true // keyboard focus opens
  pointerFocus = false
}

function place() {
  const t = trigger.value
  const el = tip.value
  if (!t || !el) return
  const r = t.getBoundingClientRect()
  const vw = document.documentElement.clientWidth
  const vh = window.innerHeight
  const w = el.offsetWidth
  const h = el.offsetHeight
  const left = Math.max(MARGIN, Math.min(r.left + r.width / 2 - w / 2, vw - w - MARGIN))
  let top = r.bottom + GAP
  if (top + h > vh - MARGIN) {
    const above = r.top - GAP - h
    top = above >= MARGIN ? above : Math.max(MARGIN, vh - h - MARGIN)
  }
  pos.value = { top, left }
  placed.value = true
}

function onOutside(e: PointerEvent) {
  const target = e.target as Node
  if (!trigger.value?.contains(target) && !tip.value?.contains(target)) open.value = false
}
function onKey(e: KeyboardEvent) {
  if (e.key === 'Escape') open.value = false
}

watch(open, async (v) => {
  if (v) {
    placed.value = false
    await nextTick()
    place()
    window.addEventListener('scroll', place, { capture: true, passive: true })
    window.addEventListener('resize', place, { passive: true })
    document.addEventListener('pointerdown', onOutside, true)
    document.addEventListener('keydown', onKey)
  } else {
    detach()
  }
})

function detach() {
  window.removeEventListener('scroll', place, { capture: true })
  window.removeEventListener('resize', place)
  document.removeEventListener('pointerdown', onOutside, true)
  document.removeEventListener('keydown', onKey)
}
onBeforeUnmount(detach)

function onClick() {
  // Mouse users already opened it on hover; touch / pen users toggle with a tap.
  pointerFocus = false
  open.value = lastPointer === 'mouse' ? true : !open.value
}
function onEnter(e: PointerEvent) {
  if (e.pointerType === 'mouse') open.value = true
}
function onLeave(e: PointerEvent) {
  if (e.pointerType === 'mouse') open.value = false
}
</script>

<template>
  <span class="relative inline-flex" @pointerenter="onEnter" @pointerleave="onLeave">
    <span
      ref="trigger"
      role="button" tabindex="0"
      class="focus-ring grid h-4 w-4 cursor-help place-items-center rounded-full border border-gray-300 text-[10px] font-semibold leading-none text-ink-3 hover:border-ink-3 hover:text-ink"
      :aria-label="label ?? 'Metric definition'"
      :aria-expanded="open"
      :aria-describedby="open ? id : undefined"
      @pointerdown="onPointerDown"
      @focus="onFocus" @blur="open = false"
      @click.stop.prevent="onClick"
      @keydown.enter.stop.prevent="open = !open" @keydown.space.stop.prevent="open = !open"
    >i</span>
    <Teleport to="body">
      <span
        v-if="open" :id="id" ref="tip" role="tooltip"
        class="fixed z-[100] block max-h-[calc(100vh-16px)] w-[min(18rem,calc(100vw-16px))] overflow-y-auto rounded-lg border border-line bg-white p-3 text-left text-xs font-normal normal-case leading-relaxed tracking-normal text-ink-2 shadow-lg [overflow-wrap:anywhere]"
        :style="{ top: `${pos.top}px`, left: `${pos.left}px`, visibility: placed ? 'visible' : 'hidden' }"
      >{{ text }}</span>
    </Teleport>
  </span>
</template>
