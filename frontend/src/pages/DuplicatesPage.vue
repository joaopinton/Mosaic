<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import Icon from '../components/Icon.vue'
import PageFrame from '../components/PageFrame.vue'
import Segmented from '../components/Segmented.vue'
import { api, thumbUrl } from '../lib/api'
import { formatBytes } from '../lib/format'
import { errorMessage, toast } from '../lib/toast'
import type { DuplicateExact, DuplicateNear } from '../lib/types'

const route = useRoute()
const router = useRouter()
const kind = ref<'exact' | 'near'>('exact')
const exact = ref<DuplicateExact[]>([])
const near = ref<DuplicateNear[]>([])
const loading = ref(true)

async function load(): Promise<void> {
  loading.value = true
  try {
    const data = await api.duplicates(kind.value)
    if (kind.value === 'exact') exact.value = data.groups as DuplicateExact[]
    else near.value = data.groups as DuplicateNear[]
  } catch (error) {
    toast(errorMessage(error), 'error')
  } finally {
    loading.value = false
  }
}

const open = (uid: string): void => void router.push({ query: { ...route.query, photo: uid } })

onMounted(load)
watch(kind, load)
</script>

<template>
  <PageFrame title="Duplicati" subtitle="Mosaic non cancella mai nulla da solo: qui vedi cosa è doppio e decidi tu.">
    <template #actions>
      <Segmented v-model="kind" label="Tipo di duplicati" :options="[{ value: 'exact', label: 'Identici' }, { value: 'near', label: 'Quasi identici' }]" />
    </template>

    <div v-if="loading" class="skeleton ph" aria-busy="true" />

    <template v-else-if="kind === 'exact'">
      <div v-if="!exact.length" class="empty"><Icon name="check" :size="28" /><h3>Nessun file doppio</h3><p>I file con lo stesso contenuto in cartelle diverse compaiono qui.</p></div>
      <ul v-else class="groups">
        <li v-for="g in exact" :key="g.photo.uid" class="group panel panel-pad">
          <button type="button" class="thumb" :aria-label="`Apri ${g.photo.title || 'la foto'}`" @click="open(g.photo.uid)"><img :src="thumbUrl(g.photo.hash, 'tile_224')" alt="" /></button>
          <div class="grow">
            <strong>{{ g.photo.title || 'Foto in libreria' }}</strong>
            <p class="subtle small">Copie con lo stesso contenuto:</p>
            <ul class="copies">
              <li v-for="c in g.copies" :key="c.path" class="mono small"><span class="truncate">{{ c.path }}</span><span class="subtle">{{ formatBytes(c.size) }}</span><span v-if="!c.exists" class="chip deviates">non esiste più</span></li>
            </ul>
          </div>
        </li>
      </ul>
    </template>

    <template v-else>
      <div v-if="!near.length" class="empty"><Icon name="check" :size="28" /><h3>Nessuna foto quasi identica</h3><p>Raffiche e scatti ripetuti compaiono qui dopo l'analisi AI.</p></div>
      <ul v-else class="groups">
        <li v-for="(g, i) in near" :key="i" class="group panel panel-pad">
          <div class="strip">
            <button v-for="p in g.photos" :key="p.uid" type="button" class="thumb" :aria-label="`Apri ${p.title || 'la foto'}`" @click="open(p.uid)"><img :src="thumbUrl(p.hash, 'tile_224')" alt="" loading="lazy" /></button>
          </div>
        </li>
      </ul>
    </template>
  </PageFrame>
</template>

<style scoped>
.ph { height: 8rem; }
.groups { display: grid; gap: var(--space-3); margin: 0; padding: 0; list-style: none; }
.group { display: flex; gap: var(--space-4); align-items: flex-start; }
.thumb { flex: none; width: 7rem; height: 7rem; padding: 0; border: 0; border-radius: var(--radius-control); overflow: hidden; background: var(--stage-cell); cursor: pointer; }
.thumb img { width: 100%; height: 100%; object-fit: cover; }
.strip { display: flex; flex-wrap: wrap; gap: var(--space-2); }
.copies { display: grid; gap: var(--space-1); margin: var(--space-1) 0 0; padding: 0; list-style: none; }
.copies li { display: flex; gap: var(--space-3); align-items: center; }
.small { font-size: var(--text-xs); }
</style>
