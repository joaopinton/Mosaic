<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { api, posterUrl, thumbUrl } from '../lib/api'
import { jobs, refreshJobs } from '../lib/store'
import { errorMessage, toast } from '../lib/toast'
import type {
  PosterCase, PosterCell, PosterFact, PosterFont, PosterLayout, PosterLine, PosterOptions, PosterPaper, PosterStyle,
} from '../lib/types'
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

// carattere: la stessa famiglia e lo stesso peso del file finale (poster_text.FONTS)
const FONTS: { value: PosterFont; label: string; css: string; weight: string }[] = [
  { value: 'bahnschrift', label: 'Bahnschrift', css: "Bahnschrift, 'Segoe UI', Arial, sans-serif", weight: '600' },
  { value: 'georgia', label: 'Georgia', css: 'Georgia, serif', weight: '700' },
  { value: 'impact', label: 'Impact', css: "Impact, 'Arial Black', sans-serif", weight: '400' },
  { value: 'consolas', label: 'Consolas', css: 'Consolas, monospace', weight: '700' },
]
const CASES: { value: PosterCase; label: string }[] = [
  { value: 'auto', label: 'Come scritte' }, { value: 'upper', label: 'MAIUSCOLE' }, { value: 'lower', label: 'minuscole' },
]
const MAX_TEXTS = 8
const MAX_COLORS = 6

const opts = reactive<PosterOptions>({
  paper: 'a3', landscape: false, style: 'mondrian', line: 'medium', colors: 4, seed: 1, dpi: 200, occasion: props.title, date: '', place: '',
  texts: null, font: 'bahnschrift', case: 'auto',
})
// finché le scritte sono automatiche l'occasione segue il titolo dell'album
watch(() => [props.open, props.title], () => {
  if (props.open && opts.texts === null) opts.occasion = props.title
}, { immediate: true })
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

// Scritte dei riquadri (D-33): si parte dai dati automatici; appena se ne tocca una diventano dell'utente
// (opts.texts), che le cambia, nasconde, aggiunge e riordina. La prima va nel riquadro più grande.
interface TextRow { id: number; label: string; value: string; hidden: boolean; custom: boolean }
let nextRow = 0
const rows = ref<TextRow[]>([])
const customized = computed(() => opts.texts !== null)
const fromFacts = (facts: PosterFact[]): TextRow[] =>
  facts.map((f) => ({ id: ++nextRow, label: f.label, value: f.value, hidden: false, custom: false }))
watch(() => layout.value?.facts, (facts) => {
  if (!facts || customized.value) return
  const same = facts.length === rows.value.length && facts.every((f, i) => f.label === rows.value[i].label && f.value === rows.value[i].value)
  if (!same) rows.value = fromFacts(facts)  // righe nuove solo se cambiano: chi sta scrivendo non perde il campo
})
const filled = computed(() => rows.value.filter((r) => !r.hidden && r.value.trim()))
/** Le scritte che entrano: tante quanti i riquadri colorati, nell'ordine della lista. */
const placed = computed(() => new Set(filled.value.slice(0, opts.colors).map((r) => r.id)))

