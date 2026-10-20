<script setup lang="ts">
import PhotoSwipe from 'photoswipe'
import 'photoswipe/style.css'
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, cropUrl, liveUrl, originalUrl, thumbUrl, videoUrl } from '../lib/api'
import { formatBytes, formatDate, formatDuration } from '../lib/format'
import { libraryVersion, status, viewer } from '../lib/store'
import { errorMessage, toast } from '../lib/toast'
import type { Album, PhotoDetail, RemoveResult, SimilarHit } from '../lib/types'
import Dialog from './Dialog.vue'
import Dropdown from './Dropdown.vue'
import Icon from './Icon.vue'
import RemovePhotosDialog from './RemovePhotosDialog.vue'

// Dettaglio foto: la query `?photo=uid` è lo stato (il tasto indietro lo chiude). Frecce per scorrere la griglia.
const props = defineProps<{ uid: string | null }>()
const route = useRoute()
const router = useRouter()

const photo = ref<PhotoDetail | null>(null)
const loading = ref(false)
const failure = ref('')
const similar = ref<SimilarHit[]>([])
const similarReady = ref(true)
const albums = ref<Album[]>([])
const keywordInput = ref('')
const labelInput = ref('')
const title = ref('')
const caption = ref('')
const liveVideo = ref<HTMLVideoElement | null>(null)
const videoError = ref(false)
const body = ref<HTMLElement | null>(null)
let token = 0

const open = computed(() => props.uid !== null)
const index = computed(() => viewer.photos.findIndex((p) => p.uid === props.uid))
const previous = computed(() => (index.value > 0 ? viewer.photos[index.value - 1] : null))
const next = computed(() => (index.value >= 0 && index.value < viewer.photos.length - 1 ? viewer.photos[index.value + 1] : null))
const camera = computed(() => (photo.value?.camera ? `${photo.value.camera.make} ${photo.value.camera.model}`.trim() : ''))

function close(): void {
  const { photo: _removed, ...rest } = route.query
  void router.push({ query: rest })
}

function go(uid: string | undefined): void {
  if (uid) void router.replace({ query: { ...route.query, photo: uid } })
}

async function load(uid: string): Promise<void> {
  const mine = ++token
  loading.value = true
  failure.value = ''
  videoError.value = false
  similar.value = []
  try {
    const detail = await api.photo(uid)
    if (mine !== token) return
    photo.value = detail
    title.value = detail.title
    caption.value = detail.caption
    void loadSimilar(uid, mine)
    await nextTick()
    body.value?.scrollTo({ top: 0 })
  } catch (error) {
    if (mine === token) failure.value = errorMessage(error)
  } finally {
    if (mine === token) loading.value = false
  }
}

async function loadSimilar(uid: string, mine: number): Promise<void> {
  try {
    const result = await api.similar(uid, 8)
    if (mine !== token) return
    similar.value = result.hits
    similarReady.value = result.ready
  } catch {
    similar.value = []
  }
}

watch(() => props.uid, (uid) => {
  if (uid) void load(uid)
  else photo.value = null
}, { immediate: true })
watch(libraryVersion, () => props.uid && void load(props.uid))

onMounted(async () => {
  try {
    albums.value = await api.albums('album')
  } catch {
    albums.value = []
  }
  window.addEventListener('keydown', onKey)
})
onBeforeUnmount(() => window.removeEventListener('keydown', onKey))

function onKey(event: KeyboardEvent): void {
  if (!open.value || event.ctrlKey || event.metaKey || event.altKey) return
  const el = event.target as HTMLElement | null
  if (el && (el.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName))) return
  if (event.key === 'ArrowLeft') go(previous.value?.uid)
  else if (event.key === 'ArrowRight') go(next.value?.uid)
  else if (event.key.toLowerCase() === 'f' && photo.value) void save({ favorite: !photo.value.favorite })
}

