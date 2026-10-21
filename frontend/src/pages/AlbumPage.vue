<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import AlbumMenu from '../components/AlbumMenu.vue'
import Icon from '../components/Icon.vue'
import PageFrame from '../components/PageFrame.vue'
import PhotoGrid from '../components/PhotoGrid.vue'
import { api } from '../lib/api'
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
const story = ref('')
const savingStory = ref(false)
const regenerating = ref(false)
const menu = ref<InstanceType<typeof AlbumMenu> | null>(null)
// dove tornare dopo aver rimosso l'album: la sezione da cui viene
const LIST_OF: Record<string, string> = { album: '/albums', folder: '/folders', moment: '/moments', month: '/calendar' }

function setAlbum(updated: Album): void {
  album.value = updated
  currentAlbum.value = updated.type === 'album' ? { uid: updated.uid, title: updated.title } : null
}

function removed(): void {
  void router.push(LIST_OF[album.value?.type ?? 'album'] ?? '/albums')
}

async function load(): Promise<void> {
  missing.value = false
  try {
    const loaded = await api.album(String(route.params.uid))
    setAlbum(loaded)
    currentSection.value = SECTION_OF[loaded.type] ?? null
    story.value = loaded.description
  } catch {
    missing.value = true
    currentAlbum.value = null
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
  try {
    album.value = await api.patchAlbum(album.value.uid, { favorite: !album.value.favorite })
  } catch (error) {
    toast(errorMessage(error), 'error')
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
             :subtitle="`${formatCount(album.photo_count)} foto${album.smart ? ' · album automatico' : ''}${album.source ? ` · selezione di ${album.source.title}` : ''}${album.private ? ' · tra le nascoste' : ''}`">
    <template #actions>
      <button type="button" class="btn primary" data-test="curate" :disabled="album.photo_count === 0"
              :aria-expanded="sidebar.panel === 'curate'" @click="openCurate({ uid: album.uid, title: album.title })">
        <Icon name="wand" /> Crea selezione
      </button>
      <button type="button" class="btn" data-test="album-export" :disabled="album.photo_count === 0" @click="menu?.exportAll()"><Icon name="download" /> Esporta</button>
      <button type="button" class="btn icon" :class="{ active: album.favorite }" :aria-pressed="album.favorite" aria-label="Album preferito" @click="toggleFavorite"><Icon name="heart" /></button>
      <AlbumMenu ref="menu" :album="album" @changed="setAlbum" @removed="removed" />
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
    <PhotoGrid :query="`album:${album.uid}`" empty-title="Album vuoto" empty-text="Seleziona alcune foto in Esplora e aggiungile a questo album." />
  </PageFrame>
  <PageFrame v-else title="Album" />
</template>

<style scoped>
.story { display: flex; flex-direction: column; gap: var(--space-2); margin-bottom: var(--space-4); }
.story h2 { font-size: var(--text-lg); }
.story-actions { display: flex; justify-content: flex-end; gap: var(--space-2); }
</style>
