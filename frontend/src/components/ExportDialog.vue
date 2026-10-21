<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { api, ApiError } from '../lib/api'
import { jobs, refreshJobs } from '../lib/store'
import { errorMessage, toast } from '../lib/toast'
import type { CropFormat, EnhanceOptions, ExportPreviewShot } from '../lib/types'
import Dialog from './Dialog.vue'
import ExportCrop from './ExportCrop.vue'
import Icon from './Icon.vue'
import ProgressBar from './ProgressBar.vue'
import Segmented from './Segmented.vue'

// Esporta in una cartella (D-30): copie degli originali o, con «Migliora luce e colori» e «Ritaglia a formato» (D-34),
// JPEG corretti e ritagliati. Il ritaglio si accende all'apertura solo se lo chiede `crop`.
const props = defineProps<{ open: boolean; uids: string[]; title: string; crop?: boolean }>()
const emit = defineEmits<{ close: [] }>()

const dest = ref('')
const raw = ref(false)
const videos = ref(true)
const improve = ref(false)
const cropOn = ref(false)
const cropFormat = ref<CropFormat | null>(null)
const look = reactive<EnhanceOptions>({ light: true, contrast: true, vivid: true, harmonize: true, strength: 'medium' })
const canPick = ref(true)
const shots = ref<ExportPreviewShot[]>([])
const previewing = ref(false)
const created = ref<{ job: number; folder: string } | null>(null)
const starting = ref(false)
const STRENGTHS = [{ value: 'light', label: 'Leggera' }, { value: 'medium', label: 'Media' }, { value: 'strong', label: 'Forte' }] as const

const job = computed(() => (created.value ? jobs.value.find((j) => j.id === created.value?.job) ?? null : null))
const phase = computed<'edit' | 'working' | 'done' | 'failed'>(() => {
  if (!created.value) return 'edit'
  if (job.value?.state === 'done') return 'done'
  if (job.value?.state === 'failed' || job.value?.state === 'cancelled') return 'failed'
  return 'working'
})
const fraction = computed(() => (job.value && job.value.total ? job.value.done / job.value.total : null))
const count = computed(() => props.uids.length)
const workingLabel = computed(() => {
  if (cropOn.value) return improve.value ? 'Ritaglio, miglioro e salvo le foto…' : 'Ritaglio e salvo le foto…'
  return improve.value ? 'Miglioro e copio le foto…' : 'Copio le foto…'
})
const canStart = computed(() => !!dest.value.trim() && !starting.value && count.value > 0 && (!cropOn.value || !!cropFormat.value))

watch(() => props.open, async (open) => {
  if (!open) return
  created.value = null
  shots.value = []
  cropOn.value = props.crop === true
  try {
    dest.value = (await api.exportDefaultDir(props.title)).folder
  } catch {
    dest.value = ''
  }
}, { immediate: true })

let previewTimer: ReturnType<typeof setTimeout> | undefined
let generation = 0
watch(() => [props.open, improve.value, props.uids, { ...look }], () => {
  clearTimeout(previewTimer)
  if (!props.open || !improve.value) return
  previewTimer = setTimeout(() => void loadPreview(), 300)
}, { deep: true })
onBeforeUnmount(() => clearTimeout(previewTimer))

async function loadPreview(): Promise<void> {
  const mine = ++generation
  previewing.value = true
  try {
    const { photos } = await api.exportPreview(props.uids, { ...look })
    if (mine === generation) shots.value = photos
  } catch (error) {
    if (mine === generation) toast(errorMessage(error), 'error')
  } finally {
    if (mine === generation) previewing.value = false
  }
}

async function pick(): Promise<void> {
  try {
    const { folder } = await api.pickFolder(dest.value)
    if (folder) dest.value = folder
  } catch (error) {
    if (error instanceof ApiError && error.status === 501) {
      canPick.value = false
      toast('Qui non posso aprire la finestra delle cartelle: scrivi il percorso nel campo.')
    } else {
      toast(errorMessage(error), 'error')
    }
  }
}