async function save(changes: Record<string, unknown>): Promise<void> {
  if (!photo.value) return
  try {
    photo.value = await api.patchPhoto(photo.value.uid, changes)
    title.value = photo.value.title
    caption.value = photo.value.caption
    libraryVersion.value++
  } catch (error) {
    toast(errorMessage(error), 'error')
  }
}

function addKeyword(): void {
  const word = keywordInput.value.trim()
  keywordInput.value = ''
  if (word) void save({ keywords_add: [word] })
}

function addLabel(): void {
  const name = labelInput.value.trim()
  labelInput.value = ''
  if (name) void save({ labels_add: [name] })
}

async function toAlbum(album: Album): Promise<void> {
  if (!photo.value) return
  try {
    await api.batch([photo.value.uid], 'add_to_album', album.uid)
    toast(`Aggiunta a «${album.title}»`)
    await load(photo.value.uid)
  } catch (error) {
    toast(errorMessage(error), 'error')
  }
}

async function fromAlbum(uid: string): Promise<void> {
  if (!photo.value) return
  try {
    await api.batch([photo.value.uid], 'remove_from_album', uid)
    await load(photo.value.uid)
  } catch (error) {
    toast(errorMessage(error), 'error')
  }
}

async function nameFace(marker: string, event: Event): Promise<void> {
  const input = event.target as HTMLInputElement
  const name = input.value.trim()
  if (!name) return
  try {
    await api.nameMarker(marker, name)
    if (photo.value) await load(photo.value.uid)
    libraryVersion.value++
  } catch (error) {
    toast(errorMessage(error), 'error')
  }
}

async function rejectFace(marker: string): Promise<void> {
  try {
    await api.rejectMarker(marker)
    if (photo.value) await load(photo.value.uid)
  } catch (error) {
    toast(errorMessage(error), 'error')
  }
}

async function copyPath(path: string): Promise<void> {
  try {
    await navigator.clipboard.writeText(path)
    toast('Percorso copiato')
  } catch {
    toast('Non riesco a copiare: seleziona il percorso e premi Ctrl+C', 'error')
  }
}

async function captionWithLlm(): Promise<void> {
  if (!photo.value) return
  try {
    await api.caption(true, [photo.value.uid])
    toast('Didascalia in coda: arriva in qualche decina di secondi')
  } catch (error) {
    toast(errorMessage(error), 'error')
  }
}

function playLive(on: boolean): void {
  const video = liveVideo.value
  if (!video) return
  if (on) void video.play().catch(() => undefined)
  else {
    video.pause()
    video.currentTime = 0
  }
}

function openLightbox(): void {
  const items = viewer.photos.filter((p) => p.type !== 'video' && p.hash).map((p) => {
    const ratio = p.width && p.height ? p.width / p.height : 1.5
    const w = ratio >= 1.6 ? 2560 : Math.round(1600 * ratio)
    return { uid: p.uid, src: thumbUrl(p.hash, 'fit_2560'), width: w, height: Math.round(w / ratio), alt: p.title }
  })
  const start = items.findIndex((i) => i.uid === props.uid)
  if (start < 0) return
  const dialog = body.value?.closest('dialog') ?? document.body
  const pswp = new PhotoSwipe({ dataSource: items, index: start, bgOpacity: 1, appendToEl: dialog as HTMLElement,
                                closeTitle: 'Chiudi', zoomTitle: 'Zoom', arrowPrevTitle: 'Precedente', arrowNextTitle: 'Successiva' })
  pswp.on('change', () => go(items[pswp.currIndex]?.uid))
  pswp.init()
}

const llmReady = computed(() => status.value?.llm_ready ?? false)

const personQuery = (name: string): string => `person:"${name.replace(/"/g, ' ')}"`

const removing = ref(false)

/** Dopo «Elimina» si passa alla foto accanto (o si chiude, se era l'ultima): la griglia si aggiorna da sola. */
function afterRemove(result: RemoveResult): void {
  removing.value = false
  if (!result.removed) return
  const target = next.value ?? previous.value
  if (target) go(target.uid)
  else close()
}

