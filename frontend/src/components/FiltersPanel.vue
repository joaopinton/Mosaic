<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../lib/api'
import { pushHistory } from '../lib/store'
import type { Album, LabelInfo, Subject } from '../lib/types'
import Icon from './Icon.vue'

// Pannello "morph" della sidebar: sostituisce il contenuto, allarga la colonna e costruisce una query di ricerca.
const emit = defineEmits<{ close: [] }>()
const router = useRouter()

const form = reactive({
  text: '', type: '', year: '', month: '', label: '', person: '', album: '', camera: '', color: '', orientation: '',
  ratio: '', mp: '', favorite: false, review: false, faces: false, geo: false,
})
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

const quote = (v: string): string => (/\s/.test(v) ? `"${v}"` : v)

function buildQuery(): string {
  const parts: string[] = []
  if (form.text.trim()) parts.push(form.text.trim())
  const simple: [string, string][] = [['type', form.type], ['year', form.year], ['month', form.month], ['label', form.label],
    ['person', form.person], ['album', form.album], ['camera', form.camera], ['color', form.color], ['ratio', form.ratio],
    ['mp', form.mp]]
  for (const [key, value] of simple) if (value.trim()) parts.push(`${key}:${quote(value.trim())}`)
  if (form.orientation) parts.push(`${form.orientation}:true`)
  for (const key of ['favorite', 'review', 'faces', 'geo'] as const) if (form[key]) parts.push(`${key}:true`)
  return parts.join(' ')
}

function apply(): void {
  const q = buildQuery()
  if (!q) return
  pushHistory(q)
  void router.push({ path: '/search', query: { q } })
  emit('close')
}

function reset(): void {
  Object.assign(form, { text: '', type: '', year: '', month: '', label: '', person: '', album: '', camera: '', color: '',
    orientation: '', ratio: '', mp: '', favorite: false, review: false, faces: false, geo: false })
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
        <div class="field"><label for="f-color">Colore</label>
          <select id="f-color" v-model="form.color" class="select"><option value="">Qualsiasi</option>
            <option v-for="c in COLORS" :key="c" :value="c">{{ c }}</option></select></div>
      </div>
      <div class="two">
        <div class="field"><label for="f-year">Anno</label>
          <input id="f-year" v-model="form.year" class="input" inputmode="numeric" placeholder="2024 o 2022-2024" /></div>
        <div class="field"><label for="f-month">Mese</label>
          <select id="f-month" v-model="form.month" class="select"><option value="">Tutti</option>
            <option v-for="(m, i) in MONTHS" :key="m" :value="String(i + 1)">{{ m }}</option></select></div>
      </div>
      <div class="field"><label for="f-label">Etichetta</label>
        <select id="f-label" v-model="form.label" class="select"><option value="">Qualsiasi</option>
          <option v-for="l in labels.slice(0, 200)" :key="l.slug" :value="l.slug">{{ l.name }} ({{ l.count }})</option></select></div>
      <div class="field"><label for="f-person">Persona</label>
        <select id="f-person" v-model="form.person" class="select"><option value="">Chiunque</option>
          <option v-for="p in people" :key="p.uid" :value="p.slug">{{ p.name }}</option></select></div>
      <div class="field"><label for="f-album">Album</label>
        <select id="f-album" v-model="form.album" class="select"><option value="">Tutti</option>
          <option v-for="a in albums" :key="a.uid" :value="a.slug">{{ a.title }}</option></select></div>
      <div class="two">
        <div class="field"><label for="f-camera">Fotocamera</label>
          <input id="f-camera" v-model="form.camera" class="input" placeholder="nikon d850" autocomplete="off" /></div>
        <div class="field"><label for="f-orient">Orientamento</label>
          <select id="f-orient" v-model="form.orientation" class="select"><option value="">Qualsiasi</option>
            <option value="landscape">Orizzontale</option><option value="portrait">Verticale</option><option value="square">Quadrata</option></select></div>
      </div>
      <div class="two">
        <div class="field"><label for="f-ratio">Formato</label>
          <select id="f-ratio" v-model="form.ratio" class="select"><option value="">Qualsiasi</option>
            <option v-for="r in RATIOS" :key="r" :value="r">{{ r }}</option></select></div>
        <div class="field"><label for="f-mp">Megapixel</label>
          <input id="f-mp" v-model="form.mp" class="input" inputmode="decimal" placeholder="12 o 8-20" autocomplete="off" /></div>
      </div>
      <fieldset class="checks">
        <legend class="caps">Solo</legend>
        <label><input v-model="form.favorite" type="checkbox" /> Preferiti</label>
        <label><input v-model="form.review" type="checkbox" /> Da rivedere</label>
        <label><input v-model="form.faces" type="checkbox" /> Con volti</label>
        <label><input v-model="form.geo" type="checkbox" /> Con posizione GPS</label>
      </fieldset>
    </div>
    <footer class="foot">
      <button type="button" class="btn" @click="reset">Azzera</button>
      <button type="submit" class="btn primary" :disabled="!buildQuery()"><Icon name="search" /> Cerca</button>
    </footer>
  </form>
</template>

<style scoped>
.panel-filters { display: flex; flex-direction: column; height: 100%; min-height: 0; padding: var(--space-3); background: var(--bg-sidebar); border-radius: inherit; }
.head { display: flex; align-items: center; gap: var(--space-2); padding-bottom: var(--space-3); }
.head h2 { font-size: var(--text-lg); }
.fields { display: flex; flex: 1; flex-direction: column; gap: var(--space-3); min-height: 0; overflow-y: auto; padding: 0 var(--space-1) var(--space-3); }
.two { display: grid; grid-template-columns: 1fr 1fr; gap: var(--space-3); }
.checks { display: grid; grid-template-columns: 1fr 1fr; gap: var(--space-2); margin: 0; padding: 0; border: 0; font-size: var(--text-sm); }
.checks legend { grid-column: 1 / -1; padding: 0 0 var(--space-1); }
.checks label { display: flex; align-items: center; gap: var(--space-2); }
.foot { display: flex; justify-content: flex-end; gap: var(--space-2); padding-top: var(--space-3); border-top: var(--border-width) solid var(--border-default); }
</style>