function sync(): void {
  opts.texts = rows.value.filter((r) => !r.hidden).map((r) => ({ label: r.label.trim(), value: r.value.trim() }))
}
function edit(row: TextRow, field: 'label' | 'value', event: Event): void {
  row[field] = (event.target as HTMLInputElement).value
  // una scritta appena riempita deve vedersi: se resta fuori, un riquadro in più (fino al massimo del cursore)
  const rank = filled.value.indexOf(row) + 1
  if (field === 'value' && rank > opts.colors) opts.colors = Math.min(MAX_COLORS, rank)
  sync()
}
function move(index: number, step: -1 | 1): void {
  const list = [...rows.value]
  ;[list[index], list[index + step]] = [list[index + step], list[index]]
  rows.value = list
  sync()
}
function toggle(row: TextRow): void {
  row.hidden = !row.hidden
  sync()
}
function removeRow(index: number): void {
  rows.value = rows.value.filter((_, i) => i !== index)
  sync()
}
async function addText(): Promise<void> {
  const row: TextRow = { id: ++nextRow, label: '', value: '', hidden: false, custom: true }
  rows.value = [...rows.value, row]
  sync()
  await nextTick()
  document.getElementById(`p-text-value-${row.id}`)?.focus()
}
function resetTexts(): void {
  opts.texts = null
  opts.occasion = props.title
  rows.value = fromFacts(layout.value?.facts ?? [])
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
  const face = FONTS.find((f) => f.value === layout.value?.font) ?? FONTS[0]
  return { color: t.fg, fontFamily: face.css, fontWeight: face.weight, padding: `calc(${t.pad} * 100cqh)`, '--value': `calc(${t.value} * 100cqh)`, '--label': `calc(${t.label_size} * 100cqh)` }
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
            <div class="two">
              <div class="field"><label for="p-font">Carattere</label>
                <select id="p-font" v-model="opts.font" class="select">
                  <option v-for="f in FONTS" :key="f.value" :value="f.value" :style="{ fontFamily: f.css }">{{ f.label }}</option>
                </select></div>
              <div class="field"><label for="p-case">Lettere</label>
                <select id="p-case" v-model="opts.case" class="select">
                  <option v-for="c in CASES" :key="c.value" :value="c.value">{{ c.label }}</option>
                </select></div>
            </div>
            <fieldset class="texts" data-test="poster-texts">
              <legend class="label">Scritte <span class="hint">· la prima va nel riquadro più grande</span></legend>
              <ol class="text-rows">
                <li v-for="(r, i) in rows" :key="r.id" class="text-row" data-test="poster-text"
                    :class="{ off: r.hidden, out: !r.hidden && r.value.trim() && !placed.has(r.id) }">
                  <input :id="`p-text-label-${r.id}`" class="input" :value="r.label" maxlength="30" placeholder="Etichetta (facoltativa)"
                         :disabled="r.hidden" :aria-label="`Etichetta della scritta ${i + 1}`" @change="edit(r, 'label', $event)" />
                  <span class="tools">
                    <button type="button" class="btn ghost sm icon" :disabled="i === 0" :aria-label="`Sposta su la scritta ${i + 1}`" @click="move(i, -1)"><Icon name="chevronUp" /></button>
                    <button type="button" class="btn ghost sm icon" :disabled="i === rows.length - 1" :aria-label="`Sposta giù la scritta ${i + 1}`" @click="move(i, 1)"><Icon name="chevronDown" /></button>
                    <button v-if="r.custom" type="button" class="btn ghost sm icon" :aria-label="`Togli la scritta ${i + 1}`" @click="removeRow(i)"><Icon name="x" /></button>
                    <button v-else type="button" class="btn ghost sm icon" :aria-pressed="r.hidden"
                            :aria-label="`${r.hidden ? 'Mostra' : 'Nascondi'} la scritta ${i + 1}`" @click="toggle(r)"><Icon :name="r.hidden ? 'eyeOff' : 'eye'" /></button>
                  </span>
                  <input :id="`p-text-value-${r.id}`" class="input value" :value="r.value" maxlength="80" placeholder="Testo"
                         :disabled="r.hidden" :aria-label="`Testo della scritta ${i + 1}`" @change="edit(r, 'value', $event)" />
                  <span v-if="!r.hidden && r.value.trim() && !placed.has(r.id)" class="hint note">Non entra: spostala su o aumenta i riquadri</span>
                </li>
              </ol>
              <div class="row-end">
                <button type="button" class="btn sm" :disabled="rows.length >= MAX_TEXTS" @click="addText"><Icon name="plus" /> Aggiungi scritta</button>
                <button v-if="customized" type="button" class="btn ghost sm" @click="resetTexts"><Icon name="refresh" /> Automatiche</button>
              </div>
            </fieldset>
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
/* carattere e peso arrivano da FONTS (textStyle), uguali a quelli del file; niente spaziatura in più: righe misurate dal server */
.fact { position: absolute; inset: 0; display: flex; flex-direction: column; line-height: 1.08; }
.fact-label { font-size: var(--label); white-space: nowrap; }
.fact-value { display: flex; flex-direction: column; margin-top: auto; font-size: var(--value); white-space: nowrap; }
.texts { display: flex; flex-direction: column; gap: var(--space-2); min-width: 0; margin: 0; padding: 0; border: 0; }
.texts legend { padding: 0; margin-bottom: var(--space-2); }
.text-rows { display: flex; flex-direction: column; gap: var(--space-1-5); margin: 0; padding: 0; list-style: none; }
.text-row {
  display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: var(--space-1); padding: var(--space-1-5);
  border: 1px solid var(--border-subtle); border-radius: var(--radius-control); background: var(--bg-sunken);
}
.text-row .input { min-height: var(--control-h-sm); padding: 0 var(--space-2); }
.text-row .value, .text-row .note { grid-column: 1 / -1; }
.text-row.off .input { text-decoration: line-through; }
.text-row.out { border-style: dashed; border-color: var(--border-strong); }
.tools { display: flex; align-items: center; }
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
