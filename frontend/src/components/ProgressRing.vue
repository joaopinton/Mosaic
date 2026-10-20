<script setup lang="ts">
import { computed } from 'vue'

// Anello di progresso compatto (sidebar, anche compressa): pieno in proporzione, oppure un arco che gira se il totale non è noto.
const props = withDefaults(defineProps<{ value: number | null; size?: number; stroke?: number; label: string }>(), { size: 20, stroke: 2.5 })
const radius = computed(() => (props.size - props.stroke) / 2)
const length = computed(() => 2 * Math.PI * radius.value)
const offset = computed(() => (props.value === null ? length.value * 0.72 : length.value * (1 - Math.max(0, Math.min(1, props.value)))))
</script>

<template>
  <svg class="ring" :class="{ spinning: value === null }" :width="size" :height="size" :viewBox="`0 0 ${size} ${size}`"
       role="progressbar" :aria-label="label" aria-valuemin="0" aria-valuemax="100"
       :aria-valuenow="value === null ? undefined : Math.round(value * 100)">
    <circle class="track" :cx="size / 2" :cy="size / 2" :r="radius" :stroke-width="stroke" fill="none" />
    <circle class="arc" :cx="size / 2" :cy="size / 2" :r="radius" :stroke-width="stroke" fill="none"
            :stroke-dasharray="length" :stroke-dashoffset="offset" stroke-linecap="round" />
  </svg>
</template>

<style scoped>
.ring { flex: none; transform: rotate(-90deg); }
.track { stroke: var(--border-default); }
.arc { stroke: var(--accent); transition: stroke-dashoffset var(--dur-slow) var(--ease-out); }
.spinning { animation: ring-spin 1s linear infinite; }
@keyframes ring-spin { from { transform: rotate(-90deg); } to { transform: rotate(270deg); } }
@media (prefers-reduced-motion: reduce) { .spinning { animation: none; } }
</style>
