<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import Icon from '../components/Icon.vue'
import PageFrame from '../components/PageFrame.vue'
import Segmented from '../components/Segmented.vue'
import { api, cropUrl } from '../lib/api'
import { libraryVersion } from '../lib/store'
import { errorMessage, toast } from '../lib/toast'
import type { FaceGroup, Subject, SuggestionGroup } from '../lib/types'

const tab = ref<'people' | 'new' | 'confirm'>('people')
const subjects = ref<Subject[]>([])
const faces = ref<FaceGroup[]>([])
const suggestions = ref<SuggestionGroup[]>([])
const loading = ref(true)
const names = ref<Record<string, string>>({})

async function load(): Promise<void> {
  try {
    ;[subjects.value, faces.value, suggestions.value] = await Promise.all([api.subjects(), api.faces(), api.suggestions()])
  } catch (error) {
    toast(errorMessage(error), 'error')
  } finally {
    loading.value = false
  }
}

async function name(face: FaceGroup): Promise<void> {
  const value = (names.value[face.face] ?? '').trim()
  if (!value) return
  try {
    await api.nameFace(face.face, value)
    toast(`Salvato: ${value}`)
    names.value = { ...names.value, [face.face]: '' }
    libraryVersion.value++
    await load()
  } catch (error) {
    toast(errorMessage(error), 'error')
  }
}

const suggestionCount = computed(() => suggestions.value.reduce((total, g) => total + g.markers.length, 0))

async function confirm(group: SuggestionGroup, uids: string[]): Promise<void> {
  try {
    let confirmed = 1
    if (uids.length === 1) await api.confirmMarker(uids[0])
    else ({ confirmed } = await api.confirmSuggestions(uids))
    toast(confirmed === 1 ? `Confermato: ${group.name}` : `${confirmed} volti confermati: ${group.name}`)
    libraryVersion.value++ // il watch su libraryVersion ricarica l'elenco
  } catch (error) {
    toast(errorMessage(error), 'error')
  }
}

async function reject(group: SuggestionGroup, uid: string): Promise<void> {
  try {
    await api.rejectSuggestion(uid)
    toast(`Non è ${group.name}`)
    await load()
  } catch (error) {
    toast(errorMessage(error), 'error')
  }
}

onMounted(load)
watch(libraryVersion, load)
</script>

