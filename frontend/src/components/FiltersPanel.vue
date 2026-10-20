<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../lib/api'
import { buildQuery, emptyForm, formFromQuery, type FilterForm } from '../lib/query'
import { pushHistory } from '../lib/store'
import type { Album, LabelInfo, Subject } from '../lib/types'
import Icon from './Icon.vue'

// Pannello "morph" della sidebar: sostituisce il contenuto, allarga la colonna e costruisce una query di ricerca.
// Si apre già compilato con la ricerca corrente, così i filtri si aggiungono o tolgono uno per uno.
const emit = defineEmits<{ close: [] }>()
const route = useRoute()
const router = useRouter()

const currentQuery = (): string => (route.path === '/search' && typeof route.query.q === 'string' ? route.query.q : '')
const form = reactive<FilterForm>(formFromQuery(currentQuery()))
watch(currentQuery, (q) => Object.assign(form, formFromQuery(q)))

const labels = ref<LabelInfo[]>([])
const people = ref<Subject[]>([])
const albums = ref<Album[]>([])
const COLORS = ['rosso', 'arancione', 'giallo', 'verde', 'azzurro', 'blu', 'viola', 'rosa', 'marrone', 'nero', 'bianco', 'grigio']
// 4:3 è orizzontale, 3:4 verticale; tolleranza del 6% applicata dalla ricerca
const RATIOS = ['1:1', '4:3', '3:4', '3:2', '2:3', '16:9', '9:16', '5:4', '4:5']
const MONTHS = ['gennaio', 'febbraio', 'marzo', 'aprile', 'maggio', 'giugno', 'luglio', 'agosto', 'settembre', 'ottobre', 'novembre', 'dicembre']

onMounted(async () => {
  const [l, p, a] = await Promise.allSettled([api.labels(), api.subjects(), api.albums('album')])
  if (l.status === 'fulfilled') labels.value = l.value ?? []
  if (p.status === 'fulfilled') people.value = p.value ?? []
  if (a.status === 'fulfilled') albums.value = a.value ?? []
})

type MultiKey = 'people' | 'labels' | 'albums' | 'colors'
interface Option { value: string, text: string }
interface MultiField { key: MultiKey, id: string, label: string, hint: string, add: string, options: Option[] }

// Scelte multiple: persone ed etichette devono esserci tutte, per album e colori ne basta uno.
const multi = computed<MultiField[]>(() => [
  { key: 'people', id: 'f-person', label: 'Persone', hint: 'tutte nella foto', add: 'Aggiungi una persona…',
    options: people.value.map((p) => ({ value: p.slug, text: p.name })) },
  { key: 'labels', id: 'f-label', label: 'Etichette', hint: 'tutte insieme', add: 'Aggiungi un\'etichetta…',
    options: labels.value.slice(0, 200).map((l) => ({ value: l.slug, text: `${l.name} (${l.count})` })) },
  { key: 'albums', id: 'f-album', label: 'Album', hint: 'almeno uno', add: 'Aggiungi un album…',
    options: albums.value.map((a) => ({ value: a.slug, text: a.title })) },
  { key: 'colors', id: 'f-color', label: 'Colori', hint: 'almeno uno', add: 'Aggiungi un colore…',
    options: COLORS.map((c) => ({ value: c, text: c })) },
])

// Nome da mostrare nel chip (senza il conteggio delle etichette); lo slug se l'elenco non è ancora arrivato.
function nameOf(options: Option[], value: string): string {
  return options.find((o) => o.value === value)?.text.replace(/ \(\d+\)$/, '') ?? value
}

function pick(key: MultiKey, event: Event): void {
  const select = event.target as HTMLSelectElement
  const value = select.value
  select.value = ''
  if (value && !form[key].includes(value)) form[key] = [...form[key], value]
}

function unpick(key: MultiKey, value: string): void {
  form[key] = form[key].filter((v) => v !== value)
}

function dropExtra(raw: string): void {
  form.extra = form.extra.filter((v) => v !== raw)
}

const query = computed(() => buildQuery(form))

function apply(): void {
  const q = query.value
  if (!q) return
  pushHistory(q)
  void router.push({ path: '/search', query: { q } })
  emit('close')
}

function reset(): void {
  Object.assign(form, emptyForm())
}
</script>