async function start(): Promise<void> {
  if (!canStart.value) return
  starting.value = true
  const crop = cropOn.value ? cropFormat.value : null
  try {
    created.value = await api.exportPhotos({
      uids: props.uids, dest: dest.value.trim(), raw: raw.value && !crop, videos: videos.value && !crop,
      enhance: improve.value ? { ...look } : null, crop,
    })
    void refreshJobs()
  } catch (error) {
    toast(errorMessage(error), 'error')
  } finally {
    starting.value = false
  }
}

async function reveal(id = created.value?.job): Promise<void> {
  if (id === undefined) return
  try {
    await api.revealExport(id)
  } catch (error) {
    toast(errorMessage(error), 'error')
  }
}

// chiuso il dialogo mentre lavora: l'avviso arriva lo stesso, con la cartella a un clic
watch(phase, (now) => {
  if (props.open || !created.value) return
  const id = created.value.job
  if (now === 'done') toast(job.value?.message || 'Esportazione finita', 'default', { label: 'Apri la cartella', run: () => void reveal(id) }, 10000)
  if (now === 'failed') toast(`Esportazione non riuscita: ${job.value?.error || 'errore sconosciuto'}`, 'error')
})
</script>

<template>
  <Dialog :open="open" title="Esporta in una cartella" size="lg" @close="emit('close')">
    <div v-if="phase === 'edit'" class="export">
      <div class="field">
        <label for="ex-dest">Cartella</label>
        <div class="dest">
          <input id="ex-dest" v-model="dest" class="input mono" autocomplete="off" spellcheck="false" data-test="export-dest" />
          <button v-if="canPick" type="button" class="btn" data-test="export-pick" @click="pick"><Icon name="folderOpen" /> Scegli…</button>
        </div>
        <p class="hint">{{ count }} foto. Se la cartella non c'è la creo; i file con lo stesso nome non vengono mai sovrascritti.</p>
      </div>

      <fieldset class="checks">
        <legend class="caps">Cosa copiare</legend>
        <label><input v-model="videos" type="checkbox" :disabled="cropOn" /> Anche i video</label>
        <label><input v-model="raw" type="checkbox" :disabled="cropOn" /> Anche RAW e XMP</label>
      </fieldset>

      <section class="improve" :class="{ on: cropOn }">
        <label class="switch-row">
          <input v-model="cropOn" type="checkbox" data-test="export-crop" />
          <span><strong><Icon name="crop" :size="15" /> Ritaglia a formato</strong>
            <span class="hint">Le copie escono tutte nello stesso formato, ritagliate sui soggetti; gli originali restano come sono.</span></span>
        </label>
        <ExportCrop v-if="cropOn" v-model="cropFormat" :uids="uids" />
      </section>

      <section class="improve" :class="{ on: improve }">
        <label class="switch-row">
          <input v-model="improve" type="checkbox" data-test="export-improve" />
          <span><strong><Icon name="sparkle" :size="15" /> Migliora luce e colori</strong>
            <span class="hint">Le copie diventano JPEG corretti; gli originali nella libreria restano come sono.</span></span>
        </label>
        <template v-if="improve">
          <fieldset class="checks">
            <label><input v-model="look.light" type="checkbox" /> Più luce sul soggetto</label>
            <label><input v-model="look.contrast" type="checkbox" /> Contrasto automatico</label>
            <label><input v-model="look.vivid" type="checkbox" /> Colori più vivi</label>
            <label><input v-model="look.harmonize" type="checkbox" /> Tono coordinato tra le foto</label>
          </fieldset>
          <div class="field">
            <span class="label">Intensità</span>
            <Segmented v-model="look.strength" :options="[...STRENGTHS]" label="Intensità del miglioramento" />
          </div>
          <div class="previews" :class="{ busy: previewing }" aria-live="polite">
            <p v-if="previewing && !shots.length" class="hint"><Icon name="spinner" class="spin" :size="14" /> Preparo l'anteprima…</p>
            <figure v-for="s in shots" :key="s.uid" class="pair">
              <img :src="s.before" alt="Prima" />
              <img :src="s.after" alt="Dopo" />
              <figcaption class="hint"><span>Prima</span><span>Dopo</span></figcaption>
            </figure>
          </div>
        </template>
      </section>
    </div>

    <div v-else-if="phase === 'working'" class="status" role="status">
      <Icon name="spinner" class="spin" :size="22" />
      <strong>{{ workingLabel }}</strong>
      <ProgressBar size="md" :value="fraction" label="Esportazione: avanzamento" />
      <span class="hint">{{ job?.message || 'in coda' }}</span>
      <span class="hint">Puoi chiudere: ti avviso quando ho finito.</span>
    </div>

    <div v-else-if="phase === 'done' && created" class="status done">
      <span class="ok"><Icon name="check" :size="22" /></span>
      <strong>Esportazione finita</strong>
      <span class="hint">{{ job?.message }}</span>
      <div class="actions">
        <button type="button" class="btn primary" data-test="export-reveal" @click="reveal()"><Icon name="folderOpen" /> Apri la cartella</button>
        <button type="button" class="btn" @click="emit('close')">Chiudi</button>
      </div>
    </div>

    <div v-else class="status" role="alert">
      <strong>Non sono riuscito a esportare le foto</strong>
      <span class="error-text">{{ job?.error || 'errore sconosciuto' }}</span>
      <button type="button" class="btn" @click="created = null">Riprova</button>
    </div>

    <template v-if="phase === 'edit'" #footer>
      <button type="button" class="btn" @click="emit('close')">Annulla</button>
      <button type="button" class="btn primary" data-test="export-start" :disabled="!canStart" @click="start">
        <Icon name="download" /> Esporta {{ count }} foto
      </button>
    </template>
  </Dialog>
