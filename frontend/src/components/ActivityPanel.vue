<script setup lang="ts">
import { computed } from 'vue'
import { formatAgo, formatCountOf, formatRemaining, QUIET_TYPES, stageInfo, type StageSummary } from '../lib/activity'
import { api } from '../lib/api'
import { RESTARTABLE, STATE_LABELS } from '../lib/jobs'
import { jobs, refreshJobs, uploads } from '../lib/store'
import { errorMessage, toast } from '../lib/toast'
import { useActivity } from '../lib/useActivity'
import Icon from './Icon.vue'
import ProgressBar from './ProgressBar.vue'
import ProgressRing from './ProgressRing.vue'

// Pannello Attività della sidebar (come i Filtri): la pipeline come passi, le altre attività, i caricamenti e lo storico recente.
const emit = defineEmits<{ close: [] }>()
const { pipeline, others, upload, current, busy, remaining, now } = useActivity()

const headline = computed(() => (current.value ? current.value.label : 'Tutto aggiornato'))
const extra = computed(() => [...(upload.value ? [upload.value] : []), ...others.value])
const recent = computed(() => jobs.value
  .filter((j) => j.state !== 'queued' && j.state !== 'running' && (!QUIET_TYPES.has(j.type) || j.state === 'failed')).slice(0, 8))
const failedUploads = computed(() => uploads.value.filter((u) => u.state === 'failed'))

function sub(stage: StageSummary): string {
  if (stage.state === 'running') {
    const parts = [formatCountOf(stage) || stage.message, formatRemaining(remaining(stage))].filter(Boolean)
    return parts.join(' · ') || 'in corso…'
  }
  if (stage.state === 'queued') return stage.total ? `in coda · ${formatCountOf(stage)}` : 'in coda'
  if (stage.state === 'done') return stage.message || 'fatto'
  if (stage.state === 'failed') return stage.jobs[0]?.error || 'non riuscita'
  return '—'
}

async function cancel(stage: StageSummary): Promise<void> {
  try {
    await api.cancelJobType(stage.type) // sul server: anche i blocchi in coda che qui non sono elencati
    await refreshJobs()
    toast(`${stage.label}: annullata`)
  } catch (error) {
    toast(errorMessage(error), 'error')
  }
}

const canRestart = (stage: StageSummary): boolean =>
  RESTARTABLE.has(stage.type) && (stage.state === 'running' || stage.state === 'queued' || stage.state === 'failed')

async function restart(stage: StageSummary): Promise<void> {
  try {
    await api.restartJobType(stage.type) // ferma l'intera fase e la rimette in coda da capo
    await refreshJobs()
    toast(`${stage.label}: riavviata`)
  } catch (error) {
    toast(errorMessage(error), 'error')
  }
}
</script>

