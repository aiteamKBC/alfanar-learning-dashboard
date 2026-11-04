<script setup lang="ts">
/** Small inline stroke-icon set (no external icon font). */
const props = withDefaults(defineProps<{ name: string; size?: number; stroke?: number }>(), { size: 18, stroke: 1.8 })

const PATHS: Record<string, string> = {
  users: 'M16 19v-1.5a3.5 3.5 0 0 0-3.5-3.5h-5A3.5 3.5 0 0 0 4 17.5V19M10 10.5a3 3 0 1 0 0-6 3 3 0 0 0 0 6M20 19v-1.5a3.5 3.5 0 0 0-2.5-3.35M15.5 4.6a3 3 0 0 1 0 5.8',
  layers: 'M12 3 3 8l9 5 9-5-9-5ZM3 13l9 5 9-5M3 17.5l9 5 9-5',
  pulse: 'M3 12h4l2.5-6 5 12L17 12h4',
  gauge: 'M12 21a9 9 0 1 1 9-9M12 12l4-4M3.5 15h3M17.5 15h3',
  quiz: 'M9 4h6l1 2h3v15H5V6h3l1-2ZM9 12l2 2 4-4M9 17h6',
  target: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18ZM12 16a4 4 0 1 0 0-8 4 4 0 0 0 0 8ZM12 12h.01',
  award: 'M12 15a6 6 0 1 0 0-12 6 6 0 0 0 0 12ZM8.5 14 7 21l5-3 5 3-1.5-7',
  upload: 'M12 16V4M7 9l5-5 5 5M5 20h14',
  book: 'M4 5.5A2.5 2.5 0 0 1 6.5 3H20v15H6.5A2.5 2.5 0 0 0 4 20.5v-15ZM4 20.5A2.5 2.5 0 0 0 6.5 23H20v-5',
  coverage: 'M4 4h7v7H4zM13 4h7v7h-7zM4 13h7v7H4zM16.5 13v7M13 16.5h7',
  search: 'M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16ZM21 21l-4.35-4.35',
  chevron: 'm9 6 6 6-6 6',
  down: 'm6 9 6 6 6-6',
  refresh: 'M20 11a8 8 0 0 0-14.9-4M4 4v4h4M4 13a8 8 0 0 0 14.9 4M20 20v-4h-4',
  clock: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18ZM12 7v5l3 2',
  database: 'M12 8c4.4 0 8-1.3 8-3s-3.6-3-8-3-8 1.3-8 3 3.6 3 8 3ZM4 5v14c0 1.7 3.6 3 8 3s8-1.3 8-3V5M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3',
  sheet: 'M5 3h10l4 4v14H5zM14 3v5h5M8 12h8M8 16h8M12 12v8',
  shield: 'M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10Z',
  info: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18ZM12 11v5M12 8h.01',
  video: 'M4 6h11v12H4zM15 10l5-3v10l-5-3',
  pdf: 'M6 3h9l4 4v14H6zM14 3v5h5M9 14h6M9 17h4',
  audio: 'M4 14v-2a8 8 0 0 1 16 0v2M4 14h3v6H4zM17 14h3v6h-3z',
  reading: 'M2 5h7a3 3 0 0 1 3 3v12a2 2 0 0 0-2-2H2zM22 5h-7a3 3 0 0 0-3 3v12a2 2 0 0 1 2-2h8z',
  assignment: 'M9 4h6v3H9zM6 5H5v16h14V5h-1M9 13l2 2 4-4',
  logout: 'M15 4h4v16h-4M10 8l-4 4 4 4M6 12h10',
  filter: 'M3 5h18l-7 8v6l-4 2v-8z',
  check: 'm5 12 5 5 9-10',
  x: 'M6 6l12 12M18 6 6 18',
  matrix: 'M3 4h18v16H3zM3 9h18M3 14h18M9 4v16',
}
</script>

<template>
  <svg :width="props.size" :height="props.size" viewBox="0 0 24 24" fill="none" stroke="currentColor" :stroke-width="props.stroke" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
    <path :d="PATHS[props.name] ?? PATHS.info" />
  </svg>
</template>
