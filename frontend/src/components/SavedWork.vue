<script setup lang="ts">
import { ref, watch } from 'vue'
import { api, posterUrl, thumbUrl } from '../lib/api'
import { formatBytes, formatDate } from '../lib/format'
import { libraryVersion } from '../lib/store'
import { errorMessage, toast } from '../lib/toast'
import type { Album, PosterFile } from '../lib/types'
import Icon from './Icon.vue'

// «Selezioni e poster» di un album o di una cartella (D-27): quello che è nato da qui si ritrova qui.
const props = defineProps<{ album: Album }>()

const selections = ref<Album[]>([])
const posters = ref<PosterFile[]>([])
const folder = ref('')

async function load(): Promise<void> {
  const [s, p] = await Promise.allSettled([api.albumSelections(props.album.uid), api.albumPosters(props.album.uid)])
  selections.value = s.status === 'fulfilled' ? s.value : []
  if (p.status === 'fulfilled') {
    posters.value = p.value.posters
    folder.value = p.value.folder
  } else {
    posters.value = []
  }
}

watch(() => [props.album.uid, libraryVersion.value], () => void load(), { immediate: true })

async function openPoster(name: string): Promise<void> {
  try {
    await api.openPoster(props.album.uid, name)
  } catch (error) {
    toast(errorMessage(error), 'error')
  }
}

async function revealPoster(name: string): Promise<void> {
  try {
    await api.revealPoster(props.album.uid, name)
  } catch (error) {
    toast(errorMessage(error), 'error')
  }
}
</script>

<template>
  <section v-if="selections.length || posters.length" class="saved" aria-labelledby="saved-title">
    <header class="saved-head">
      <h2 id="saved-title">Selezioni e poster</h2>
      <span v-if="posters.length" class="hint mono truncate" :title="folder">{{ folder }}</span>
    </header>
    <ul class="cards">
      <li v-for="s in selections" :key="s.uid">
        <RouterLink :to="`/albums/${s.uid}`" class="card" data-test="saved-selection">
          <span class="thumb">
            <img v-if="s.cover" :src="thumbUrl(s.cover, 'tile_224')" alt="" loading="lazy" />
            <Icon v-else name="images" :size="20" />
            <span class="kind"><Icon name="wand" :size="12" /> Selezione</span>
          </span>
          <span class="label truncate">{{ s.title }}</span>
          <span class="meta">{{ s.photo_count }} foto</span>
        </RouterLink>
      </li>
      <li v-for="p in posters" :key="p.name">
        <div class="card poster" data-test="saved-poster">
          <a class="thumb" :href="posterUrl(album.uid, p.name)" target="_blank" rel="noopener" :title="p.name">
            <img :src="posterUrl(album.uid, p.name)" alt="" loading="lazy" />
            <span class="kind"><Icon name="grid" :size="12" /> Poster</span>
          </a>
          <span class="label truncate">{{ formatDate(p.created, true) }}</span>
          <span class="meta">{{ formatBytes(p.size) }}</span>
          <span class="tools">
            <button type="button" class="btn ghost sm" @click="openPoster(p.name)"><Icon name="external" /> Apri</button>
            <button type="button" class="btn ghost icon sm" aria-label="Mostra nella cartella" @click="revealPoster(p.name)"><Icon name="folderOpen" /></button>
          </span>
        </div>
      </li>
    </ul>
  </section>
</template>

<style scoped>
.saved { display: flex; flex-direction: column; gap: var(--space-3); margin-bottom: var(--space-4); }
.saved-head { display: flex; align-items: baseline; justify-content: space-between; gap: var(--space-3); min-width: 0; }
.saved-head h2 { flex: none; font-size: var(--text-lg); }
.cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(10rem, 1fr)); gap: var(--space-3); margin: 0; padding: 0; list-style: none; }
.card {
  display: flex; flex-direction: column; gap: var(--space-1); height: 100%; padding: var(--space-2); border: 1px solid var(--border-subtle);
  border-radius: var(--radius-panel); background: var(--bg-surface); color: inherit; text-decoration: none;
  transition: border-color var(--dur-fast) var(--ease-out), box-shadow var(--dur-fast) var(--ease-out);
}
.card:hover { border-color: var(--border-strong); box-shadow: var(--shadow-2); }
.thumb { position: relative; display: grid; place-items: center; aspect-ratio: 4 / 3; overflow: hidden; border-radius: var(--radius-control); background: var(--stage-cell); color: var(--fg-subtle); }
.thumb img { width: 100%; height: 100%; object-fit: cover; }
.poster .thumb img { object-fit: contain; }
.kind {
  position: absolute; left: var(--space-1-5); top: var(--space-1-5); display: inline-flex; align-items: center; gap: var(--space-1);
  padding: 1px var(--space-1-5); border-radius: var(--radius-chip); background: var(--overlay-scrim); color: var(--overlay-fg);
  font-size: var(--text-2xs); font-weight: var(--weight-semibold);
}
.label { font-size: var(--text-sm); font-weight: var(--weight-medium); }
.meta { color: var(--fg-muted); font-size: var(--text-xs); }
.tools { display: flex; align-items: center; gap: var(--space-1); margin-top: auto; }
</style>
