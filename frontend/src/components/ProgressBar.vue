<script setup lang="ts">
// Barra di progresso: determinata (0..1, avanza con transform) o indeterminata (un segmento che scorre) quando il totale non è noto.
withDefaults(defineProps<{ value: number | null; label: string; size?: 'xs' | 'sm' | 'md'; tone?: 'accent' | 'done' | 'failed' }>(),
  { size: 'sm', tone: 'accent' })
</script>

<template>
  <span class="progress" :class="[size, tone, { indeterminate: value === null }]" role="progressbar" :aria-label="label"
        aria-valuemin="0" aria-valuemax="100" :aria-valuenow="value === null ? undefined : Math.round(value * 100)">
    <span class="fill" :style="value === null ? undefined : { transform: `scaleX(${Math.max(0, Math.min(1, value))})` }" />
  </span>
</template>

<style scoped>
.progress { position: relative; display: block; width: 100%; overflow: hidden; border-radius: var(--radius-pill); background: var(--bg-sunken); }
.progress.xs { height: 2px; border-radius: 0; background: transparent; }
.progress.sm { height: 4px; }
.progress.md { height: 6px; }
.fill {
  position: absolute; inset: 0; border-radius: inherit; background: var(--accent); transform-origin: left center;
  transition: transform var(--dur-slow) var(--ease-out);
}
.done .fill { background: var(--match-solid); }
.failed .fill { background: var(--deviates-solid); }
/* luce che scorre sulla parte già fatta: si vede che sta lavorando anche quando il numero cambia lentamente */
.progress:not(.indeterminate):not(.done):not(.failed) .fill::after {
  content: ''; position: absolute; inset: 0;
  background: linear-gradient(90deg, transparent 0%, var(--progress-sheen) 50%, transparent 100%);
  background-size: 200% 100%; animation: shimmer 1.8s linear infinite;
}
.indeterminate .fill { right: auto; width: 35%; animation: indeterminate 1.4s var(--ease-in-out) infinite; }
@keyframes indeterminate { from { transform: translateX(-100%); } to { transform: translateX(290%); } }
@media (prefers-reduced-motion: reduce) {
  .fill, .fill::after { animation: none !important; transition: none; }
  .indeterminate .fill { width: 100%; opacity: 0.5; }
}
</style>
