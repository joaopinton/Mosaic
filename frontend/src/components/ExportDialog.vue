<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { api, ApiError, exportDownloadUrl } from '../lib/api'
import { jobs, refreshJobs } from '../lib/store'
import { errorMessage, toast } from '../lib/toast'
import type { CropFormat, EnhanceOptions, ExportPreviewShot } from '../lib/types'
import Dialog from './Dialog.vue'
import ExportCrop from './ExportCrop.vue'
import Icon from './Icon.vue'
import ProgressBar from './ProgressBar.vue'
import Segmented from './Segmented.vue'

// Un solo dialogo per scaricare ed esportare le foto, da qualunque pagina (D-30, D-34, D-36): destinazione (zip da scaricare o
// cartella), formato, qualità, dimensione, nomi, «Migliora luce e colori» e «Ritaglia a formato». Le scelte si ricordano.
// Il ritaglio si accende all'apertura solo se lo chiede `crop`; `mode` forza la destinazione.
const props = defineProps<{ open: boolean; uids: string[]; title: string; crop?: boolean; mode?: 'zip' | 'folder' }>()
const emit = defineEmits<{ close: [] }>()

type Format = 'original' | 'jpeg' | 'png' | 'webp'
interface Prefs { mode: 'zip' | 'folder'; fmt: Format; quality: number; maxSide: number; numbered: boolean; raw: boolean; videos: boolean }
const PREFS_KEY = 'mosaic.export.prefs'
const DEFAULTS: Prefs = { mode: 'zip', fmt: 'original', quality: 92, maxSide: 0, numbered: false, raw: false, videos: true }

function loadPrefs(): Prefs {
  try {
    return { ...DEFAULTS, ...JSON.parse(localStorage.getItem(PREFS_KEY) ?? '{}') }
  } catch {
    return { ...DEFAULTS }
  }
}

const initial = loadPrefs()
const destMode = ref<'zip' | 'folder'>(initial.mode)
const fmt = ref<Format>(initial.fmt)
const quality = ref(initial.quality)
const maxSide = ref(initial.maxSide) // 0 = misura originale
const numbered = ref(initial.numbered)
const dest = ref('')
const raw = ref(initial.raw)
const videos = ref(initial.videos)
const improve = ref(false)
const cropOn = ref(false)
const cropFormat = ref<CropFormat | null>(null)
const look = reactive<EnhanceOptions>({ light: true, contrast: true, vivid: true, harmonize: true, strength: 'medium' })
const canPick = ref(true)
const shots = ref<ExportPreviewShot[]>([])
const previewing = ref(false)
const created = ref<{ job: number; folder: string; zip: boolean } | null>(null)
const starting = ref(false)
const STRENGTHS = [{ value: 'light', label: 'Leggera' }, { value: 'medium', label: 'Media' }, { value: 'strong', label: 'Forte' }] as const
const FORMATS = [{ value: 'original', label: 'Originale' }, { value: 'jpeg', label: 'JPEG' }, { value: 'png', label: 'PNG' }, { value: 'webp', label: 'WebP' }] as const
const SIDES = [{ value: 0, label: 'Originale' }, { value: 3840, label: '4K (3840)' }, { value: 2560, label: '2560' }, { value: 1920, label: 'Full HD (1920)' }, { value: 1280, label: '1280' }]
const MODES = [{ value: 'zip', label: 'Scarica (zip)' }, { value: 'folder', label: 'In una cartella' }] as const