</template>

<style scoped>
.export { display: flex; flex-direction: column; gap: var(--space-4); }
.dest { display: flex; gap: var(--space-2); }
.dest .input { flex: 1; min-width: 0; }
.checks { display: grid; grid-template-columns: 1fr 1fr; gap: var(--space-2); margin: 0; padding: 0; border: 0; font-size: var(--text-sm); }
.checks legend { grid-column: 1 / -1; padding: 0 0 var(--space-1); }
.checks label { display: flex; align-items: center; gap: var(--space-2); }
.improve { display: flex; flex-direction: column; gap: var(--space-3); padding: var(--space-3) var(--space-4); border: 1px solid var(--border-default);
  border-radius: var(--radius-panel); transition: background-color var(--dur-fast) var(--ease-out), border-color var(--dur-fast) var(--ease-out); }
.improve.on { border-color: transparent; background: var(--bg-selected); }
.switch-row { display: flex; align-items: flex-start; gap: var(--space-2); cursor: pointer; }
.switch-row input { margin-top: 0.25rem; }
.switch-row > span { display: flex; flex-direction: column; gap: var(--space-1); }
.switch-row strong { display: inline-flex; align-items: center; gap: var(--space-1-5); }
.previews { display: grid; grid-template-columns: repeat(auto-fill, minmax(15rem, 1fr)); gap: var(--space-3); transition: opacity var(--dur-fast) var(--ease-out); }
.previews.busy { opacity: 0.55; }
.pair { display: grid; grid-template-columns: 1fr 1fr; gap: var(--space-1); margin: 0; }
.pair img { width: 100%; aspect-ratio: 1; object-fit: cover; border-radius: var(--radius-chip); background: var(--bg-sunken); }
.pair figcaption { display: contents; }
.pair figcaption span { text-align: center; }
.status { display: flex; flex-direction: column; align-items: flex-start; gap: var(--space-3); padding: var(--space-4); border-radius: var(--radius-panel); background: var(--bg-selected); }
.status.done { background: var(--match-bg); }
.ok { display: grid; place-items: center; width: 2.5rem; height: 2.5rem; border-radius: 50%; background: var(--match-solid); color: var(--fg-on-accent); }
.actions { display: flex; flex-wrap: wrap; gap: var(--space-2); }
@media (max-width: 40rem) { .checks { grid-template-columns: 1fr; } .dest { flex-wrap: wrap; } }
</style>
