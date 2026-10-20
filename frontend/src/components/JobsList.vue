<script setup lang="ts">
import { api } from '../lib/api'
import { formatCountOf, stageInfo } from '../lib/activity'
import { jobLabel, RESTARTABLE, STATE_LABELS } from '../lib/jobs'
import { jobs, refreshJobs } from '../lib/store'
import { errorMessage, toast } from '../lib/toast'
import Icon from './Icon.vue'
import ProgressBar from './ProgressBar.vue'

// Si ferma e si riavvia l'intera fase: i metadati sono divisi in tanti blocchi e fermarne uno solo non bastava.
async function cancel(type: string): Promise<void> {
  try {
    await api.cancelJobType(type)
    await refreshJobs()
  } catch (error) {
    toast(errorMessage(error), 'error')
  }
}

async function restart(type: string): Promise<void> {
  try {
    await api.restartJobType(type)
    await refreshJobs()
    toast(`${jobLabel(type)}: riavviata`)
  } catch (error) {
    toast(errorMessage(error), 'error')
  }
}
</script>

<template>
  <p v-if="!jobs.length" class="subtle">Nessuna attività recente.</p>
  <ul v-else class="jobs" aria-label="Attività">
    <li v-for="j in jobs.slice(0, 12)" :key="j.id" class="job">
      <span class="dot" :class="j.state" aria-hidden="true" />
      <span class="grow">
        <strong>{{ jobLabel(j.type) }}</strong>
        <span class="subtle small"> · {{ STATE_LABELS[j.state] }}<template v-if="j.message"> · {{ j.message }}</template></span>
        <span v-if="j.error" class="error-text small"> {{ j.error }}</span>
        <span v-if="j.state === 'running' && j.total" class="subtle small mono">{{ formatCountOf({ done: j.done, total: j.total, unit: stageInfo(j.type).unit }) }}</span>
        <ProgressBar v-if="j.state === 'running'" :value="j.total ? j.done / j.total : (j.progress || null)" :label="`${jobLabel(j.type)}: avanzamento`" />
      </span>
      <button v-if="RESTARTABLE.has(j.type) && j.state !== 'done'" type="button" class="btn ghost icon sm" data-test="job-restart"
              :aria-label="`Riavvia ${jobLabel(j.type)}`" :title="`Riavvia ${jobLabel(j.type)}`" @click="restart(j.type)"><Icon name="refresh" /></button>
      <button v-if="j.state === 'queued' || j.state === 'running'" type="button" class="btn ghost icon sm" data-test="job-cancel"
              :aria-label="`Ferma ${jobLabel(j.type)}`" :title="`Ferma ${jobLabel(j.type)}`" @click="cancel(j.type)"><Icon name="x" /></button>
    </li>
  </ul>
</template>

<style scoped>
.jobs { display: grid; gap: var(--space-2); margin: 0; padding: 0; list-style: none; }
.job { display: flex; align-items: center; gap: var(--space-3); font-size: var(--text-sm); }
.grow { display: grid; gap: var(--space-1); min-width: 0; }
.small { font-size: var(--text-xs); }
.dot { width: 8px; height: 8px; flex: none; border-radius: 50%; background: var(--border-strong); }
.dot.running { background: var(--accent); animation: pulse-dot 1.6s var(--ease-in-out) infinite; }
.dot.done { background: var(--match-solid); }
.dot.failed { background: var(--deviates-solid); }
.dot.queued { background: var(--to-verify-solid); }
</style>