<template>
  <PageFrame title="Persone" :subtitle="`${subjects.length} con un nome · ${faces.length} gruppi da riconoscere · ${suggestionCount} da confermare`">
    <template #actions>
      <Segmented v-model="tab" label="Elenco" :options="[{ value: 'people', label: 'Persone' }, { value: 'new', label: `Da nominare (${faces.length})` }, { value: 'confirm', label: `Da confermare (${suggestionCount})` }]" />
    </template>

    <div v-if="loading" class="grid" aria-busy="true"><div v-for="n in 8" :key="n" class="skeleton ph" /></div>

    <template v-else-if="tab === 'people'">
      <div v-if="!subjects.length" class="empty">
        <Icon name="people" :size="28" />
        <h3>Nessuna persona con un nome</h3>
        <p>Dai un nome ai gruppi di volti nella scheda «Da nominare»: Mosaic riconoscerà le stesse persone nelle altre foto.</p>
        <button v-if="faces.length" type="button" class="btn primary" @click="tab = 'new'">Vai ai volti da nominare</button>
      </div>
      <ul v-else class="grid">
        <li v-for="s in subjects" :key="s.uid">
          <RouterLink :to="`/people/${s.uid}`" class="person" :class="{ hidden: s.hidden }">
            <img v-if="s.marker" :src="cropUrl(s.marker, 160)" :alt="s.name" loading="lazy" />
            <span v-else class="noimg"><Icon name="user" :size="28" /></span>
            <strong class="truncate">{{ s.name }}</strong>
            <span class="subtle small">{{ s.photo_count }} {{ s.photo_count === 1 ? 'foto' : 'foto' }}</span>
          </RouterLink>
        </li>
      </ul>
    </template>

    <template v-else-if="tab === 'confirm'">
      <div v-if="!suggestions.length" class="empty">
        <Icon name="check" :size="28" />
        <h3>Nessun volto da confermare</h3>
        <p>Quando Mosaic crede di riconoscere una persona che hai già nominato, ma non ne è sicuro, la propone qui.</p>
      </div>
      <template v-else>
        <section v-for="g in suggestions" :key="g.subject" class="group" :aria-label="`Volti che somigliano a ${g.name}`">
          <header class="group-head">
            <h3 class="truncate">{{ g.name }}</h3>
            <span class="subtle small">{{ g.markers.length }} {{ g.markers.length === 1 ? 'volto' : 'volti' }}</span>
            <button type="button" class="btn sm primary" @click="confirm(g, g.markers.map((m) => m.uid))">Conferma tutti</button>
          </header>
          <ul class="grid">
            <li v-for="m in g.markers" :key="m.uid" class="newface">
              <img :src="cropUrl(m.uid, 160)" :alt="`Volto simile a ${g.name}`" loading="lazy" />
              <div class="actions">
                <button type="button" class="btn sm icon" :aria-label="`È ${g.name}`" :title="`È ${g.name}`" @click="confirm(g, [m.uid])"><Icon name="check" /></button>
                <button type="button" class="btn sm icon ghost" :aria-label="`Non è ${g.name}`" :title="`Non è ${g.name}`" @click="reject(g, m.uid)"><Icon name="x" /></button>
              </div>
            </li>
          </ul>
        </section>
      </template>
    </template>

    <template v-else>
      <div v-if="!faces.length" class="empty">
        <Icon name="check" :size="28" />
        <h3>Tutti i gruppi hanno un nome</h3>
        <p>I nuovi volti compaiono qui dopo ogni analisi.</p>
      </div>
      <ul v-else class="grid">
        <li v-for="f in faces" :key="f.face" class="newface">
          <img :src="cropUrl(f.marker, 160)" alt="Volto da riconoscere" loading="lazy" />
          <span class="subtle small">{{ f.samples }} {{ f.samples === 1 ? 'foto' : 'foto' }}</span>
          <input v-model="names[f.face]" class="input" placeholder="Chi è?" :aria-label="`Nome del gruppo di ${f.samples} volti`" autocomplete="off" @keydown.enter.prevent="name(f)" />
          <button type="button" class="btn sm" :disabled="!(names[f.face] ?? '').trim()" @click="name(f)">Salva</button>
        </li>
      </ul>
    </template>
  </PageFrame>
</template>

<style scoped>
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(10rem, 1fr)); gap: var(--space-4); margin: 0; padding: 0; list-style: none; }
.ph { aspect-ratio: 1 / 1.25; border-radius: var(--radius-shell); }
.person, .newface {
  display: flex; flex-direction: column; align-items: center; gap: var(--space-1-5); padding: var(--space-4) var(--space-3); border: 1px solid var(--border-subtle);
  border-radius: var(--radius-shell); background: var(--bg-surface); box-shadow: var(--shadow-1); color: var(--fg-default); text-decoration: none; text-align: center;
  transition: border-color var(--dur-fast) var(--ease-out), box-shadow var(--dur-fast) var(--ease-out);
}
.person:hover { border-color: var(--border-strong); box-shadow: var(--shadow-2); }
.person.hidden { opacity: 0.55; }
.person img, .newface img, .noimg { width: 6.5rem; height: 6.5rem; border-radius: var(--radius-pill); object-fit: cover; background: var(--stage-cell); }
.noimg { display: grid; place-items: center; color: var(--fg-subtle); }
.person strong { max-width: 100%; }
.small { font-size: var(--text-xs); }
.newface .input { width: 100%; }
.group { margin-block-end: var(--space-6); }
.group-head { display: flex; align-items: center; gap: var(--space-3); margin-block-end: var(--space-3); }
.group-head h3 { margin: 0; font-size: var(--text-lg); }
.group-head .btn { margin-inline-start: auto; }
.actions { display: flex; gap: var(--space-2); }
</style>
