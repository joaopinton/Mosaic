<script setup lang="ts">
import { onMounted, ref } from 'vue'
import CoverCard from '../components/CoverCard.vue'
import Icon from '../components/Icon.vue'
import PageFrame from '../components/PageFrame.vue'
import { api } from '../lib/api'
import type { CalendarYear } from '../lib/types'

const years = ref<CalendarYear[]>([])
const loading = ref(true)
onMounted(async () => {
  try {
    years.value = await api.calendar()
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <PageFrame title="Calendario">
    <div v-if="loading" class="cards" aria-busy="true"><div v-for="n in 6" :key="n" class="skeleton ph" /></div>
    <div v-else-if="!years.length" class="empty">
      <Icon name="calendar" :size="28" />
      <h3>Nessun mese ancora</h3>
      <p>I mesi compaiono quando ci sono abbastanza foto con una data affidabile.</p>
    </div>
    <section v-for="y in years" v-else :key="y.year" class="year" :aria-label="String(y.year)">
      <h2>{{ y.year }}</h2>
      <div class="cards">
        <CoverCard v-for="m in y.months" :key="m.album" :to="`/albums/${m.album}`" :title="m.title.split(' ')[0]" :count="m.count" :cover="m.cover" icon="calendar" />
      </div>
    </section>
  </PageFrame>
</template>

<style scoped>
.year { margin-bottom: var(--space-6); }
.year h2 { margin-bottom: var(--space-3); font-size: var(--text-xl); }
.cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(11rem, 1fr)); gap: var(--space-4); }
.ph { aspect-ratio: 4 / 3.6; border-radius: var(--radius-shell); }
</style>
