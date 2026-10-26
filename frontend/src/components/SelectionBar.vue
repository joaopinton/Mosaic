<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api, type BatchActionName } from '../lib/api'
import { runBatchSafe } from '../lib/batch'
import { clearSelection, currentAlbum, hasSelection, libraryVersion, selected, viewer } from '../lib/store'
import { errorMessage, toast } from '../lib/toast'
import { pushUndo, undoAction } from '../lib/undo'
import type { Album } from '../lib/types'
import Dialog from './Dialog.vue'
import Dropdown from './Dropdown.vue'
import ExportDialog from './ExportDialog.vue'
import Icon from './Icon.vue'
import RemovePhotosDialog from './RemovePhotosDialog.vue'

// Barra delle azioni di gruppo: compare quando c'è almeno una foto selezionata.
const albums = ref<Album[]>([])
const newAlbum = ref(false)
const newTitle = ref('')
const busy = ref(false)
const removing = ref(false)
const exporting = ref(false)
const exportUids = ref<string[]>([])
const exportCrop = ref(false)
const exportMode = ref<'zip' | 'folder' | undefined>(undefined)

/** Titolo proposto per una nuova selezione: «Selezione del 20 ottobre». */
function defaultTitle(now = new Date()): string {
  return `Selezione del ${now.toLocaleDateString('it-IT', { day: 'numeric', month: 'long' })}`
}

function askNew(): void {
  newTitle.value = defaultTitle()
  newAlbum.value = true
}

/** Titolo della cartella o dello zip: l'album aperto, altrimenti «Selezione del 20 ottobre». */
const exportTitle = computed(() => (currentAlbum.value ? currentAlbum.value.title : defaultTitle()))

function askExport(mode?: 'zip' | 'folder', crop = false): void {
  exportUids.value = [...selected.value] // fotografia della selezione: resta valida anche se poi cambia
  exportCrop.value = crop // «Ritaglia…» apre lo stesso dialogo con il ritaglio a formato acceso (D-34)
  exportMode.value = mode
  exporting.value = true
}

/** Lo stato attuale delle foto selezionate che il server già conosce: se sono tutte preferite il pulsante le toglie. */
function allFlagged(flag: 'favorite' | 'private'): boolean {
  const known = new Map(viewer.photos.map((p) => [p.uid, p[flag]]))
  return selected.value.length > 0 && selected.value.every((uid) => known.get(uid) === true)
}
const allFavorite = computed(() => allFlagged('favorite'))
const allPrivate = computed(() => allFlagged('private'))

async function loadAlbums(): Promise<void> {
  try {
    albums.value = await api.albums('album')
  } catch {
    albums.value = []
  }
}
onMounted(loadAlbums)

async function run(action: BatchActionName, label: string, album?: string): Promise<void> {
  if (busy.value) return
  busy.value = true
  try {
    if (await runBatchSafe([...selected.value], action, label, album)) {
      clearSelection()
      void loadAlbums()
    }
  } finally {
    busy.value = false
  }
}

function afterRemove(): void {
  removing.value = false
  clearSelection()
  void loadAlbums()
}

async function createAndAdd(): Promise<void> {
  const title = newTitle.value.trim()
  if (!title) return
  const uids = [...selected.value]
  try {
    const created = await api.createAlbum(title, [...selected.value])
    let current = created.uid
    const entry = pushUndo({
      label: `«${title}» salvata (${created.photo_count} foto)`,
      undo: () => api.deleteAlbum(current),
      redo: async () => void (current = (await api.createAlbum(title, [...uids])).uid),
    })
    toast(`«${title}» salvata con ${created.photo_count} foto`, 'default', undoAction(entry), 8000)
    newAlbum.value = false
    newTitle.value = ''
    clearSelection()
    libraryVersion.value++
    void loadAlbums()
  } catch (error) {
    toast(errorMessage(error), 'error')
  }
}
</script>

