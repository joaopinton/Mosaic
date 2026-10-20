<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, thumbUrl } from '../lib/api'
import { formatDuration } from '../lib/format'
import { justify } from '../lib/layout'
import { clearSelection, hasSelection, libraryVersion, selected, toggleSelected, viewer } from '../lib/store'
import { errorMessage } from '../lib/toast'
import type { PhotoSummary } from '../lib/types'
import Icon from './Icon.vue'

// Griglia giustificata con scorrimento infinito. La query è la sola fonte di verità: cambia la query, si ricarica.
// Con `items` mostra invece un elenco già pronto (per esempio una selezione), senza chiedere nulla al server.
const props = withDefaults(defineProps<{
  query?: string
  items?: PhotoSummary[]
  hints?: Record<string, string>
  order?: string
  reverse?: boolean
  emptyTitle?: string
  emptyText?: string
}>(), { query: '', order: '', reverse: false, emptyTitle: 'Nessuna foto', emptyText: 'Qui non c\'è ancora niente.' })
const emit = defineEmits<{ loaded: [total: number, semantic: boolean] }>()

const PAGE = 60
const GAP = 4
const TARGET = 220

const route = useRoute()
const router = useRouter()
const photos = ref<PhotoSummary[]>([])
const total = ref(0)
const loading = ref(false)
const failure = ref('')
const finished = ref(false)
const width = ref(0)
const container = ref<HTMLElement | null>(null)
const sentinel = ref<HTMLElement | null>(null)
let generation = 0
let resizeObserver: ResizeObserver | undefined
let intersection: IntersectionObserver | undefined

const rows = computed(() => justify(photos.value, width.value, width.value < 600 ? 140 : TARGET, GAP))

function showItems(items: PhotoSummary[]): void {
  photos.value = items
  total.value = items.length
  finished.value = true
  viewer.photos = items
  emit('loaded', items.length, false)
}

async function load(reset: boolean, keepCount = PAGE): Promise<void> {
  if (props.items) return showItems(props.items)
  if (loading.value || (!reset && finished.value)) return
  const mine = ++generation
  loading.value = true
  failure.value = ''
  try {
    const offset = reset ? 0 : photos.value.length
    const result = await api.photos({ q: props.query, count: reset ? Math.min(Math.max(keepCount, PAGE), 500) : PAGE, offset,
                                      order: props.order, reverse: props.reverse })
    if (mine !== generation) return
    photos.value = reset ? result.photos : [...photos.value, ...result.photos]
    total.value = result.total
    finished.value = photos.value.length >= result.total || result.photos.length === 0
    viewer.photos = photos.value
    emit('loaded', result.total, result.semantic)
  } catch (error) {
    if (mine === generation) failure.value = errorMessage(error)
  } finally {
    if (mine === generation) loading.value = false
  }
}

function restart(): void {
  generation++
  loading.value = false
  finished.value = false
  photos.value = []
  clearSelection()
  void load(true)
}

watch(() => [props.query, props.order, props.reverse, props.items], restart)
// quando un job termina i dati cambiano: si ricarica quanto già mostrato, senza perdere la posizione
watch(libraryVersion, () => {
  loading.value = false
  finished.value = false
  void load(true, photos.value.length)
})

onMounted(() => {
  resizeObserver = new ResizeObserver((entries) => {
    width.value = Math.floor(entries[0].contentRect.width)
  })
  if (container.value) resizeObserver.observe(container.value)
  intersection = new IntersectionObserver((entries) => {
    if (entries.some((e) => e.isIntersecting)) void load(false)
  }, { rootMargin: '800px' })
  if (sentinel.value) intersection.observe(sentinel.value)
  void load(true)
})

onBeforeUnmount(() => {
  generation++
  resizeObserver?.disconnect()
  intersection?.disconnect()
})

function open(photo: PhotoSummary, event: MouseEvent | KeyboardEvent): void {
  if (hasSelection.value || event.ctrlKey || event.metaKey) return toggleSelected(photo.uid)
  void router.push({ query: { ...route.query, photo: photo.uid } })
}

function label(p: PhotoSummary): string {
  return p.title || (p.taken_at_local ? new Date(p.taken_at_local).toLocaleDateString('it-IT') : 'Foto senza titolo')
}
</script>