const job = computed(() => (created.value ? jobs.value.find((j) => j.id === created.value?.job) ?? null : null))
const phase = computed<'edit' | 'working' | 'done' | 'failed' | 'cancelled'>(() => {
  if (!created.value) return 'edit'
  if (job.value?.state === 'done') return 'done'
  if (job.value?.state === 'failed') return 'failed'
  if (job.value?.state === 'cancelled') return 'cancelled'
  return 'working'
})
const fraction = computed(() => (job.value && job.value.total ? job.value.done / job.value.total : null))
const count = computed(() => props.uids.length)
const isZip = computed(() => destMode.value === 'zip')
const rewrites = computed(() => fmt.value !== 'original' || maxSide.value > 0)
const showQuality = computed(() => fmt.value === 'jpeg' || fmt.value === 'webp' || (fmt.value === 'original' && (improve.value || cropOn.value || maxSide.value > 0)))
const workingLabel = computed(() => {
  if (cropOn.value) return improve.value ? 'Ritaglio, miglioro e salvo le foto…' : 'Ritaglio e salvo le foto…'
  if (improve.value) return 'Miglioro e salvo le foto…'
  return rewrites.value ? 'Converto le foto…' : isZip.value ? 'Preparo lo zip…' : 'Copio le foto…'
})
const canStart = computed(() => (isZip.value || !!dest.value.trim()) && !starting.value && count.value > 0 && (!cropOn.value || !!cropFormat.value))
const zipName = computed(() => props.title.replace(/[<>:"/\\|?*]+/g, ' ').replace(/\s{2,}/g, ' ').trim() || 'Foto Mosaic')

function savePrefs(): void {
  const prefs: Prefs = { mode: destMode.value, fmt: fmt.value, quality: quality.value, maxSide: maxSide.value, numbered: numbered.value, raw: raw.value, videos: videos.value }
  try {
    localStorage.setItem(PREFS_KEY, JSON.stringify(prefs))
  } catch {
    // senza archivio del browser le scelte valgono solo per questa volta
  }
}

watch(() => props.open, async (open) => {
  if (!open) return
  created.value = null
  shots.value = []
  cropOn.value = props.crop === true
  improve.value = false
  const saved = loadPrefs()
  destMode.value = props.mode ?? saved.mode
  fmt.value = saved.fmt
  quality.value = saved.quality
  maxSide.value = saved.maxSide
  numbered.value = saved.numbered
  raw.value = saved.raw
  videos.value = saved.videos
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
    savePrefs()
    const zip = isZip.value
    const made = await api.exportPhotos({
      uids: props.uids, dest: zip ? '' : dest.value.trim(), raw: raw.value && !crop, videos: videos.value && !crop,
      enhance: improve.value ? { ...look } : null, crop,
      zip, zip_name: zipName.value, fmt: fmt.value, quality: quality.value, max_side: maxSide.value || null, numbered: numbered.value,
    })
    created.value = { ...made, zip }
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

/** Avvia il download dello zip pronto: un vero download (del browser o dell'app desktop). */
function download(id = created.value?.job): void {
  if (id === undefined) return
  const link = document.createElement('a')
  link.href = exportDownloadUrl(id)
  link.download = ''
  document.body.append(link)
  link.click()
  link.remove()
}

async function stop(): Promise<void> {
  if (!created.value) return
  try {
    await api.cancelJob(created.value.job)
  } catch (error) {
    toast(errorMessage(error), 'error')
  }
}

// lo zip pronto parte da solo; a dialogo chiuso l'avviso arriva lo stesso, con la cartella o il download a un clic
watch(phase, (now) => {
  if (!created.value) return
  const { job: id, zip } = created.value
  if (now === 'done' && zip) download(id)
  if (props.open) return
  if (now === 'done') {
    toast(job.value?.message || 'Esportazione finita', 'default',
          zip ? { label: 'Scarica di nuovo', run: () => download(id) } : { label: 'Apri la cartella', run: () => void reveal(id) }, 10000)
  }
  if (now === 'failed') toast(`Esportazione non riuscita: ${job.value?.error || 'errore sconosciuto'}`, 'error')
  if (now === 'cancelled') toast('Esportazione annullata')
})
</script>

<template>
  <Dialog :open="open" title="Scarica o esporta le foto" size="lg" @close="emit('close')">
    <div v-if="phase === 'edit'" class="export">
      <div class="field">
        <span class="label">Dove</span>
        <Segmented v-model="destMode" :options="[...MODES]" label="Dove salvare le foto" />
      </div>

      <div v-if="!isZip" class="field">
        <label for="ex-dest">Cartella</label>
        <div class="dest">
          <input id="ex-dest" v-model="dest" class="input mono" autocomplete="off" spellcheck="false" data-test="export-dest" />
          <button v-if="canPick" type="button" class="btn" data-test="export-pick" @click="pick"><Icon name="folderOpen" /> Scegli…</button>
        </div>
        <p class="hint">{{ count }} foto. Se la cartella non c'è la creo; i file con lo stesso nome non vengono mai sovrascritti.</p>
      </div>
      <p v-else class="hint" data-test="export-zip-hint">{{ count }} foto in un unico file «{{ zipName }}.zip», che parte da solo quando è pronto.</p>

      <fieldset class="checks format">
        <legend class="caps">Formato e dimensione</legend>
        <div class="field">
          <span class="label">Formato</span>
          <Segmented v-model="fmt" :options="[...FORMATS]" label="Formato delle foto" />
        </div>
        <div class="field">
          <label for="ex-side">Lato lungo</label>
          <select id="ex-side" v-model.number="maxSide" class="input" data-test="export-side">
            <option v-for="o in SIDES" :key="o.value" :value="o.value">{{ o.label }}</option>
          </select>
        </div>
        <div v-if="showQuality" class="field">
          <label for="ex-quality">Qualità {{ quality }}</label>
          <input id="ex-quality" v-model.number="quality" type="range" min="40" max="100" step="1" data-test="export-quality" />
        </div>
        <label><input v-model="numbered" type="checkbox" data-test="export-numbered" /> Numera i file (0001_nome…)</label>
        <p v-if="fmt === 'original' && (improve || cropOn) && !maxSide" class="hint wide">Con miglioramento o ritaglio le foto escono in JPEG.</p>
      </fieldset>

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
            <span class="hint">Le copie diventano corrette; gli originali nella libreria restano come sono.</span></span>
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
      <button type="button" class="btn sm" data-test="export-stop" @click="stop">Annulla</button>
    </div>

    <div v-else-if="phase === 'done' && created" class="status done">
      <span class="ok"><Icon name="check" :size="22" /></span>
      <strong>{{ created.zip ? 'Zip pronto' : 'Esportazione finita' }}</strong>
      <span class="hint">{{ job?.message }}</span>
      <div class="actions">
        <button v-if="created.zip" type="button" class="btn primary" data-test="export-download" @click="download()"><Icon name="download" /> Scarica di nuovo</button>
        <button v-else type="button" class="btn primary" data-test="export-reveal" @click="reveal()"><Icon name="folderOpen" /> Apri la cartella</button>
        <button type="button" class="btn" @click="emit('close')">Chiudi</button>
      </div>
    </div>

    <div v-else-if="phase === 'cancelled'" class="status" role="status">
      <strong>Esportazione annullata</strong>
      <span class="hint">Le foto già copiate in una cartella restano dove sono; uno zip interrotto non viene creato.</span>
      <button type="button" class="btn" @click="created = null">Ricomincia</button>
    </div>

    <div v-else class="status" role="alert">
      <strong>Non sono riuscito a esportare le foto</strong>
      <span class="error-text">{{ job?.error || 'errore sconosciuto' }}</span>
      <button type="button" class="btn" @click="created = null">Riprova</button>
    </div>

    <template v-if="phase === 'edit'" #footer>
      <button type="button" class="btn" @click="emit('close')">Annulla</button>
      <button type="button" class="btn primary" data-test="export-start" :disabled="!canStart" @click="start">
        <Icon name="download" /> {{ isZip ? 'Scarica' : 'Esporta' }} {{ count }} foto
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
.checks.format { align-items: end; }
.checks .field { display: flex; flex-direction: column; gap: var(--space-1); }
.checks .wide { grid-column: 1 / -1; margin: 0; }
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