<template>
  <div class="panel-activity">
    <header class="head">
      <button type="button" class="btn ghost icon sm" aria-label="Torna alla navigazione" @click="emit('close')"><Icon name="chevronLeft" /></button>
      <h2>Attività</h2>
    </header>

    <section class="hero" :class="{ idle: !busy }" aria-live="polite">
      <span v-if="busy" class="hero-ring">
        <ProgressRing :value="current?.fraction ?? null" :size="56" :stroke="5" :label="`${headline}: avanzamento`" />
        <span v-if="current?.fraction !== null && current" class="hero-pct mono">{{ Math.round((current.fraction ?? 0) * 100) }}</span>
      </span>
      <span v-else class="hero-ok"><Icon name="check" :size="26" /></span>
      <span class="hero-text">
        <strong>{{ headline }}</strong>
        <span v-if="current" class="muted">{{ sub(current) }}</span>
        <span v-else class="muted">Mosaic ha finito: niente in coda.</span>
      </span>
    </section>

    <section aria-labelledby="act-pipeline">
      <h3 id="act-pipeline" class="caps">Elaborazione della libreria</h3>
      <ol class="steps">
        <li v-for="stage in pipeline" :key="stage.type" class="stage" :data-state="stage.state">
          <span class="marker" aria-hidden="true">
            <Icon v-if="stage.state === 'done'" name="check" :size="13" />
            <ProgressRing v-else-if="stage.state === 'running'" :value="stage.fraction" :size="22" :stroke="2.5" label="" />
            <Icon v-else-if="stage.state === 'failed'" name="alert" :size="13" />
            <Icon v-else :name="stage.icon" :size="13" />
          </span>
          <span class="body">
            <span class="row-top">
              <strong>{{ stage.label }}</strong>
              <span class="state">{{ stage.state === 'idle' ? '' : STATE_LABELS[stage.state === 'done' ? 'done' : stage.state] }}</span>
            </span>
            <span class="detail">{{ sub(stage) }}</span>
            <ProgressBar v-if="stage.state === 'running'" :value="stage.fraction" :label="`${stage.label}: avanzamento`" />
          </span>
          <span class="actions">
            <button v-if="canRestart(stage)" type="button" class="btn ghost icon sm" data-test="stage-restart"
                    :aria-label="`Riavvia ${stage.label}`" :title="`Riavvia ${stage.label}`" @click="restart(stage)"><Icon name="refresh" /></button>
            <button v-if="stage.state === 'running' || stage.state === 'queued'" type="button" class="btn ghost icon sm"
                    :aria-label="`Annulla ${stage.label}`" :title="`Annulla ${stage.label}`" @click="cancel(stage)"><Icon name="x" /></button>
          </span>
        </li>
      </ol>
    </section>

    <section v-if="extra.length || failedUploads.length" aria-labelledby="act-other">
      <h3 id="act-other" class="caps">Altre attività</h3>
      <ul class="list">
        <li v-for="stage in extra" :key="stage.type" class="item">
          <span class="item-icon"><Icon :name="stage.icon" :size="15" /></span>
          <span class="body">
            <span class="row-top"><strong>{{ stage.label }}</strong><span class="state">{{ STATE_LABELS[stage.state] ?? '' }}</span></span>
            <span class="detail">{{ sub(stage) }}</span>
            <ProgressBar v-if="stage.state === 'running'" :value="stage.fraction" :label="`${stage.label}: avanzamento`" />
          </span>
          <span class="actions">
            <button v-if="canRestart(stage)" type="button" class="btn ghost icon sm" :aria-label="`Riavvia ${stage.label}`"
                    :title="`Riavvia ${stage.label}`" @click="restart(stage)"><Icon name="refresh" /></button>
            <button v-if="stage.jobs.length" type="button" class="btn ghost icon sm" :aria-label="`Annulla ${stage.label}`"
                    :title="`Annulla ${stage.label}`" @click="cancel(stage)"><Icon name="x" /></button>
          </span>
        </li>
        <li v-for="u in failedUploads" :key="u.id" class="item failed">
          <span class="item-icon"><Icon name="alert" :size="15" /></span>
          <span class="body"><strong class="truncate">{{ u.name }}</strong><span class="detail">{{ u.error }}</span></span>
        </li>
      </ul>
    </section>

    <section v-if="recent.length" aria-labelledby="act-recent">
      <h3 id="act-recent" class="caps">Recenti</h3>
      <ul class="list recent">
        <li v-for="j in recent" :key="j.id" class="item" :data-state="j.state">
          <span class="dot" aria-hidden="true" />
          <span class="body">
            <span class="row-top"><strong>{{ stageInfo(j.type).label }}</strong><span class="state">{{ formatAgo(j.finished_at, now) }}</span></span>
            <span v-if="j.error" class="detail error-text">{{ j.error }}</span>
            <span v-else-if="j.message" class="detail">{{ j.message }}</span>
          </span>
        </li>
      </ul>
    </section>
  </div>
</template>

