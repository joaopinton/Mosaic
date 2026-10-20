<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import Dialog from '../components/Dialog.vue'
import ExportDialog from '../components/ExportDialog.vue'
import Icon from '../components/Icon.vue'
import PageFrame from '../components/PageFrame.vue'
import PhotoGrid from '../components/PhotoGrid.vue'
import PosterDialog from '../components/PosterDialog.vue'
import { api, ApiError } from '../lib/api'
import { formatSpan, fromRouteQuery, MODES, newSeed, shapeLabel, toRouteQuery } from '../lib/curate'
import { formatCount } from '../lib/format'
import { currentSection, libraryVersion, openCurate, selected } from '../lib/store'
import { SECTION_OF } from '../lib/nav'
import { errorMessage, toast } from '../lib/toast'
import type { CurateResult } from '../lib/types'

// Selezione di un album o di una cartella: si apre come una ricerca, i parametri stanno nell'indirizzo.
const route = useRoute()
const router = useRouter()
const result = ref<CurateResult | null>(null)
const loading = ref(false)
const failure = ref('')
const missing = ref(false)
const saving = ref(false)
const saveTitle = ref('')
const posterOpen = ref(false)
const exportOpen = ref(false)
const posterUids = computed(() => photos.value.map((p) => p.uid))
let generation = 0

const uid = computed(() => String(route.params.uid))
const params = computed(() => fromRouteQuery(route.query))
const album = computed(() => result.value?.album ?? null)
const stats = computed(() => result.value?.stats ?? null)
const photos = computed(() => result.value?.photos ?? [])
const hints = computed(() => Object.fromEntries(photos.value.map((p) => [p.uid, p.reasons.join(' · ')])))
const peak = computed(() => Math.max(1, ...(stats.value?.timeline ?? []).map((b) => b[0])))
const unnamed = computed(() => (stats.value ? stats.value.people_covered - stats.value.people.length : 0))

const subtitle = computed(() => {
  if (!stats.value) return ''
  const parts = [`${formatCount(stats.value.selected)} foto scelte su ${formatCount(stats.value.candidates)}`]
  if (params.value.shape) parts.push(shapeLabel(params.value.shape))
  parts.push(MODES.find((m) => m.value === params.value.mode)?.label ?? '')
  return parts.filter(Boolean).join(' · ')
})

async function load(): Promise<void> {
  const mine = ++generation
  loading.value = true
  failure.value = ''
  missing.value = false
  try {
    const data = await api.curate(uid.value, params.value)
    if (mine === generation) {
      result.value = data
      currentSection.value = SECTION_OF[data.album.type] ?? null
    }
  } catch (error) {
    if (mine !== generation) return
    if (error instanceof ApiError && error.status === 404) missing.value = true
    else failure.value = errorMessage(error)
  } finally {
    if (mine === generation) loading.value = false
  }
}

// chiave primitiva: aprire una foto (?photo=) non cambia i parametri e non deve ricalcolare né svuotare la selezione
const key = computed(() => `${uid.value}|${JSON.stringify(params.value)}`)
watch(key, () => { if (route.path.endsWith('/selection')) void load() }, { immediate: true })
watch(libraryVersion, () => { if (result.value) void load() })

onBeforeUnmount(() => { currentSection.value = null })

function edit(): void {
  openCurate({ uid: uid.value, title: album.value?.title ?? '' }, params.value)
}

function regenerate(): void {
  void router.replace({ query: toRouteQuery({ ...params.value, seed: newSeed() }) })
}

function selectAll(): void {
  selected.value = photos.value.map((p) => p.uid)
}

function askSave(): void {
  saveTitle.value = `Selezione di ${album.value?.title ?? 'questo album'}`.slice(0, 200)
  saving.value = true
}

