<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { carriesFiles, collectFiles, isDesktop, type PickedFile } from '../lib/drop'
import { errorMessage, toast } from '../lib/toast'
import DropDialog from './DropDialog.vue'
import Icon from './Icon.vue'

// Rilascio di file su tutta la finestra: velo con il bersaglio mentre trascini, poi il dialogo di conferma.
const PATHS_WAIT_MS = 1500

const over = ref(false)
const reading = ref(false)
const paths = ref<string[] | null>(null)
const files = ref<PickedFile[] | null>(null)
let depth = 0
let waiter: ((value: string[]) => void) | null = null

declare global {
  interface Window { mosaicDrop?: (paths: string[]) => void }
}

function onEnter(event: DragEvent): void {
  if (!carriesFiles(event)) return
  event.preventDefault()
  depth++
  over.value = true
}

function onOver(event: DragEvent): void {
  if (!carriesFiles(event)) return
  event.preventDefault()
  if (event.dataTransfer) event.dataTransfer.dropEffect = 'copy'
}

function onLeave(event: DragEvent): void {
  if (!carriesFiles(event)) return
  depth = Math.max(0, depth - 1)
  if (depth === 0) over.value = false
}

/** Nell'app desktop aspetta i percorsi veri da pywebview; se non arrivano, ripiega sul caricamento. */
function waitForPaths(): Promise<string[]> {
  return new Promise((resolve) => {
    const mine = (value: string[]): void => { clearTimeout(timer); if (waiter === mine) waiter = null; resolve(value) }
    const timer = setTimeout(() => mine([]), PATHS_WAIT_MS)
    waiter = mine // un rilascio più recente sostituisce questo: il suo timeout non lo cancella
  })
}

async function onDrop(event: DragEvent): Promise<void> {
  if (!carriesFiles(event)) return
  event.preventDefault()
  depth = 0
  over.value = false
  const transfer = event.dataTransfer
  reading.value = true
  try {
    // i file vanno letti subito: il DataTransfer si svuota appena l'evento finisce
    const picked = transfer ? collectFiles(transfer) : Promise.resolve([])
    picked.catch(() => undefined) // se arrivano i percorsi veri l'elenco dei file non serve: niente errori non gestiti
    const real = isDesktop() ? await waitForPaths() : []
    if (real.length) {
      paths.value = real
      return
    }
    const list = await picked
    if (!list.length) {
      toast('Niente da aggiungere: rilascia foto, video, cartelle o file .zip', 'error')
      return
    }
    files.value = list
  } catch (error) {
    toast(errorMessage(error), 'error')
  } finally {
    reading.value = false
  }
}

function receivePaths(value: string[]): void {
  if (waiter) waiter(value)
  else if (value.length) paths.value = value
}

function close(): void {
  paths.value = null
  files.value = null
}

onMounted(() => {
  window.mosaicDrop = receivePaths
  window.addEventListener('dragenter', onEnter)
  window.addEventListener('dragover', onOver)
  window.addEventListener('dragleave', onLeave)
  window.addEventListener('drop', onDrop)
})
onBeforeUnmount(() => {
  delete window.mosaicDrop
  window.removeEventListener('dragenter', onEnter)
  window.removeEventListener('dragover', onOver)
  window.removeEventListener('dragleave', onLeave)
  window.removeEventListener('drop', onDrop)
})
</script>

<template>
  <Transition name="veil">
    <div v-if="over || reading" class="veil" data-test="drop-veil" aria-live="polite">
      <div class="target" :class="{ reading }">
        <span class="glyph"><Icon :name="reading ? 'spinner' : 'upload'" :size="34" :class="{ spin: reading }" /></span>
        <strong>{{ reading ? 'Leggo quello che hai rilasciato…' : 'Rilascia per aggiungere a Mosaic' }}</strong>
        <span class="muted">Foto, video, cartelle intere o file .zip</span>
      </div>
    </div>
  </Transition>
  <DropDialog :paths="paths" :files="files" @close="close" />
</template>

<style scoped>
.veil {
  position: fixed; inset: 0; z-index: var(--z-overlay); display: grid; place-items: center; padding: var(--space-6);
  background: var(--drop-veil); backdrop-filter: blur(3px); pointer-events: none;
}
.target {
  display: flex; flex-direction: column; align-items: center; gap: var(--space-2); width: min(34rem, 100%); padding: var(--space-10) var(--space-6);
  border: 2px dashed var(--accent); border-radius: var(--radius-shell); background: var(--bg-raised); box-shadow: var(--shadow-4);
  text-align: center; animation: drop-pulse 1.6s var(--ease-in-out) infinite;
}
.target.reading { animation: none; border-style: solid; }
.target strong { font-size: var(--text-xl); }
.glyph { display: grid; place-items: center; width: 4.5rem; height: 4.5rem; margin-bottom: var(--space-2); border-radius: 50%; background: var(--accent-soft); color: var(--accent-soft-fg); }
@keyframes drop-pulse { 50% { transform: scale(1.02); } }
.veil-enter-active, .veil-leave-active { transition: opacity var(--dur-fast) var(--ease-out); }
.veil-enter-from, .veil-leave-to { opacity: 0; }
@media (prefers-reduced-motion: reduce) { .target { animation: none; } }
</style>
