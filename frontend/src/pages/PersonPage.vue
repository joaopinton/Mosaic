<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import Dialog from '../components/Dialog.vue'
import Dropdown from '../components/Dropdown.vue'
import Icon from '../components/Icon.vue'
import PageFrame from '../components/PageFrame.vue'
import PhotoGrid from '../components/PhotoGrid.vue'
import { api, cropUrl } from '../lib/api'
import { errorMessage, toast } from '../lib/toast'
import type { Subject } from '../lib/types'

const route = useRoute()
const router = useRouter()
const subjects = ref<Subject[]>([])
const renaming = ref(false)
const newName = ref('')
const uid = computed(() => String(route.params.uid))
const subject = computed(() => subjects.value.find((s) => s.uid === uid.value) ?? null)
const others = computed(() => subjects.value.filter((s) => s.uid !== uid.value))

async function load(): Promise<void> {
  subjects.value = await api.subjects()
  newName.value = subject.value?.name ?? ''
}

async function rename(): Promise<void> {
  if (!subject.value) return
  try {
    await api.patchSubject(subject.value.uid, { name: newName.value })
    renaming.value = false
    await load()
  } catch (error) {
    toast(errorMessage(error), 'error')
  }
}

async function toggleHidden(): Promise<void> {
  if (!subject.value) return
  await api.patchSubject(subject.value.uid, { hidden: !subject.value.hidden })
  await load()
}

async function mergeInto(target: Subject): Promise<void> {
  if (!subject.value) return
  try {
    await api.mergeSubjects(target.uid, subject.value.uid)
    toast(`Unita a «${target.name}»`)
    void router.push(`/people/${target.uid}`)
  } catch (error) {
    toast(errorMessage(error), 'error')
  }
}

onMounted(load)
watch(uid, load)
</script>

<template>
  <PageFrame v-if="subject" :title="subject.name" :subtitle="`${subject.photo_count} foto`" width="full">
    <template #actions>
      <img v-if="subject.marker" class="avatar" :src="cropUrl(subject.marker, 64)" alt="" width="36" height="36" />
      <Dropdown label="Azioni sulla persona">
        <button type="button" class="menu-item" role="menuitem" @click="renaming = true"><Icon name="edit" /> Rinomina</button>
        <button type="button" class="menu-item" role="menuitem" @click="toggleHidden"><Icon name="eye" /> {{ subject.hidden ? 'Mostra' : 'Nascondi' }}</button>
        <template v-if="others.length">
          <div class="menu-sep" />
          <span class="caps head">Unisci a</span>
          <button v-for="o in others.slice(0, 12)" :key="o.uid" type="button" class="menu-item" role="menuitem" @click="mergeInto(o)">{{ o.name }}</button>
        </template>
      </Dropdown>
    </template>
    <PhotoGrid :query="`person:${subject.slug}`" empty-title="Nessuna foto" empty-text="Questa persona non compare in nessuna foto visibile." />
    <Dialog :open="renaming" title="Rinomina la persona" size="sm" @close="renaming = false">
      <form class="field" @submit.prevent="rename"><label for="p-name">Nome</label><input id="p-name" v-model="newName" class="input" maxlength="200" /></form>
      <template #footer><button type="button" class="btn" @click="renaming = false">Annulla</button><button type="button" class="btn primary" @click="rename">Salva</button></template>
    </Dialog>
  </PageFrame>
  <PageFrame v-else title="Persona">
    <div class="empty"><h3>Persona non trovata</h3><RouterLink to="/people" class="btn">Torna alle persone</RouterLink></div>
  </PageFrame>
</template>

<style scoped>
.avatar { border-radius: var(--radius-pill); object-fit: cover; }
.head { display: block; padding: var(--space-1) var(--space-3); }
</style>
