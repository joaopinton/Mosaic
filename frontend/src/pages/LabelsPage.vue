<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import CoverCard from '../components/CoverCard.vue'
import Icon from '../components/Icon.vue'
import PageFrame from '../components/PageFrame.vue'
import { api } from '../lib/api'
import type { LabelInfo } from '../lib/types'

const labels = ref<LabelInfo[]>([])
const loading = ref(true)
const filter = ref('')

onMounted(async () => {
  try {
    labels.value = await api.labels()
  } finally {
    loading.value = false
  }
})

const groups = computed(() => {
  const needle = filter.value.trim().toLowerCase()
  const byCategory = new Map<string, LabelInfo[]>()
  for (const l of labels.value) {
    if (needle && !l.name.toLowerCase().includes(needle)) continue
    const key = l.category || 'Altre'
    byCategory.set(key, [...(byCategory.get(key) ?? []), l])
  }
  return [...byCategory.entries()].sort((a, b) => b[1].length - a[1].length).map(([category, items]) => ({ category, items }))
})
</script>

<template>
  <PageFrame title="Etichette" :subtitle="`${labels.length} etichette`">
    <template #actions>
      <input v-model="filter" class="input" type="search" placeholder="Filtra le etichette" aria-label="Filtra le etichette" />
    </template>
    <div v-if="loading" class="cards" aria-busy="true"><div v-for="n in 8" :key="n" class="skeleton ph" /></div>
    <div v-else-if="!labels.length" class="empty">
      <Icon name="tag" :size="28" />
      <h3>Nessuna etichetta</h3>
      <p>Le etichette arrivano dall'analisi AI delle foto, oppure le aggiungi tu dal dettaglio di una foto.</p>
    </div>
    <section v-for="g in groups" v-else :key="g.category" class="group" :aria-label="g.category">
      <h2 class="caps">{{ g.category }}</h2>
      <div class="cards">
        <CoverCard v-for="l in g.items" :key="l.slug" :to="{ path: '/search', query: { q: `label:${l.slug}` } }" :title="l.name" :count="l.count" :cover="l.cover" icon="tag" />
      </div>
    </section>
    <p v-if="!loading && labels.length && !groups.length" class="muted">Nessuna etichetta corrisponde a «{{ filter }}».</p>
  </PageFrame>
</template>

<style scoped>
.group { margin-bottom: var(--space-6); }
.group h2 { margin-bottom: var(--space-2); }
.cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(10rem, 1fr)); gap: var(--space-3); }
.ph { aspect-ratio: 4 / 3.6; border-radius: var(--radius-shell); }
</style>
