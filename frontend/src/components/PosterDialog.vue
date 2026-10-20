<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { api, posterUrl, thumbUrl } from '../lib/api'
import { jobs, refreshJobs } from '../lib/store'
import { errorMessage, toast } from '../lib/toast'
import type { PosterCell, PosterLayout, PosterLine, PosterOptions, PosterPaper, PosterStyle } from '../lib/types'
import Dialog from './Dialog.vue'
import Icon from './Icon.vue'
import ProgressBar from './ProgressBar.vue'
import Segmented from './Segmented.vue'

// Poster a mosaico dalla selezione (D-26): anteprima dal vivo con la stessa impaginazione del file finale.
const props = defineProps<{ open: boolean; uids: string[]; title: string; album: string }>()
const albumTitle = computed(() => props.title)
const emit = defineEmits<{ close: [] }>()

const PAPERS: { value: PosterPaper; label: string }[] = [
  { value: 'a4', label: 'A4 · 21 × 29,7 cm' }, { value: 'a3', label: 'A3 · 29,7 × 42 cm' }, { value: 'a2', label: 'A2 · 42 × 59,4 cm' },
  { value: '30x40', label: '30 × 40 cm' }, { value: '50x70', label: '50 × 70 cm' }, { value: '70x100', label: '70 × 100 cm' },
  { value: 'square', label: 'Quadrato · 50 × 50 cm' },
]
const STYLES: { value: PosterStyle; label: string }[] = [
  { value: 'mondrian', label: 'Mondrian' }, { value: 'gallery', label: 'Galleria' }, { value: 'none', label: 'Senza bordi' },
]
const LINES: { value: PosterLine; label: string }[] = [{ value: 'thin', label: 'Sottili' }, { value: 'medium', label: 'Medie' }, { value: 'bold', label: 'Spesse' }]
const ORIENT: { value: 'portrait' | 'landscape'; label: string }[] = [{ value: 'portrait', label: 'Verticale' }, { value: 'landscape', label: 'Orizzontale' }]

const opts = reactive<PosterOptions>({
  paper: 'a3', landscape: false, style: 'mondrian', line: 'medium', colors: 4, seed: 1, dpi: 200, occasion: props.title, date: '', place: '',
})
const occasionTouched = ref(false)
watch(() => [props.open, props.title], () => {
  if (props.open && !occasionTouched.value) opts.occasion = props.title
}, { immediate: true })
const autoDate = computed(() => layout.value?.facts.find((f) => f.label === 'Data')?.value ?? '')
const orientation = computed({ get: () => (opts.landscape ? 'landscape' : 'portrait'), set: (v) => (opts.landscape = v === 'landscape') })
const layout = ref<PosterLayout | null>(null)
const loading = ref(false)
const failure = ref('')
const created = ref<{ job: number; name: string; folder: string } | null>(null)

const job = computed(() => (created.value ? jobs.value.find((j) => j.id === created.value?.job) ?? null : null))
const phase = computed<'edit' | 'working' | 'done' | 'failed'>(() => {
  if (!created.value) return 'edit'
  if (job.value?.state === 'done') return 'done'
  if (job.value?.state === 'failed' || job.value?.state === 'cancelled') return 'failed'
  return 'working'
})
const fraction = computed(() => (job.value && job.value.total ? job.value.done / job.value.total : null))

let timer: ReturnType<typeof setTimeout> | undefined
let generation = 0
async function refresh(): Promise<void> {
  const mine = ++generation
  loading.value = true
  failure.value = ''
  try {
    const result = await api.posterLayout(props.uids, { ...opts })
    if (mine === generation) layout.value = result
  } catch (error) {
    if (mine === generation) failure.value = errorMessage(error)
  } finally {
    if (mine === generation) loading.value = false
  }
}
watch(() => [props.open, props.uids, { ...opts }], () => {
  clearTimeout(timer)
  if (props.open && props.uids.length) timer = setTimeout(refresh, 250)
}, { immediate: true, deep: true })
onBeforeUnmount(() => clearTimeout(timer))

function reshuffle(): void {
  opts.seed = 1 + Math.floor(Math.random() * 999_999)
}

/** Posizione di un riquadro nell'anteprima: come nel file, metà riga per lato e una riga intera sul bordo del foglio. */
function cellStyle(c: PosterCell): Record<string, string> {
  const l = layout.value
  if (!l) return {}
  const short = Math.min(l.width_mm, l.height_mm)
  const lw = (l.line * short) / l.width_mm
  const lh = (l.line * short) / l.height_mm
  const left = c.x + (c.x < 1e-6 ? lw : lw / 2)
  const top = c.y + (c.y < 1e-6 ? lh : lh / 2)
  const right = c.x + c.w - (c.x + c.w > 1 - 1e-6 ? lw : lw / 2)
  const bottom = c.y + c.h - (c.y + c.h > 1 - 1e-6 ? lh : lh / 2)
  return { left: `${left * 100}%`, top: `${top * 100}%`, width: `${(right - left) * 100}%`, height: `${(bottom - top) * 100}%`,
    ...(c.color ? { background: c.color } : {}) }
}

