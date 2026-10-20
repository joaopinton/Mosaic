<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute } from 'vue-router'
import { formatCount } from '../lib/format'
import PageFrame from '../components/PageFrame.vue'
import PhotoGrid from '../components/PhotoGrid.vue'

// Pagina «solo una query»: Esplora, Preferiti, Video, Live, Da rivedere, Privato, Archivio, Casuali.
const route = useRoute()
const total = ref<number | null>(null)
const meta = computed(() => route.meta as { title: string; query: string; order?: string; emptyTitle?: string; emptyText?: string })
const subtitle = computed(() => (total.value === null ? '' : `${formatCount(total.value)} ${total.value === 1 ? 'elemento' : 'elementi'}`))
</script>

<template>
  <PageFrame :title="meta.title" :subtitle="subtitle" width="full">
    <PhotoGrid :query="meta.query" :order="meta.order" :empty-title="meta.emptyTitle" :empty-text="meta.emptyText" @loaded="(n) => (total = n)">
      <template #empty>
        <RouterLink v-if="route.path === '/'" to="/library" class="btn primary">Apri la Libreria</RouterLink>
      </template>
    </PhotoGrid>
  </PageFrame>
</template>