async function toggleArchive(): Promise<void> {
  if (!photo.value) return
  try {
    await api.batch([photo.value.uid], photo.value.archived ? 'restore' : 'archive')
    libraryVersion.value++
    close()
  } catch (error) {
    toast(errorMessage(error), 'error')
  }
}
</script>

<template>
  <Dialog :open="open" title="Dettaglio foto" size="xl" bare @close="close">
    <div class="detail">
      <header class="top">
        <button type="button" class="btn ghost icon" aria-label="Foto precedente" :disabled="!previous" @click="go(previous?.uid)"><Icon name="chevronLeft" /></button>
        <button type="button" class="btn ghost icon" aria-label="Foto successiva" :disabled="!next" @click="go(next?.uid)"><Icon name="chevronRight" /></button>
        <span class="pos muted" v-if="index >= 0">{{ index + 1 }} / {{ viewer.photos.length }}</span>
        <span class="grow" />
        <template v-if="photo">
          <button type="button" class="btn ghost icon" :class="{ active: photo.favorite }" :aria-pressed="photo.favorite" aria-label="Preferita" @click="save({ favorite: !photo.favorite })"><Icon name="heart" /></button>
          <button type="button" class="btn ghost icon" :class="{ active: photo.private }" :aria-pressed="photo.private" aria-label="Privata" @click="save({ private: !photo.private })"><Icon name="lock" /></button>
          <Dropdown label="Altre azioni">
            <a class="menu-item" role="menuitem" :href="originalUrl(photo.uid)" target="_blank" rel="noopener"><Icon name="external" /> Apri l'originale</a>
            <a class="menu-item" role="menuitem" :href="originalUrl(photo.uid)" :download="photo.files[0]?.name"><Icon name="download" /> Scarica</a>
            <button v-if="llmReady" type="button" class="menu-item" role="menuitem" @click="captionWithLlm"><Icon name="wand" /> Descrivi con l'AI</button>
            <div class="menu-sep" />
            <button type="button" class="menu-item" :class="{ danger: !photo.archived }" role="menuitem"
                    @click="toggleArchive">
              <Icon name="archive" /> {{ photo.archived ? 'Ripristina dall\'archivio' : 'Archivia' }}
            </button>
            <button type="button" class="menu-item danger" role="menuitem" data-test="detail-remove" @click="removing = true">
              <Icon name="trash" /> Elimina…
            </button>
          </Dropdown>
        </template>
        <button type="button" class="btn ghost icon" aria-label="Chiudi" @click="close"><Icon name="x" /></button>
      </header>

      <div ref="body" class="content">
        <div v-if="failure" class="empty" role="alert"><h3>Foto non disponibile</h3><p>{{ failure }}</p></div>
        <div v-else-if="!photo" class="skeleton stage" aria-busy="true" />
        <template v-else>
          <section class="media" aria-label="Anteprima">
            <div class="stage" :style="{ aspectRatio: photo.width && photo.height ? `${photo.width} / ${photo.height}` : '3 / 2' }">
              <template v-if="photo.type === 'video'">
                <video v-if="!videoError" class="fill" controls preload="metadata" :poster="thumbUrl(photo.hash, 'fit_1280')" :src="videoUrl(photo.uid)" @error="videoError = true" />
                <div v-else class="converting">
                  <Icon name="spinner" class="spin" /> Converto il video per il browser… riprova tra poco.
                  <button type="button" class="btn sm" @click="videoError = false">Riprova</button>
                </div>
              </template>
              <template v-else>
                <button type="button" class="fill zoom" aria-label="Apri a schermo intero" @click="openLightbox"
                        @mouseenter="playLive(true)" @mouseleave="playLive(false)" @focus="playLive(true)" @blur="playLive(false)">
                  <img :src="thumbUrl(photo.hash, 'fit_1920')" :alt="photo.title || 'Foto'" />
                  <video v-if="photo.type === 'live'" ref="liveVideo" class="live" :src="liveUrl(photo.uid)" muted loop playsinline preload="none" />
                </button>
                <span v-for="p in photo.people" :key="p.marker" class="face" :title="p.name || 'Volto'"
                      :style="{ left: `${p.x * 100}%`, top: `${p.y * 100}%`, width: `${p.w * 100}%`, height: `${p.h * 100}%` }" />
                <span v-if="photo.type === 'live'" class="chip live-chip">LIVE</span>
              </template>
            </div>
          </section>

          <section class="info" aria-label="Informazioni">
            <div class="field">
              <label for="d-title">Titolo <span v-if="photo.title_source" class="chip">{{ photo.title_source }}</span></label>
              <input id="d-title" v-model="title" class="input" maxlength="300" placeholder="Senza titolo" @change="save({ title })" />
            </div>
            <div class="field">
              <label for="d-caption">Didascalia <span v-if="photo.caption_source" class="chip">{{ photo.caption_source }}</span></label>
              <textarea id="d-caption" v-model="caption" class="textarea" rows="3" maxlength="2000" placeholder="Aggiungi una descrizione" @change="save({ caption })" />
            </div>

            <div class="group">
              <h3 class="caps">Etichette</h3>
              <div class="chips">
                <span v-for="l in photo.labels" :key="l.slug" class="chip" :class="{ accent: l.source === 'manuale' }" :title="`${l.confidence}% · ${l.source}`">
                  <RouterLink :to="{ path: '/search', query: { q: `label:${l.slug}` } }" class="plain">{{ l.name }}</RouterLink>
                  <button type="button" :aria-label="`Rimuovi l'etichetta ${l.name}`" @click="save({ labels_remove: [l.slug] })"><Icon name="x" :size="12" /></button>
                </span>
                <span v-if="!photo.labels.length" class="subtle small">Nessuna etichetta{{ status?.ai_ready ? '' : ' (modello AI non installato)' }}</span>
              </div>
              <input v-model="labelInput" class="input" placeholder="Aggiungi un'etichetta e premi Invio" aria-label="Nuova etichetta" @keydown.enter.prevent="addLabel" />
            </div>

            <div class="group">
              <h3 class="caps">Parole chiave</h3>
              <div class="chips">
                <span v-for="k in photo.keywords" :key="k" class="chip">
                  {{ k }}<button type="button" :aria-label="`Rimuovi ${k}`" @click="save({ keywords_remove: [k] })"><Icon name="x" :size="12" /></button>
                </span>
              </div>
              <input v-model="keywordInput" class="input" placeholder="Aggiungi una parola chiave" aria-label="Nuova parola chiave" @keydown.enter.prevent="addKeyword" />
            </div>

            <div v-if="photo.people.length" class="group">
              <h3 class="caps">Persone</h3>
              <ul class="people">
                <li v-for="p in photo.people" :key="p.marker">
                  <img :src="cropUrl(p.marker, 64)" alt="" width="40" height="40" />
                  <input v-if="!p.name" class="input sm" placeholder="Chi è?" :aria-label="`Nome del volto ${p.marker}`" @keydown.enter.prevent="nameFace(p.marker, $event)" @change="nameFace(p.marker, $event)" />
                  <RouterLink v-else :to="{ path: '/search', query: { q: personQuery(p.name) } }" class="grow plain">{{ p.name }}</RouterLink>
                  <button type="button" class="btn ghost icon sm" aria-label="Non è un volto" @click="rejectFace(p.marker)"><Icon name="x" /></button>
                </li>
              </ul>
            </div>

            <div class="group">
              <h3 class="caps">Album</h3>
              <div class="chips">
                <span v-for="a in photo.albums" :key="a.uid" class="chip accent">
                  <RouterLink :to="`/albums/${a.uid}`" class="plain">{{ a.title }}</RouterLink>
                  <button type="button" :aria-label="`Togli da ${a.title}`" @click="fromAlbum(a.uid)"><Icon name="x" :size="12" /></button>
                </span>
                <Dropdown label="Aggiungi a un album" btn-class="btn sm" align="left">
                  <template #button><Icon name="plus" /> Aggiungi</template>
                  <button v-for="a in albums" :key="a.uid" type="button" class="menu-item" role="menuitem" @click="toAlbum(a)">{{ a.title }}</button>
                  <span v-if="!albums.length" class="menu-item subtle">Nessun album: creane uno da Album.</span>
                </Dropdown>
              </div>
            </div>

            <div class="group">
              <h3 class="caps">Dati</h3>
              <dl class="meta">
                <dt>Scattata</dt><dd>{{ formatDate(photo.taken_at, true) }} <span v-if="photo.taken_source" class="chip">{{ photo.taken_source }}</span></dd>
                <template v-if="camera"><dt>Fotocamera</dt><dd>{{ camera }}</dd></template>
                <template v-if="photo.lens"><dt>Obiettivo</dt><dd>{{ photo.lens }}</dd></template>
                <template v-if="photo.iso || photo.exposure || photo.f_number || photo.focal_length">
                  <dt>Esposizione</dt>
                  <dd class="mono">{{ [photo.focal_length && `${photo.focal_length} mm`, photo.f_number && `ƒ/${photo.f_number}`, photo.exposure && `${photo.exposure} s`, photo.iso && `ISO ${photo.iso}`].filter(Boolean).join(' · ') }}</dd>
                </template>
                <dt>Dimensioni</dt><dd>{{ photo.width }} × {{ photo.height }}<template v-if="photo.duration"> · {{ formatDuration(photo.duration) }}</template></dd>
                <template v-if="photo.lat !== null"><dt>Posizione</dt><dd class="mono">{{ photo.lat?.toFixed(5) }}, {{ photo.lng?.toFixed(5) }}</dd></template>
                <dt>Colori</dt><dd class="chips"><span v-for="c in photo.colors" :key="c" class="chip">{{ c }}</span></dd>
                <dt>Qualità</dt><dd>{{ photo.quality }} <span v-if="photo.review" class="chip to-verify">da rivedere</span></dd>
              </dl>
            </div>

            <div class="group">
              <h3 class="caps">File</h3>
              <ul class="files">
                <li v-for="f in photo.files" :key="f.path" :class="{ missing: f.missing }">
                  <span class="grow">
                    <strong class="truncate">{{ f.name }}</strong>
                    <span class="subtle small">{{ f.media_type }} · {{ formatBytes(f.size) }}<template v-if="f.original_name !== f.name"> · era {{ f.original_name }}</template></span>
                  </span>
                  <button type="button" class="btn ghost icon sm" :aria-label="`Copia il percorso di ${f.name}`" @click="copyPath(f.path)"><Icon name="copy" /></button>
                </li>
              </ul>
            </div>
          </section>

          <section class="similar" aria-label="Foto simili">
            <h3 class="caps">Foto simili</h3>
            <p v-if="!similarReady" class="subtle small">Le foto simili compaiono quando l'analisi AI ha elaborato questa foto.</p>
            <ul v-else class="sim-grid">
              <li v-for="h in similar" :key="h.photo.uid">
                <button type="button" class="sim" :aria-label="`Foto simile al ${Math.round(h.score * 100)}%`" @click="go(h.photo.uid)">
                  <img :src="thumbUrl(h.photo.hash, 'tile_224')" :alt="h.photo.title || 'Foto simile'" loading="lazy" />
                  <span class="score">{{ Math.round(h.score * 100) }}%</span>
                  <span class="why">
                    <template v-for="(reasons, axis) in h.reasons" :key="axis"><b>{{ axis }}</b> {{ reasons.join('; ') }}<br /></template>
                  </span>
                </button>
              </li>
            </ul>
          </section>
        </template>
      </div>
    </div>
    <RemovePhotosDialog :open="removing" :uids="props.uid ? [props.uid] : []" @close="removing = false" @done="afterRemove" />
  </Dialog>
