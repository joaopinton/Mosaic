<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import Icon from '../components/Icon.vue'
import PageFrame from '../components/PageFrame.vue'
import PhotoGrid from '../components/PhotoGrid.vue'
import { formatCount } from '../lib/format'
import { chipLabel, parseQuery, removeChip } from '../lib/query'
import { pushHistory } from '../lib/store'

// Ricerca: testo libero (semantico + parole) e filtri `chiave:valore` mostrati come chip rimovibili.
const route = useRoute()
const router = useRouter()
const q = computed(() => (typeof route.query.q === 'string' ? route.query.q : ''))
const input = ref(q.value)
const total = ref<number | null>(null)
const semantic = ref(false)
const parsed = computed(() => parseQuery(q.value))
const EXAMPLES = ['moto rossa sul palco', 'label:moto year:2024', 'person:anna color:blu', 'type:video -favorite:true', 'iso:800+ camera:nikon']

watch(q, (value) => (input.value = value))

function submit(next = input.value): void {
  const text = next.trim()
  if (!text) return
  pushHistory(text)
  void router.push({ path: '/search', query: { q: text } })
}

function drop(chip: ReturnType<typeof parseQuery>['chips'][number]): void {
  submit(removeChip(q.value, chip))
}
</script>

<template>
  <PageFrame title="Ricerca" :subtitle="total === null ? '' : `${formatCount(total)} risultati${semantic ? ' · per contenuto' : ''}`" width="full">
    <form class="bar" role="search" @submit.prevent="submit()">
      <Icon name="search" />
      <input v-model="input" class="field-input" type="search" placeholder="Cerca per contenuto o con filtri…" aria-label="Cerca nella libreria" autocomplete="off" />
      <button type="submit" class="btn primary">Cerca</button>
    </form>
    <div v-if="parsed.chips.length" class="chips" aria-label="Filtri attivi">
      <span v-for="c in parsed.chips" :key="c.raw" class="chip accent">
        {{ chipLabel(c) }}<button type="button" :aria-label="`Togli il filtro ${c.key}`" @click="drop(c)"><Icon name="x" :size="12" /></button>
      </span>
    </div>

    <div v-if="!q" class="empty">
      <Icon name="search" :size="28" />
      <h3>Cosa stai cercando?</h3>
      <p>Descrivi la foto con parole tue, anche in italiano, oppure combina i filtri. Prova con:</p>
      <ul class="examples">
        <li v-for="e in EXAMPLES" :key="e"><button type="button" class="chip" @click="submit(e)">{{ e }}</button></li>
      </ul>
    </div>
    <PhotoGrid v-else :query="q" empty-title="Nessun risultato" empty-text="Prova con meno filtri o con parole diverse." @loaded="(n, s) => { total = n; semantic = s }" />
  </PageFrame>
</template>

<style scoped>
.bar { display: flex; align-items: center; gap: var(--space-3); margin-bottom: var(--space-3); padding: var(--space-1-5) var(--space-1-5) var(--space-1-5) var(--space-4); border: 1px solid var(--border-strong); border-radius: var(--radius-panel); background: var(--bg-field-white); color: var(--fg-on-white); color-scheme: light; }
.bar:focus-within { border-color: var(--border-focus); box-shadow: 0 0 0 1px var(--border-focus); }
.field-input { flex: 1; min-width: 0; height: var(--control-h-lg); border: 0; background: transparent; color: var(--fg-on-white); font-size: var(--text-md); outline: none; }
.chips { display: flex; flex-wrap: wrap; gap: var(--space-1-5); margin-bottom: var(--space-3); }
.examples { display: flex; flex-wrap: wrap; justify-content: center; gap: var(--space-2); margin: var(--space-2) 0 0; padding: 0; list-style: none; }
.examples .chip { cursor: pointer; font-family: var(--font-mono); }
</style>