/** Testo in negativo nel riquadro: misure in unità dell'altezza del riquadro (cqh), come nel file finale. */
function textStyle(c: PosterCell): Record<string, string> {
  const t = c.text
  if (!t) return {}
  return { color: t.fg, padding: `calc(${t.pad} * 100cqh)`, '--value': `calc(${t.value} * 100cqh)`, '--label': `calc(${t.label_size} * 100cqh)` }
}

function imageStyle(c: PosterCell): Record<string, string> {
  const [x0, y0, x1, y1] = c.crop
  return { width: `${100 / (x1 - x0)}%`, height: `${100 / (y1 - y0)}%`, left: `${(-x0 / (x1 - x0)) * 100}%`, top: `${(-y0 / (y1 - y0)) * 100}%` }
}

async function create(): Promise<void> {
  try {
    created.value = await api.createPoster(props.uids, { ...opts }, props.title, props.album)
    await refreshJobs()
  } catch (error) {
    toast(errorMessage(error), 'error')
  }
}

async function reveal(): Promise<void> {
  if (!created.value) return
  try {
    await api.revealPoster(props.album, created.value.name)
  } catch (error) {
    toast(errorMessage(error), 'error')
  }
}

async function openFile(): Promise<void> {
  if (!created.value) return
  try {
    await api.openPoster(props.album, created.value.name)
  } catch (error) {
    toast(errorMessage(error), 'error')
  }
}

function again(): void {
  created.value = null
}

function close(): void {
  created.value = null
  emit('close')
}
</script>

<template>
  <Dialog :open="open" title="Poster a mosaico" size="xl" @close="close">
    <div class="poster" :data-phase="phase">
      <div class="stage">
        <div v-if="layout" class="sheet" :class="{ busy: loading }" data-test="poster-preview"
             :style="{ '--ratio': String(layout.width_mm / layout.height_mm), background: layout.ink }">
          <template v-if="phase === 'done' && created">
            <img class="final" :src="posterUrl(album, created.name)" alt="Il poster finito" />
          </template>
          <template v-else>
            <div v-for="(c, i) in layout.cells" :key="i" class="cell" :class="{ text: c.text }" :style="cellStyle(c)">
              <img v-if="c.hash" :src="thumbUrl(c.hash, 'fit_720')" :style="imageStyle(c)" alt="" draggable="false" />
              <div v-else-if="c.text" class="fact" :style="textStyle(c)">
                <span v-if="c.text.label" class="fact-label">{{ c.text.label }}</span>
                <span class="fact-value"><span v-for="(l, j) in c.text.lines" :key="j">{{ l }}</span></span>
              </div>
            </div>
          </template>
        </div>
        <div v-else-if="failure" class="empty" role="alert"><p>{{ failure }}</p><button type="button" class="btn" @click="refresh">Riprova</button></div>
        <div v-else class="sheet placeholder skeleton" />
      </div>

      <aside class="side">
        <template v-if="phase === 'edit'">
          <div class="two paper">
            <div class="field"><label for="p-paper">Formato</label>
              <select id="p-paper" v-model="opts.paper" class="select"><option v-for="p in PAPERS" :key="p.value" :value="p.value">{{ p.label }}</option></select></div>
            <div class="field"><label for="p-dpi">Stampa</label>
              <select id="p-dpi" v-model.number="opts.dpi" class="select">
                <option :value="150">150 dpi</option><option :value="200">200 dpi</option><option :value="300">300 dpi</option>
              </select></div>
          </div>
          <Segmented v-model="orientation" :options="ORIENT" label="Orientamento" />
          <Segmented v-model="opts.style" :options="STYLES" label="Stile del poster" />
          <Segmented v-if="opts.style !== 'none'" v-model="opts.line" :options="LINES" label="Spessore delle righe" />
          <template v-if="opts.style === 'mondrian'">
            <div class="field">
              <label for="p-colors">Riquadri con i dati: <strong>{{ opts.colors }}</strong></label>
              <input id="p-colors" v-model.number="opts.colors" type="range" min="0" max="6" step="1" />
            </div>
            <div class="field"><label for="p-occasion">Occasione</label>
              <input id="p-occasion" v-model.lazy="opts.occasion" class="input" maxlength="80" placeholder="per esempio: Festa al garage"
                     @change="occasionTouched = true" /></div>
            <div class="two">
              <div class="field"><label for="p-date">Data</label>
                <input id="p-date" v-model.lazy="opts.date" class="input" maxlength="80" :placeholder="autoDate || 'dalle foto'" /></div>
              <div class="field"><label for="p-place">Luogo</label>
                <input id="p-place" v-model.lazy="opts.place" class="input" maxlength="80" placeholder="Padova" /></div>
            </div>
          </template>
          <div class="row-end">
            <span v-if="layout" class="hint mono">{{ layout.pixels[0] }} × {{ layout.pixels[1] }} px<template v-if="layout.dpi < opts.dpi"> · {{ layout.dpi }} dpi</template></span>
            <button type="button" class="btn sm" :disabled="loading" @click="reshuffle"><Icon name="shuffle" /> Rimescola</button>
          </div>
          <p class="hint">Si salva in <strong>{{ albumTitle }}</strong>.</p>
        </template>

        <div v-else-if="phase === 'working'" class="status" role="status">
          <Icon name="spinner" class="spin" :size="22" />
          <strong>Compongo il poster…</strong>
          <ProgressBar size="md" :value="fraction" label="Poster: avanzamento" />
          <span class="hint">{{ job?.message || 'in coda' }}</span>
        </div>

        <div v-else-if="phase === 'done' && created" class="status done">
          <span class="ok"><Icon name="check" :size="22" /></span>
          <strong>Poster pronto</strong>
          <span class="hint">Salvato in <span class="mono">{{ created.folder }}</span></span>
          <div class="actions">
            <button type="button" class="btn primary" @click="openFile"><Icon name="external" /> Apri</button>
            <button type="button" class="btn" @click="reveal"><Icon name="folderOpen" /> Mostra nella cartella</button>
            <a class="btn" :href="posterUrl(album, created.name)" :download="created.name"><Icon name="download" /> Scarica</a>
          </div>
          <button type="button" class="btn ghost" @click="again"><Icon name="refresh" /> Un altro poster</button>
        </div>

        <div v-else class="status" role="alert">
          <strong>Non sono riuscito a creare il poster</strong>
          <span class="error-text">{{ job?.error || 'errore sconosciuto' }}</span>
          <button type="button" class="btn" @click="again">Riprova</button>
        </div>
      </aside>
    </div>
    <template v-if="phase === 'edit'" #footer>
      <button type="button" class="btn" @click="close">Annulla</button>
      <button type="button" class="btn primary" data-test="poster-create" :disabled="!layout || loading" @click="create">
        <Icon name="image" /> Crea il poster
      </button>
    </template>
  </Dialog>