</template>

<style scoped>
.detail { display: flex; flex-direction: column; min-height: 0; height: 94dvh; }
.top { display: flex; align-items: center; gap: var(--space-1); padding: var(--space-2) var(--space-3); border-bottom: 1px solid var(--border-subtle); }
.pos { padding: 0 var(--space-2); font-size: var(--text-sm); }
.content {
  display: grid; grid-template-columns: minmax(0, 1.6fr) minmax(18rem, 1fr); gap: var(--space-5); flex: 1; align-content: start;
  overflow-y: auto; padding: var(--space-4) var(--space-5) var(--space-6); overscroll-behavior: contain;
}
.media { grid-column: 1; position: sticky; top: 0; align-self: start; }
.info { grid-column: 2; display: flex; flex-direction: column; gap: var(--space-4); min-width: 0; }
.similar { grid-column: 1 / -1; }
.stage { position: relative; width: 100%; max-height: 72dvh; border-radius: var(--radius-panel); background: var(--stage-bg); overflow: hidden; }
.skeleton.stage { aspect-ratio: 3 / 2; grid-column: 1 / -1; }
.fill { position: absolute; inset: 0; width: 100%; height: 100%; }
.zoom { padding: 0; border: 0; background: none; cursor: zoom-in; }
.zoom img, .live { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: contain; }
.live { opacity: 0; transition: opacity var(--dur-base) var(--ease-out); pointer-events: none; }
.zoom:hover .live, .zoom:focus-visible .live { opacity: 1; }
.live-chip { position: absolute; left: var(--space-2); top: var(--space-2); background: var(--overlay-scrim); color: var(--overlay-fg); border-color: transparent; }
.face { position: absolute; border: 2px solid var(--accent); border-radius: var(--radius-control); pointer-events: none; box-shadow: 0 0 0 1px var(--overlay-scrim); }
.converting { position: absolute; inset: 0; display: grid; place-content: center; justify-items: center; gap: var(--space-2); color: var(--fg-muted); font-size: var(--text-sm); }
.group { display: flex; flex-direction: column; gap: var(--space-2); }
.chips { display: flex; flex-wrap: wrap; align-items: center; gap: var(--space-1-5); }
.small { font-size: var(--text-xs); }
.plain { color: inherit; text-decoration: none; }
.meta { display: grid; grid-template-columns: max-content 1fr; gap: var(--space-1) var(--space-4); margin: 0; font-size: var(--text-sm); }
.meta dt { color: var(--fg-muted); }
.meta dd { margin: 0; min-width: 0; overflow-wrap: anywhere; }
.people, .files { display: grid; gap: var(--space-2); margin: 0; padding: 0; list-style: none; }
.people li { display: flex; align-items: center; gap: var(--space-2-5); }
.people img { border-radius: var(--radius-pill); object-fit: cover; }
.files li { display: flex; align-items: center; gap: var(--space-2); font-size: var(--text-sm); }
.files li.missing { opacity: 0.55; }
.files .grow { display: grid; min-width: 0; }
.sim-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(8.5rem, 1fr)); gap: var(--space-2); margin: 0; padding: 0; list-style: none; }
.sim { position: relative; display: block; width: 100%; aspect-ratio: 1; padding: 0; border: 0; border-radius: var(--radius-control); overflow: hidden; background: var(--stage-cell); cursor: pointer; }
.sim img { width: 100%; height: 100%; object-fit: cover; }
.score { position: absolute; right: var(--space-1-5); top: var(--space-1-5); padding: 1px var(--space-1-5); border-radius: var(--radius-chip); background: var(--overlay-scrim); color: var(--overlay-fg); font-size: var(--text-2xs); font-weight: var(--weight-semibold); }
.why {
  position: absolute; inset: auto 0 0 0; padding: var(--space-4) var(--space-2) var(--space-1-5); background: var(--overlay-gradient); color: var(--overlay-fg);
  font-size: var(--text-2xs); line-height: var(--leading-snug); text-align: left; opacity: 0; transition: opacity var(--dur-fast) var(--ease-out);
}
.sim:hover .why, .sim:focus-visible .why { opacity: 1; }
@media (max-width: 60rem) {
  .content { grid-template-columns: minmax(0, 1fr); }
  .media, .info, .similar { grid-column: 1; position: static; }
}
</style>
