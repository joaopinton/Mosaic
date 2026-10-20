<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import CoverCard from '../components/CoverCard.vue'
import Dialog from '../components/Dialog.vue'
import Icon from '../components/Icon.vue'
import PageFrame from '../components/PageFrame.vue'
import { api } from '../lib/api'
import { libraryVersion } from '../lib/store'
import { errorMessage, toast } from '../lib/toast'
import type { Album } from '../lib/types'

// Elenco di album: manuali, cartelle o momenti (il tipo sta nel meta della rotta).
const route = useRoute()
const router = useRouter()
const albums = ref<Album[]>([])
const loading = ref(true)
const creating = ref(false)
const title = ref('')

async function load(): Promise<void> {
  loading.value = true
  try {
    albums.value = await api.albums(route.meta.type as string)
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
  <PageFrame :title="String(route.meta.title)" :subtitle="albums.length ? `${albums.length} ${albums.length === 1 ? 'raccolta' : 'raccolte'}` : ''">
    <template v-if="route.meta.type === 'album'" #actions>
      <button type="button" class="btn primary" @click="creating = true"><Icon name="plus" /> Nuovo album</button>
    </template>

    <div v-if="loading" class="cards" aria-busy="true"><div v-for="n in 6" :key="n" class="skeleton ph" /></div>
    <div v-else-if="!albums.length" class="empty">
      <Icon :name="route.meta.type === 'folder' ? 'folder' : 'album'" :size="28" />
      <h3>Ancora niente qui</h3>
      <p>{{ route.meta.empty }}</p>
    </div>
    <div v-else class="cards">
      <CoverCard v-for="a in albums" :key="a.uid" :to="`/albums/${a.uid}`" :title="a.title" :count="a.photo_count" :cover="a.cover"
                 :icon="route.meta.type === 'folder' ? 'folder' : 'album'" />
    </div>

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
.ph { aspect-ratio: 4 / 3.6; border-radius: var(--radius-shell); }
</style>
