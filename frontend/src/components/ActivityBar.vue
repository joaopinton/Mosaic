<script setup lang="ts">
import { computed } from 'vue'
import { formatCountOf, formatRemaining } from '../lib/activity'
import { openActivity, sidebar } from '../lib/store'
import { useActivity } from '../lib/useActivity'
import Icon from './Icon.vue'
import ProgressBar from './ProgressBar.vue'

// Barra in cima al contenuto mentre Mosaic lavora: fase, «128 di 450 foto», tempo rimanente, percentuale. Apre il pannello Attività.
const { current, step, remaining, pipeline } = useActivity()
const eta = computed(() => formatRemaining(remaining(current.value)))
const count = computed(() => (current.value ? formatCountOf(current.value) : ''))
const percent = computed(() => (current.value?.fraction === null || !current.value ? null : Math.round(current.value.fraction * 100)))
const queued = computed(() => pipeline.value.filter((s) => s.state === 'queued').length)
</script>

<template>
  <Transition name="activity">
    <button v-if="current" type="button" class="activity" data-test="activity-bar" :aria-expanded="sidebar.panel === 'activity'"
            @click="openActivity">
      <span class="badge" aria-hidden="true"><Icon :name="current.icon" :size="15" /></span>
      <span class="text">
        <span class="title">
          {{ current.label }}
          <span v-if="step" class="step">passo {{ step.index }} di {{ step.of }}</span>
        </span>
        <span class="detail truncate">
          <template v-if="count">{{ count }}</template>
          <template v-else>{{ current.message || 'in preparazione…' }}</template>
          <template v-if="eta"> · {{ eta }}</template>
          <template v-if="queued"> · poi altre {{ queued }} {{ queued === 1 ? 'fase' : 'fasi' }}</template>
        </span>
      </span>
      <span v-if="percent !== null" class="pct mono">{{ percent }}%</span>
      <span class="more">Dettagli <Icon name="chevronRight" :size="14" /></span>
      <ProgressBar class="track" size="xs" :value="current.fraction" :label="`${current.label}: avanzamento`" />
    </button>
  </Transition>
</template>

<style scoped>
.activity {
  position: relative; display: flex; align-items: center; gap: var(--space-3); width: 100%; min-height: 3rem;
  padding: var(--space-2) var(--page-pad); border: 0; border-bottom: 1px solid var(--border-subtle);
  background: var(--bg-selected); color: var(--fg-default); font: inherit; text-align: left; cursor: pointer;
  transition: background-color var(--dur-fast) var(--ease-out);
}
.activity:hover { background: var(--bg-hover); }
.activity:hover .more { color: var(--accent-text); }
.badge { display: grid; place-items: center; width: 1.9rem; height: 1.9rem; flex: none; border-radius: var(--radius-control); background: var(--accent-soft); color: var(--accent-soft-fg); }
.text { display: flex; flex: 1; flex-direction: column; min-width: 0; line-height: 1.25; }
.title { display: flex; align-items: baseline; gap: var(--space-2); font-size: var(--text-sm); font-weight: var(--weight-semibold); }
.step { color: var(--fg-muted); font-size: var(--text-xs); font-weight: var(--weight-regular); }
.detail { color: var(--fg-muted); font-size: var(--text-xs); }
.pct { flex: none; color: var(--accent-text); font-size: var(--text-lg); font-weight: var(--weight-semibold); font-variant-numeric: tabular-nums; }
.more { display: inline-flex; align-items: center; gap: var(--space-1); flex: none; color: var(--fg-muted); font-size: var(--text-xs); transition: color var(--dur-fast) var(--ease-out); }
.track { position: absolute; left: 0; right: 0; bottom: -1px; }
.activity-enter-active, .activity-leave-active { transition: opacity var(--dur-base) var(--ease-out), transform var(--dur-base) var(--ease-out); }
.activity-enter-from, .activity-leave-to { opacity: 0; transform: translateY(-6px); }
@media (max-width: 40rem) { .more, .step { display: none; } }
</style>