async function save(): Promise<void> {
  const title = saveTitle.value.trim()
  if (!title) return
  try {
    const created = await api.createAlbum(title, photos.value.map((p) => p.uid), uid.value)
    saving.value = false
    toast(`«${created.title}» salvata in ${album.value?.title ?? 'album'} con ${formatCount(created.photo_count)} foto`)
    void router.push(`/albums/${uid.value}`) // la ritrovi nella sezione «Selezioni e poster» dell'album di partenza
  } catch (error) {
    toast(errorMessage(error), 'error')
  }
}
</script>

<template>
  <PageFrame v-if="missing" title="Album non trovato">
    <div class="empty"><h3>Questo album non esiste più</h3><RouterLink to="/albums" class="btn">Torna agli album</RouterLink></div>
  </PageFrame>
  <PageFrame v-else :title="album ? `Selezione · ${album.title}` : 'Selezione'" :subtitle="subtitle" width="full">
    <template #actions>
      <RouterLink v-if="album" :to="`/albums/${album.uid}`" class="btn ghost"><Icon name="chevronLeft" /> Album</RouterLink>
      <button type="button" class="btn" :disabled="!album" @click="edit"><Icon name="sliders" /> Modifica</button>
      <button type="button" class="btn" :disabled="loading || !photos.length" @click="regenerate"><Icon name="shuffle" /> Rigenera</button>
      <button type="button" class="btn" :disabled="!photos.length" @click="selectAll"><Icon name="check" /> Seleziona tutte</button>
      <button type="button" class="btn" :disabled="!photos.length" data-test="poster-open" @click="posterOpen = true"><Icon name="grid" /> Crea poster</button>
      <button type="button" class="btn" :disabled="!photos.length" data-test="selection-export" @click="exportOpen = true"><Icon name="download" /> Esporta</button>
      <button type="button" class="btn primary" :disabled="!photos.length" data-test="save-selection" @click="askSave"><Icon name="album" /> Salva nell'album</button>
    </template>

    <div v-if="loading && !result" class="empty" role="status">
      <Icon name="spinner" :size="28" class="spin" />
      <h3>Scelgo le foto migliori…</h3>
      <p>La prima volta guardo nitidezza e luce di ogni foto: con album grandi può volerci qualche secondo.</p>
    </div>
    <div v-else-if="failure" class="empty" role="alert">
      <h3>Non sono riuscito a creare la selezione</h3>
      <p>{{ failure }}</p>
      <button type="button" class="btn" @click="load">Riprova</button>
    </div>
    <template v-else-if="result && stats">
      <section class="panel how" :class="{ busy: loading }" aria-labelledby="how-title">
        <div class="how-head">
          <h2 id="how-title">Come ho scelto</h2>
          <p class="muted">{{ formatSpan(stats.start, stats.end) }}</p>
        </div>
        <div v-if="stats.timeline.length" class="timeline" role="img"
             :aria-label="`Linea del tempo dell'evento: ${stats.selected} foto scelte distribuite su ${stats.candidates}`">
          <div v-for="(bin, i) in stats.timeline" :key="i" class="bin">
            <span class="bar" :style="{ height: `${Math.max(bin[0] ? 8 : 0, (bin[0] / peak) * 100)}%` }" />
            <span v-if="bin[1]" class="dot" :title="`${bin[1]} scelte qui`">{{ bin[1] > 1 ? bin[1] : '' }}</span>
          </div>
        </div>
        <ul class="facts">
          <li v-if="stats.people_total">
            <Icon name="people" :size="15" />
            <span>{{ stats.people_covered }} {{ stats.people_covered === 1 ? 'persona' : 'persone' }} su {{ stats.people_total }}</span>
            <span v-for="p in stats.people" :key="p.name" class="chip accent">{{ p.name }} · {{ p.count }}</span>
            <span v-if="unnamed > 0" class="chip">+{{ unnamed }} senza nome</span>
          </li>
          <li v-if="stats.selected < params.count && stats.candidates > stats.selected" class="warn">
            <Icon name="info" :size="15" /> Ho trovato solo {{ stats.selected }} foto abbastanza diverse tra loro: le altre erano raffiche o quasi-doppioni.
          </li>
          <li v-if="stats.rejected"><Icon name="eye" :size="15" /> {{ formatCount(stats.rejected) }} scartate perché mosse, sfocate o troppo buie</li>
          <li v-if="stats.not_chosen"><Icon name="duplicates" :size="15" /> {{ formatCount(stats.not_chosen) }} lasciate fuori perché simili ad altre o meno riuscite</li>
          <li v-if="!stats.ai_ready" class="warn"><Icon name="alert" :size="15" /> L'analisi AI non è ancora finita: per ora scelgo solo con nitidezza, luce, orario e volti.</li>
        </ul>
      </section>

      <div v-if="!photos.length" class="empty">
        <Icon name="images" :size="28" />
        <h3>Nessuna foto adatta</h3>
        <p>Prova con un altro formato o togli il testo «Deve esserci».</p>
        <button type="button" class="btn primary" @click="edit"><Icon name="sliders" /> Modifica</button>
      </div>
      <PhotoGrid v-else :items="photos" :hints="hints" />
    </template>

    <ExportDialog :open="exportOpen" :uids="posterUids" :title="`Selezione di ${album?.title ?? 'Mosaic'}`" @close="exportOpen = false" />
    <PosterDialog v-if="album" :open="posterOpen" :uids="posterUids" :title="album.title" :album="album.uid" @close="posterOpen = false" />

    <Dialog :open="saving" title="Salva la selezione" size="sm" @close="saving = false">
      <form class="field" @submit.prevent="save">
        <label for="sel-title">Titolo</label>
        <input id="sel-title" v-model="saveTitle" class="input" maxlength="200" autocomplete="off" />
        <p class="hint">Le {{ photos.length }} foto restano dove sono: la selezione si salva dentro «{{ album?.title }}» e la ritrovi lì, in «Selezioni e poster».</p>
      </form>
      <template #footer>
        <button type="button" class="btn" @click="saving = false">Annulla</button>
        <button type="button" class="btn primary" :disabled="!saveTitle.trim()" @click="save">Salva</button>
      </template>
    </Dialog>
  </PageFrame>
