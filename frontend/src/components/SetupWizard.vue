<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../lib/api'
import { refreshStatus, status } from '../lib/store'
import { errorMessage } from '../lib/toast'
import Dialog from './Dialog.vue'
import Icon from './Icon.vue'

// Primo avvio: cartella della libreria, scelte su rinomina e volti, poi si parte con l'indicizzazione.
// Le descrizioni con l'AI restano fuori: si attivano solo dalle Impostazioni.
const router = useRouter()
const dismissed = ref(false)
const step = ref(1)
const folder = ref('')
const error = ref('')
const busy = ref(false)
const renameAuto = ref(true)
const faces = ref(true)

const open = computed(() => !!status.value?.first_run && !dismissed.value)
const modelsMissing = computed(() => status.value && !status.value.ai_ready)

async function addFolder(): Promise<void> {
  const path = folder.value.trim()
  if (!path) return
  busy.value = true
  error.value = ''
  try {
    await api.putConfig({ library_roots: [path] })
    step.value = 3
  } catch (e) {
    error.value = errorMessage(e)
  } finally {
    busy.value = false
  }
}

async function finish(): Promise<void> {
  busy.value = true
  error.value = ''
  try {
    await api.putConfig({ rename: { auto: renameAuto.value }, ai: { faces: faces.value } })
    await api.index(false)
    await refreshStatus()
    dismissed.value = true
    void router.push('/library')
  } catch (e) {
    error.value = errorMessage(e)
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <Dialog :open="open" title="Benvenuto in Mosaic" size="md" @close="dismissed = true">
    <ol class="steps" aria-label="Passi">
      <li v-for="n in 3" :key="n" :class="{ on: n <= step }" :aria-current="n === step ? 'step' : undefined">{{ n }}</li>
    </ol>

    <section v-if="step === 1" class="stack">
      <p>Mosaic organizza le tue foto e i tuoi video <strong>in locale</strong>: niente cloud, niente account.</p>
      <ul class="points">
        <li><Icon name="search" /> Cerca per contenuto, anche in italiano: «moto rossa sul palco».</li>
        <li><Icon name="people" /> Riconosce i volti e raggruppa le persone.</li>
        <li><Icon name="wand" /> Se lo attivi nelle Impostazioni, descrive le foto con un modello AI che gira sul tuo PC.</li>
        <li><Icon name="folder" /> Non modifica i tuoi originali, tranne la rinomina che deciderai tu.</li>
      </ul>
      <p v-if="modelsMissing" class="chip to-verify">I modelli AI non sono ancora installati: esegui setup.ps1.</p>
    </section>

    <section v-else-if="step === 2" class="stack">
      <div class="field">
        <label for="wiz-folder">Dove sono le tue foto?</label>
        <input id="wiz-folder" v-model="folder" class="input" placeholder="D:\Foto" autocomplete="off" @keydown.enter.prevent="addFolder" />
        <p class="hint">Scrivi il percorso della cartella (le sottocartelle sono incluse). Potrai aggiungerne altre dalla Libreria.</p>
      </div>
      <p v-if="error" class="error-text" role="alert">{{ error }}</p>
    </section>

    <section v-else class="stack">
      <label class="option"><input v-model="renameAuto" type="checkbox" />
        <span><strong>Rinomina automatica dei file</strong>
          <span class="hint">Dopo l'analisi i file diventano «0001_titolo.jpg» (con RAW, XMP e video dello stesso scatto). Ogni rinomina è annullabile dalla Libreria.</span></span></label>
      <label class="option"><input v-model="faces" type="checkbox" />
        <span><strong>Riconoscimento dei volti</strong><span class="hint">Raggruppa le persone; i nomi li dai tu.</span></span></label>
      <p v-if="error" class="error-text" role="alert">{{ error }}</p>
    </section>

    <template #footer>
      <button v-if="step > 1" type="button" class="btn" :disabled="busy" @click="step--">Indietro</button>
      <button v-if="step === 1" type="button" class="btn primary" @click="step = 2">Inizia</button>
      <button v-else-if="step === 2" type="button" class="btn primary" :disabled="busy || !folder.trim()" @click="addFolder">Avanti</button>
      <button v-else type="button" class="btn primary" :disabled="busy" @click="finish">Avvia l'indicizzazione</button>
    </template>
  </Dialog>
</template>

<style scoped>
.steps { display: flex; gap: var(--space-2); margin: 0 0 var(--space-4); padding: 0; list-style: none; }
.steps li { display: grid; place-items: center; width: var(--space-6); height: var(--space-6); border-radius: var(--radius-pill); background: var(--bg-sunken); color: var(--fg-subtle); font-size: var(--text-xs); font-weight: var(--weight-semibold); }
.steps li.on { background: var(--accent); color: var(--fg-on-accent); }
.points { display: grid; gap: var(--space-2); margin: 0; padding: 0; list-style: none; font-size: var(--text-sm); }
.points li { display: flex; align-items: center; gap: var(--space-2-5); }
.option { display: flex; gap: var(--space-3); align-items: flex-start; font-size: var(--text-sm); cursor: pointer; }
.option span { display: grid; gap: var(--space-0-5); }
</style>