<template>
  <form class="panel-filters" @submit.prevent="apply">
    <header class="head">
      <button type="button" class="btn ghost icon sm" aria-label="Torna alla navigazione" @click="emit('close')"><Icon name="chevronLeft" /></button>
      <h2>Filtri</h2>
    </header>
    <div class="fields">
      <div class="field"><label for="f-text">Cosa cerchi</label>
        <input id="f-text" v-model="form.text" class="input" placeholder="per esempio: moto rossa sul palco" autocomplete="off" /></div>
      <div class="two">
        <div class="field"><label for="f-type">Tipo</label>
          <select id="f-type" v-model="form.type" class="select">
            <option value="">Tutti</option><option value="image">Foto</option><option value="video">Video</option>
            <option value="live">Live Photo</option><option value="animated">Animate</option><option value="raw">Con RAW</option>
          </select></div>
        <div class="field"><label for="f-orient">Orientamento</label>
          <select id="f-orient" v-model="form.orientation" class="select"><option value="">Qualsiasi</option>
            <option value="landscape">Orizzontale</option><option value="portrait">Verticale</option><option value="square">Quadrata</option></select></div>
      </div>
      <div class="two">
        <div class="field"><label for="f-year">Anno</label>
          <input id="f-year" v-model="form.year" class="input" inputmode="numeric" placeholder="2024 o 2022-2024" /></div>
        <div class="field"><label for="f-month">Mese</label>
          <select id="f-month" v-model="form.month" class="select"><option value="">Tutti</option>
            <option v-for="(m, i) in MONTHS" :key="m" :value="String(i + 1)">{{ m }}</option></select></div>
      </div>
      <div v-for="m in multi" :key="m.key" class="field">
        <label :for="m.id">{{ m.label }} <span v-if="form[m.key].length > 1" class="hint">· {{ m.hint }}</span></label>
        <select :id="m.id" class="select" @change="pick(m.key, $event)"><option value="">{{ m.add }}</option>
          <option v-for="o in m.options" :key="o.value" :value="o.value" :disabled="form[m.key].includes(o.value)">{{ o.text }}</option></select>
        <ul v-if="form[m.key].length" class="picked" :aria-label="`${m.label} scelte`">
          <li v-for="v in form[m.key]" :key="v" class="chip accent">{{ nameOf(m.options, v) }}<button type="button"
            :aria-label="`Togli ${nameOf(m.options, v)}`" @click="unpick(m.key, v)"><Icon name="x" :size="12" /></button></li>
        </ul>
      </div>
      <div class="two">
        <div class="field"><label for="f-ratio">Formato</label>
          <select id="f-ratio" v-model="form.ratio" class="select"><option value="">Qualsiasi</option>
            <option v-for="r in RATIOS" :key="r" :value="r">{{ r }}</option></select></div>
        <div class="field"><label for="f-mp">Megapixel</label>
          <input id="f-mp" v-model="form.mp" class="input" inputmode="decimal" placeholder="12 o 8-20" autocomplete="off" /></div>
      </div>
      <div class="field"><label for="f-camera">Fotocamera</label>
        <input id="f-camera" v-model="form.camera" class="input" placeholder="nikon d850" autocomplete="off" /></div>
      <fieldset class="checks">
        <legend class="caps">Solo</legend>
        <label><input v-model="form.favorite" type="checkbox" /> Preferiti</label>
        <label><input v-model="form.review" type="checkbox" /> Da rivedere</label>
        <label><input v-model="form.faces" type="checkbox" /> Con volti</label>
        <label><input v-model="form.geo" type="checkbox" /> Con posizione GPS</label>
      </fieldset>
      <div v-if="form.extra.length" class="field">
        <span class="label">Altri filtri della ricerca</span>
        <ul class="picked" aria-label="Altri filtri">
          <li v-for="raw in form.extra" :key="raw" class="chip accent mono">{{ raw }}<button type="button" :aria-label="`Togli ${raw}`"
            @click="dropExtra(raw)"><Icon name="x" :size="12" /></button></li>
        </ul>
      </div>
    </div>
    <footer class="foot">
      <button type="button" class="btn" @click="reset">Azzera</button>
      <button type="submit" class="btn primary" :disabled="!query"><Icon name="search" /> Cerca</button>
    </footer>
  </form>
</template>

<style scoped>
.panel-filters { display: flex; flex-direction: column; height: 100%; min-height: 0; padding: var(--space-3); background: var(--bg-sidebar); border-radius: inherit; }
.head { display: flex; align-items: center; gap: var(--space-2); padding-bottom: var(--space-3); }
.head h2 { font-size: var(--text-lg); }
.fields { display: flex; flex: 1; flex-direction: column; gap: var(--space-3); min-height: 0; overflow-y: auto; padding: 0 var(--space-1) var(--space-3); }
.two { display: grid; grid-template-columns: 1fr 1fr; gap: var(--space-3); }
.picked { display: flex; flex-wrap: wrap; gap: var(--space-1-5); margin: var(--space-1) 0 0; padding: 0; list-style: none; }
.picked .mono { font-family: var(--font-mono); }
.checks { display: grid; grid-template-columns: 1fr 1fr; gap: var(--space-2); margin: 0; padding: 0; border: 0; font-size: var(--text-sm); }
.checks legend { grid-column: 1 / -1; padding: 0 0 var(--space-1); }
.checks label { display: flex; align-items: center; gap: var(--space-2); }
.foot { display: flex; justify-content: flex-end; gap: var(--space-2); padding-top: var(--space-3); border-top: var(--border-width) solid var(--border-default); }
</style>