<template>
  <div ref="container" class="grid" :aria-busy="loading">
    <div v-if="failure" class="empty" role="alert">
      <h3>Impossibile caricare le foto</h3>
      <p>{{ failure }}</p>
      <button type="button" class="btn" @click="restart">Riprova</button>
    </div>

    <template v-else>
      <div v-for="(row, r) in rows" :key="r" class="line" :style="{ height: `${row.height}px` }">
        <div v-for="tile in row.tiles" :key="photos[tile.index].uid" class="cell" :style="{ width: `${tile.width}px`, height: `${tile.height}px` }">
          <button
            type="button"
            class="tile"
            :class="{ picked: selected.includes(photos[tile.index].uid) }"
            :aria-label="label(photos[tile.index])"
            :aria-pressed="selected.includes(photos[tile.index].uid)"
            :title="hints?.[photos[tile.index].uid]"
            @click="open(photos[tile.index], $event)"
          >
            <img
              v-if="photos[tile.index].hash"
              :src="thumbUrl(photos[tile.index].hash, 'fit_720')"
              :alt="label(photos[tile.index])"
              loading="lazy"
              decoding="async"
              draggable="false"
            />
            <span v-else class="noimg"><Icon name="image" :size="24" /></span>
            <span class="badges">
              <span v-if="photos[tile.index].type === 'video'" class="badge"><Icon name="play" :size="12" />{{ formatDuration(photos[tile.index].duration) }}</span>
              <span v-else-if="photos[tile.index].type === 'live'" class="badge">LIVE</span>
              <span v-else-if="photos[tile.index].type === 'animated'" class="badge">GIF</span>
            </span>
            <span class="marks">
              <Icon v-if="photos[tile.index].favorite" name="heart" :size="14" />
              <Icon v-if="photos[tile.index].private" name="lock" :size="14" />
            </span>
          </button>
          <button
            type="button"
            class="pick"
            :class="{ on: selected.includes(photos[tile.index].uid), always: hasSelection }"
            :aria-label="selected.includes(photos[tile.index].uid) ? 'Deseleziona' : 'Seleziona'"
            @click.stop="toggleSelected(photos[tile.index].uid)"
          >
            <Icon name="check" :size="14" />
          </button>
        </div>
      </div>

      <div v-if="loading && photos.length === 0" class="skeletons" aria-hidden="true">
        <div v-for="n in 3" :key="n" class="line" :style="{ height: `${TARGET}px` }">
          <div v-for="k in 4" :key="k" class="skeleton cell" :style="{ flex: 1 }" />
        </div>
      </div>

      <div v-if="!loading && photos.length === 0 && !failure" class="empty">
        <Icon name="images" :size="28" />
        <h3>{{ emptyTitle }}</h3>
        <p>{{ emptyText }}</p>
        <slot name="empty" />
      </div>
    </template>

    <div ref="sentinel" class="sentinel" aria-hidden="true" />
    <p v-if="loading && photos.length > 0" class="more muted" role="status">Carico altre foto…</p>
  </div>
</template>

<style scoped>
.grid { display: flex; flex-direction: column; gap: 4px; min-height: 120px; }
.line { display: flex; gap: 4px; }
.cell { position: relative; flex: none; overflow: hidden; border-radius: var(--radius-control); background: var(--stage-cell); }
.tile {
  position: relative; display: block; width: 100%; height: 100%; padding: 0; border: 0; border-radius: inherit; background: none; cursor: pointer;
  transition: box-shadow var(--dur-fast) var(--ease-out);
}
.tile:hover { box-shadow: var(--shadow-3); }
.tile.picked { box-shadow: inset 0 0 0 3px var(--accent); }
.tile img { display: block; width: 100%; height: 100%; object-fit: cover; }
.noimg { display: grid; place-items: center; width: 100%; height: 100%; color: var(--fg-subtle); }
.badges { position: absolute; left: var(--space-1-5); bottom: var(--space-1-5); display: flex; gap: var(--space-1); }
.badge {
  display: inline-flex; align-items: center; gap: var(--space-1); padding: 1px var(--space-1-5); border-radius: var(--radius-chip);
  background: var(--overlay-scrim); color: var(--overlay-fg); font-size: var(--text-2xs); font-weight: var(--weight-semibold);
  letter-spacing: 0.04em; backdrop-filter: blur(4px);
}
.marks { position: absolute; right: var(--space-1-5); bottom: var(--space-1-5); display: flex; gap: var(--space-1); color: var(--overlay-fg); filter: drop-shadow(0 1px 2px var(--overlay-shadow)); }
.pick {
  position: absolute; top: var(--space-1-5); left: var(--space-1-5); display: grid; place-items: center; width: var(--space-6); height: var(--space-6);
  padding: 0; border: 1.5px solid var(--overlay-fg); border-radius: var(--radius-pill); background: var(--overlay-scrim); color: transparent; cursor: pointer;
  opacity: 0; transition: opacity var(--dur-fast) var(--ease-out), background-color var(--dur-fast) var(--ease-out);
}
.cell:hover .pick, .pick:focus-visible, .pick.always { opacity: 1; }
.pick.on { background: var(--accent); border-color: var(--accent); color: var(--fg-on-accent); opacity: 1; }
@media (hover: none) { .pick { opacity: 1; } }
.skeletons { display: flex; flex-direction: column; gap: 4px; }
.sentinel { height: 1px; }
.more { padding: var(--space-3) 0; text-align: center; font-size: var(--text-sm); }
</style>