<style scoped>
.panel-activity { display: flex; flex-direction: column; gap: var(--space-5); height: 100%; min-height: 0; padding: var(--space-3) var(--space-4) var(--space-6); overflow-y: auto; background: var(--bg-sidebar); border-radius: inherit; }
.head { display: flex; align-items: center; gap: var(--space-2); }
.head h2 { font-size: var(--text-lg); }
h3 { margin-bottom: var(--space-2); }
.hero { display: flex; align-items: center; gap: var(--space-4); padding: var(--space-4); border-radius: var(--radius-panel); background: var(--bg-selected); }
.hero.idle { background: var(--match-bg); }
.hero-ring { position: relative; display: grid; place-items: center; flex: none; }
.hero-pct { position: absolute; font-size: var(--text-sm); font-weight: var(--weight-bold); color: var(--accent-text); }
.hero-ok { display: grid; place-items: center; width: 56px; height: 56px; flex: none; border-radius: 50%; background: var(--match-solid); color: var(--fg-on-accent); }
.hero-text { display: flex; flex-direction: column; gap: var(--space-1); min-width: 0; font-size: var(--text-sm); }
.hero-text strong { font-size: var(--text-lg); }
.steps { position: relative; display: flex; flex-direction: column; margin: 0; padding: 0; list-style: none; }
.stage { position: relative; display: flex; align-items: flex-start; gap: var(--space-3); padding: var(--space-2) 0; }
.stage:not(:last-child)::before {
  content: ''; position: absolute; left: 11px; top: calc(var(--space-2) + 24px); bottom: calc(-1 * var(--space-2) + 2px);
  width: 2px; border-radius: 1px; background: var(--border-subtle);
}
.stage[data-state='done']:not(:last-child)::before { background: var(--match-solid); }
.marker { position: relative; z-index: 1; display: grid; place-items: center; width: 24px; height: 24px; flex: none; border: 2px solid var(--border-default); border-radius: 50%; background: var(--bg-surface); color: var(--fg-subtle); }
.stage[data-state='done'] .marker { border-color: var(--match-solid); background: var(--match-solid); color: var(--fg-on-accent); }
.stage[data-state='running'] .marker { border-color: transparent; background: var(--bg-surface); }
.stage[data-state='queued'] .marker { border-style: dashed; border-color: var(--accent); color: var(--accent-text); }
.stage[data-state='failed'] .marker { border-color: var(--deviates-solid); color: var(--deviates-fg); }
.stage[data-state='idle'] { opacity: 0.6; }
.body { display: flex; flex: 1; flex-direction: column; gap: var(--space-1); min-width: 0; font-size: var(--text-sm); }
.actions { display: flex; flex: none; gap: var(--space-1); }
.row-top { display: flex; align-items: baseline; justify-content: space-between; gap: var(--space-2); }
.state { color: var(--fg-subtle); font-size: var(--text-xs); white-space: nowrap; }
.stage[data-state='running'] .state { color: var(--accent-text); font-weight: var(--weight-medium); }
.detail { color: var(--fg-muted); font-size: var(--text-xs); overflow-wrap: anywhere; }
.list { display: flex; flex-direction: column; gap: var(--space-3); margin: 0; padding: 0; list-style: none; }
.item { display: flex; align-items: flex-start; gap: var(--space-3); }
.item-icon { display: grid; place-items: center; width: 24px; height: 24px; flex: none; border-radius: var(--radius-control); background: var(--accent-soft); color: var(--accent-soft-fg); }
.item.failed .item-icon { background: var(--deviates-bg); color: var(--deviates-fg); }
.dot { width: 8px; height: 8px; margin-top: 6px; flex: none; border-radius: 50%; background: var(--border-strong); }
.recent [data-state='done'] .dot, .recent .item[data-state='done'] .dot { background: var(--match-solid); }
.recent .item[data-state='failed'] .dot { background: var(--deviates-solid); }
</style>
