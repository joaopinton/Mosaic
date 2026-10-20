<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import Dialog from '../components/Dialog.vue'
import Dropdown from '../components/Dropdown.vue'
import ExportDialog from '../components/ExportDialog.vue'
import Icon from '../components/Icon.vue'
import PageFrame from '../components/PageFrame.vue'
import PhotoGrid from '../components/PhotoGrid.vue'
import { allUids, api } from '../lib/api'
import { formatCount } from '../lib/format'
import { currentAlbum, currentSection, openCurate, sidebar } from '../lib/store'
import { SECTION_OF } from '../lib/nav'
import SavedWork from '../components/SavedWork.vue'
import { errorMessage, toast } from '../lib/toast'
import type { Album } from '../lib/types'

const route = useRoute()
const router = useRouter()
const album = ref<Album | null>(null)
const missing = ref(false)
const renaming = ref(false)
const deleting = ref(false)
const newTitle = ref('')
const story = ref('')
const savingStory = ref(false)
const regenerating = ref(false)
const exportUids = ref<string[]>([])
const exporting = ref(false)

async function askExport(): Promise<void> {
  if (!album.value) return
  try {
    exportUids.value = await allUids(`album:${album.value.uid}`)
    exporting.value = true
  } catch (error) {
    toast(errorMessage(error), 'error')
  }
}

async function load(): Promise<void> {
  missing.value = false
  try {
    album.value = await api.album(String(route.params.uid))
    newTitle.value = album.value.title
    currentAlbum.value = album.value.type === 'album' ? { uid: album.value.uid, title: album.value.title } : null
    currentSection.value = SECTION_OF[album.value.type] ?? null
    story.value = album.value.description
  } catch {
    missing.value = true
    currentAlbum.value = null
  }
}

async function rename(): Promise<void> {
  if (!album.value || !newTitle.value.trim()) return
  try {
    album.value = await api.patchAlbum(album.value.uid, { title: newTitle.value.trim() })
    renaming.value = false
  } catch (error) {
    toast(errorMessage(error), 'error')
  }
}

async function saveStory(): Promise<void> {
  if (!album.value) return
  savingStory.value = true
  try {
    album.value = await api.patchAlbum(album.value.uid, { description: story.value.trim() })
    story.value = album.value.description
    toast('Giornata salvata')
  } catch (error) {
    toast(errorMessage(error), 'error')
  } finally {
    savingStory.value = false
  }
}

async function regenerate(): Promise<void> {
  if (!album.value) return
  regenerating.value = true
  try {
    if (story.value.trim() !== album.value.description) await saveStory()
    const { photos } = await api.captionAlbum(album.value.uid)
    toast(`Rigenero le didascalie di ${formatCount(photos)} foto in background`)
  } catch (error) {
    toast(errorMessage(error), 'error')
  } finally {
    regenerating.value = false
  }
}

async function toggleFavorite(): Promise<void> {
  if (!album.value) return
  album.value = await api.patchAlbum(album.value.uid, { favorite: !album.value.favorite })
}

async function remove(): Promise<void> {
  if (!album.value) return
  try {
    await api.deleteAlbum(album.value.uid)
    toast('Album eliminato: le foto restano nella libreria')
    void router.push('/albums')
  } catch (error) {
    toast(errorMessage(error), 'error')
  } finally {
    deleting.value = false
  }
}

onMounted(load)
onBeforeUnmount(() => { currentAlbum.value = null; currentSection.value = null })
watch(() => route.params.uid, load)
</script>

<template>
  <PageFrame v-if="missing" title="Album non trovato">
    <div class="empty"><h3>Questo album non esiste più</h3><RouterLink to="/albums" class="btn">Torna agli album</RouterLink></div>
  </PageFrame>
  <PageFrame v-else-if="album" :title="album.title" width="full"
             :subtitle="`${formatCount(album.photo_count)} foto${album.smart ? ' · album automatico' : ''}${album.source ? ` · selezione di ${album.source.title}` : ''}`">
    <template #actions>
      <button type="button" class="btn primary" data-test="curate" :disabled="album.photo_count === 0"
              :aria-expanded="sidebar.panel === 'curate'" @click="openCurate({ uid: album.uid, title: album.title })">
        <Icon name="wand" /> Crea selezione
      </button>
      <button type="button" class="btn" data-test="album-export" :disabled="album.photo_count === 0" @click="askExport"><Icon name="download" /> Esporta</button>
      <button type="button" class="btn icon" :class="{ active: album.favorite }" :aria-pressed="album.favorite" aria-label="Album preferito" @click="toggleFavorite"><Icon name="heart" /></button>
      <Dropdown label="Azioni sull'album">
        <button type="button" class="menu-item" role="menuitem" @click="renaming = true"><Icon name="edit" /> Rinomina</button>
        <template v-if="album.type === 'album'">
          <div class="menu-sep" />
          <button type="button" class="menu-item danger" role="menuitem" @click="deleting = true"><Icon name="trash" /> Elimina l'album</button>
        </template>
      </Dropdown>
    </template>
    <section class="panel panel-pad story" aria-labelledby="story-title">
      <h2 id="story-title">Racconta la giornata</h2>
      <p class="hint">Dove eravate, chi c'era, cosa è successo. L'AI lo usa per descrivere le foto di questo album.</p>
      <textarea id="al-story" v-model="story" class="input" rows="4" maxlength="2000" aria-labelledby="story-title"
                placeholder="Per esempio: gita al lago di Garda con i nonni, pranzo sul molo e giro in barca." />
      <div class="story-actions">
        <button type="button" class="btn" :disabled="savingStory || story.trim() === album.description" @click="saveStory">Salva</button>
        <button type="button" class="btn primary" :disabled="regenerating" @click="regenerate">Rigenera le didascalie</button>
      </div>
    </section>
    <SavedWork :album="album" />
    <ExportDialog :open="exporting" :uids="exportUids" :title="album.title" @close="exporting = false" />
    <PhotoGrid :query="`album:${album.uid}`" empty-title="Album vuoto" empty-text="Seleziona alcune foto in Esplora e aggiungile a questo album." />

    <Dialog :open="renaming" title="Rinomina l'album" size="sm" @close="renaming = false">
      <form class="field" @submit.prevent="rename"><label for="al-title">Titolo</label><input id="al-title" v-model="newTitle" class="input" maxlength="200" /></form>
      <template #footer><button type="button" class="btn" @click="renaming = false">Annulla</button><button type="button" class="btn primary" @click="rename">Salva</button></template>
    </Dialog>
    <Dialog :open="deleting" title="Eliminare l'album?" size="sm" @close="deleting = false">
      <p>«{{ album.title }}» viene eliminato. Le foto restano nella libreria.</p>
      <template #footer><button type="button" class="btn" @click="deleting = false">Annulla</button><button type="button" class="btn danger" @click="remove">Elimina</button></template>
    </Dialog>
  </PageFrame>
  <PageFrame v-else title="Album" />
</template>

<style scoped>
.story { display: flex; flex-direction: column; gap: var(--space-2); margin-bottom: var(--space-4); }
.story h2 { font-size: var(--text-lg); }
.story-actions { display: flex; justify-content: flex-end; gap: var(--space-2); }
</style>