</template>

<style scoped>
.poster {
  display: grid; grid-template-columns: minmax(0, 1fr) 17rem; gap: var(--space-4);
  /* tutto in una schermata: il dialogo non scorre, il foglio si adatta allo spazio che resta */
  height: min(calc(94dvh - 9.5rem), 46rem);
}
.stage { container-type: size; display: grid; place-items: center; min-height: 0; padding: var(--space-3); border-radius: var(--radius-panel); background: var(--bg-sunken); }
.sheet {
  position: relative; width: min(100cqw, calc(100cqh * var(--ratio))); aspect-ratio: var(--ratio); overflow: hidden;
  box-shadow: var(--shadow-4); transition: opacity var(--dur-fast) var(--ease-out);
}
.sheet.busy { opacity: 0.6; }
.sheet.placeholder { --ratio: 0.7071; }
.cell { position: absolute; overflow: hidden; background: var(--stage-cell); }
.cell img { position: absolute; max-width: none; object-fit: fill; }
.cell.text { container-type: size; }
.fact { position: absolute; inset: 0; display: flex; flex-direction: column;
  font-family: Bahnschrift, 'Segoe UI', Arial, sans-serif; line-height: 1.08; }
.fact-label { font-size: var(--label); font-weight: var(--weight-semibold); letter-spacing: 0.02em; }
.fact-value { display: flex; flex-direction: column; margin-top: auto; font-size: var(--value); font-weight: var(--weight-semibold); white-space: nowrap; }
/* minmax(0, …): i campi stringono invece di allargare la colonna (niente barra orizzontale) */
.two { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: var(--space-2); }
.two.paper { grid-template-columns: minmax(0, 1fr) auto; }
.side .input, .side .select { width: 100%; min-width: 0; }
.side .select { text-overflow: ellipsis; }
.final { display: block; width: 100%; height: 100%; object-fit: contain; }
.side { display: flex; flex-direction: column; gap: var(--space-3); min-width: 0; min-height: 0; padding: 2px 4px; overflow: hidden auto; }
.row-end { display: flex; align-items: center; justify-content: space-between; gap: var(--space-2); }
.label { font-size: var(--text-sm); font-weight: var(--weight-medium); }
input[type='range'] { width: 100%; accent-color: var(--accent); }
.status { display: flex; flex-direction: column; gap: var(--space-3); padding: var(--space-4); border-radius: var(--radius-panel); background: var(--bg-selected); }
.status.done { background: var(--match-bg); }
.ok { display: grid; place-items: center; width: 2.5rem; height: 2.5rem; border-radius: 50%; background: var(--match-solid); color: var(--fg-on-accent); }
.actions { display: flex; flex-wrap: wrap; gap: var(--space-2); }
@media (max-width: 52rem) {
  .poster { grid-template-columns: minmax(0, 1fr); grid-template-rows: 48dvh auto; height: auto; }
}
</style>
