<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import AlbumMenu from '../components/AlbumMenu.vue'
import CoverCard from '../components/CoverCard.vue'
import Dialog from '../components/Dialog.vue'
import Icon from '../components/Icon.vue'
import PageFrame from '../components/PageFrame.vue'
import { api } from '../lib/api'
import { libraryVersion } from '../lib/store'
import { errorMessage, toast } from '../lib/toast'
import type { Album } from '../lib/types'

// Elenco di album: manuali, cartelle o momenti (il tipo sta nel meta della rotta).
// Ogni scheda ha il suo «⋯» (D-32); quelle nascoste si vedono a richiesta, in fondo.
const route = useRoute()
const router = useRouter()
const albums = ref<Album[]>([])
const loading = ref(true)
const creating = ref(false)
const title = ref('')
const filter = ref('')
const showHidden = ref(false)
const FILTER_FROM = 12

const matching = computed(() => {
  const needle = filter.value.trim().toLowerCase()
  return needle ? albums.value.filter((a) => a.title.toLowerCase().includes(needle) || (a.path ?? '').toLowerCase().includes(needle)) : albums.value
})
const visible = computed(() => matching.value.filter((a) => !a.private))
const hidden = computed(() => matching.value.filter((a) => a.private))
const shownCount = computed(() => albums.value.filter((a) => !a.private).length)
const icon = computed(() => (route.meta.type === 'folder' ? 'folder' : 'album'))

function subtitleOf(a: Album): string | undefined {
  return a.source ? `${a.photo_count} foto · da ${a.source.title}` : undefined
}

function replace(updated: Album): void {
  albums.value = albums.value.map((a) => (a.uid === updated.uid ? updated : a))
}

function drop(uid: string): void {
  albums.value = albums.value.filter((a) => a.uid !== uid)
}

async function load(): Promise<void> {
  loading.value = true
  try {
    albums.value = await api.albums(route.meta.type as string, true)
  } catch (error) {
    toast(errorMessage(error), 'error')
  } finally {
    loading.value = false
  }
}

async function create(): Promise<void> {
  const name = title.value.trim()
  if (!name) return
  try {
    const album = await api.createAlbum(name)
    creating.value = false
    title.value = ''
    void router.push(`/albums/${album.uid}`)
  } catch (error) {
    toast(errorMessage(error), 'error')
  }
}

onMounted(load)
watch(libraryVersion, load)
</script>

<template>
  <PageFrame :title="String(route.meta.title)" :subtitle="shownCount ? `${shownCount} ${shownCount === 1 ? 'raccolta' : 'raccolte'}` : ''">
    <template v-if="route.meta.type === 'album'" #actions>
      <button type="button" class="btn primary" @click="creating = true"><Icon name="plus" /> Nuovo album</button>
    </template>

    <div v-if="loading" class="cards" aria-busy="true"><div v-for="n in 6" :key="n" class="skeleton ph" /></div>
    <div v-else-if="!albums.length" class="empty">
      <Icon :name="icon" :size="28" />
      <h3>Ancora niente qui</h3>
      <p>{{ route.meta.empty }}</p>
    </div>
    <template v-else>
      <div v-if="albums.length > FILTER_FROM" class="find">
        <Icon name="search" :size="16" />
        <input v-model="filter" class="input" type="search" :placeholder="`Cerca in ${String(route.meta.title)}`"
               :aria-label="`Cerca in ${String(route.meta.title)}`" data-test="collections-filter" />
      </div>
      <p v-if="filter.trim() && !visible.length && !hidden.length" class="hint">Nessun risultato per «{{ filter.trim() }}».</p>
      <div class="cards">
        <CoverCard v-for="a in visible" :key="a.uid" :to="`/albums/${a.uid}`" :title="a.title" :count="a.photo_count" :cover="a.cover"
                   :subtitle="subtitleOf(a)" :icon="a.source ? 'wand' : icon">
          <template #menu><AlbumMenu :album="a" btn-class="btn icon sm" @changed="replace" @removed="drop(a.uid)" /></template>
        </CoverCard>
      </div>
      <section v-if="hidden.length" class="hidden-section">
        <button type="button" class="btn ghost sm" data-test="toggle-hidden" :aria-expanded="showHidden" @click="showHidden = !showHidden">
          <Icon :name="showHidden ? 'chevronUp' : 'chevronDown'" /> {{ showHidden ? 'Nascondi' : 'Mostra' }} le nascoste ({{ hidden.length }})
        </button>
        <div v-if="showHidden" class="cards">
          <CoverCard v-for="a in hidden" :key="a.uid" :to="`/albums/${a.uid}`" :title="a.title" :count="a.photo_count" :cover="a.cover"
                     :icon="icon" dimmed>
            <template #menu><AlbumMenu :album="a" btn-class="btn icon sm" @changed="replace" @removed="drop(a.uid)" /></template>
          </CoverCard>
        </div>
      </section>
    </template>

    <Dialog :open="creating" title="Nuovo album" size="sm" @close="creating = false">
      <form class="field" @submit.prevent="create">
        <label for="new-album">Titolo</label>
        <input id="new-album" v-model="title" class="input" maxlength="200" autocomplete="off" />
      </form>
      <template #footer>
        <button type="button" class="btn" @click="creating = false">Annulla</button>
        <button type="button" class="btn primary" :disabled="!title.trim()" @click="create">Crea</button>
      </template>
    </Dialog>
  </PageFrame>
</template>

<style scoped>
.cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(13rem, 1fr)); gap: var(--space-4); }
.find { display: flex; align-items: center; gap: var(--space-2); max-width: 24rem; margin-bottom: var(--space-4); color: var(--fg-subtle); }
.find .input { flex: 1; }
.hidden-section { display: flex; flex-direction: column; align-items: flex-start; gap: var(--space-3); margin-top: var(--space-6); }
.ph { aspect-ratio: 4 / 3.6; border-radius: var(--radius-shell); }
</style>