<template>
  <Transition name="bar">
    <div v-if="hasSelection" class="bar" role="toolbar" aria-label="Azioni sulle foto selezionate">
      <span class="count">{{ selected.length }} selezionate</span>
      <button type="button" class="btn sm" :disabled="busy" data-test="bar-favorite"
              @click="allFavorite ? run('unfavorite', 'Tolte dai preferiti') : run('favorite', 'Aggiunte ai preferiti')">
        <Icon name="heart" /> {{ allFavorite ? 'Non preferite' : 'Preferite' }}</button>
      <button type="button" class="btn sm" :disabled="busy" data-test="bar-private"
              @click="allPrivate ? run('public', 'Rese di nuovo visibili') : run('private', 'Rese private')">
        <Icon name="lock" /> {{ allPrivate ? 'Non private' : 'Private' }}</button>
      <Dropdown label="Salva in un album o in una selezione" btn-class="btn sm" align="left" placement="up">
        <template #button><Icon name="album" /> Salva in…</template>
        <button type="button" class="menu-item" role="menuitem" data-test="bar-new-album" @click="askNew"><Icon name="plus" /> Nuova selezione o album…</button>
        <div v-if="albums.length" class="menu-sep" />
        <button v-for="a in albums" :key="a.uid" type="button" class="menu-item" role="menuitem" data-test="bar-album"
                @click="run('add_to_album', `Aggiunte a «${a.title}»`, a.uid)">
          <Icon :name="a.source ? 'wand' : 'album'" />
          <span class="truncate">{{ a.title }}</span>
          <span v-if="a.source" class="from truncate">da {{ a.source.title }}</span>
        </button>
      </Dropdown>
      <button type="button" class="btn sm" data-test="bar-download" @click="askExport('zip')"><Icon name="download" /> Scarica…</button>
      <button type="button" class="btn sm" data-test="bar-export" @click="askExport('folder')"><Icon name="folderOpen" /> Esporta…</button>
      <button type="button" class="btn sm" data-test="bar-crop" @click="askExport(undefined, true)"><Icon name="crop" /> Ritaglia…</button>
      <button type="button" class="btn sm" :disabled="busy" @click="run('archive', 'Archiviate')"><Icon name="archive" /> Archivia</button>
      <button type="button" class="btn ghost sm" :disabled="busy" @click="run('restore', 'Ripristinate dall\'archivio')">Ripristina</button>
      <button v-if="currentAlbum" type="button" class="btn sm" :disabled="busy" data-test="bar-out-of-album"
              @click="run('remove_from_album', 'Tolte da questo album', currentAlbum.uid)"><Icon name="x" /> Togli dall'album</button>
      <button type="button" class="btn danger sm" :disabled="busy" data-test="bar-remove" @click="removing = true"><Icon name="trash" /> Elimina…</button>
      <button type="button" class="btn ghost icon sm" aria-label="Deseleziona tutto" title="Deseleziona tutto (Esc)" @click="clearSelection"><Icon name="x" /></button>
    </div>
  </Transition>

  <RemovePhotosDialog :open="removing" :uids="selected" @close="removing = false" @done="afterRemove" />

  <ExportDialog :open="exporting" :uids="exportUids" :title="exportTitle" :crop="exportCrop" :mode="exportMode" @close="exporting = false" />

  <Dialog :open="newAlbum" title="Nuova selezione o album" size="sm" @close="newAlbum = false">
    <form class="field" @submit.prevent="createAndAdd">
      <label for="album-title">Titolo</label>
      <input id="album-title" v-model="newTitle" class="input" maxlength="200" autocomplete="off" />
      <p class="hint">Le {{ selected.length }} foto restano dove sono: la ritrovi tra gli album, da dove puoi anche esportarla in una cartella.</p>
    </form>
    <template #footer>
      <button type="button" class="btn" @click="newAlbum = false">Annulla</button>
      <button type="button" class="btn primary" :disabled="!newTitle.trim()" @click="createAndAdd">Salva</button>
    </template>
  </Dialog>
</template>

<style scoped>
.bar {
  position: fixed; z-index: var(--z-overlay); bottom: var(--space-6); left: 50%; display: flex; align-items: center; gap: var(--space-2);
  padding: var(--space-2) var(--space-3); border: 1px solid var(--border-default); border-radius: var(--radius-panel);
  background: var(--bg-raised); box-shadow: var(--shadow-4); transform: translateX(-50%);
}
.from { margin-left: auto; padding-left: var(--space-3); font-size: var(--text-xs); color: var(--fg-muted); }
.count { padding: 0 var(--space-2); font-size: var(--text-sm); font-weight: var(--weight-medium); }
.bar-enter-active { animation: toast-in var(--dur-slow) var(--ease-spring); }
.bar-leave-active { transition: opacity var(--dur-fast) var(--ease-out); }
.bar-leave-to { opacity: 0; }
@media (max-width: 40rem) { .bar { flex-wrap: wrap; width: calc(100vw - 2rem); justify-content: center; } }
</style>