</template>

<style scoped>
.how { display: flex; flex-direction: column; gap: var(--space-3); margin-bottom: var(--space-4); padding: var(--space-4) var(--space-5);
  transition: opacity var(--dur-fast) var(--ease-out); }
.how.busy { opacity: 0.55; }
.how-head { display: flex; align-items: baseline; justify-content: space-between; gap: var(--space-3); flex-wrap: wrap; }
.how-head h2 { font-size: var(--text-lg); }
.how-head p { font-size: var(--text-sm); }
.timeline { display: flex; align-items: flex-end; gap: 2px; height: 56px; padding-bottom: var(--space-1); border-bottom: 1px solid var(--border-subtle); }
.bin { position: relative; display: flex; flex: 1; align-items: flex-end; justify-content: center; height: 100%; min-width: 0; }
.bar { display: block; width: 100%; border-radius: 2px 2px 0 0; background: var(--stage-cell); }
.dot {
  position: absolute; bottom: 0; display: grid; place-items: center; min-width: 14px; height: 14px; padding: 0 2px;
  border: 2px solid var(--bg-surface); border-radius: var(--radius-pill); background: var(--accent); color: var(--fg-on-accent);
  font-size: 9px; font-weight: var(--weight-bold); line-height: 1; transform: translateY(50%);
}
.facts { display: flex; flex-direction: column; gap: var(--space-2); margin: var(--space-1) 0 0; padding: 0; list-style: none; font-size: var(--text-sm); color: var(--fg-muted); }
.facts li { display: flex; align-items: center; flex-wrap: wrap; gap: var(--space-1-5); }
.facts .warn { color: var(--to-verify-fg); }
@media (max-width: 40rem) { .how { padding: var(--space-3); } }
</style>
