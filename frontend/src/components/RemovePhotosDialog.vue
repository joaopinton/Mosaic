<script setup lang="ts">
import { computed, ref } from 'vue'
import { api } from '../lib/api'
import { libraryVersion, refreshStatus, status } from '../lib/store'
import { errorMessage, toast } from '../lib/toast'
import type { RemoveResult } from '../lib/types'
import Dialog from './Dialog.vue'

// Conferma di «Elimina»: il testo dice esattamente cosa succede, in base alla scelta fatta in Impostazioni (D-20).
const props = defineProps<{ open: boolean; uids: string[] }>()
const emit = defineEmits<{ close: []; done: [result: RemoveResult] }>()

const busy = ref(false)
const trash = computed(() => status.value?.delete_mode === 'trash')
const many = computed(() => props.uids.length !== 1)
const subject = computed(() => (many.value ? `${props.uids.length} foto` : 'questa foto'))

async function confirm(): Promise<void> {
  if (busy.value) return
  busy.value = true
  try {
    const result = await api.removePhotos(props.uids)
    if (result.removed) {
      const where = result.mode === 'trash' ? 'spostate nel Cestino' : 'tolte dal catalogo'
      toast(`${result.removed} foto ${where}`)
    }
    if (result.failed.length) {
      toast(`${result.failed.length} non eliminate (controlla anche il Cestino): ${result.failed[0].error}`, 'error')
    }
    libraryVersion.value++
    void refreshStatus()
    emit('done', result)
  } catch (error) {
    toast(errorMessage(error), 'error')
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <Dialog :open="open" :title="trash ? 'Spostare nel Cestino?' : 'Togliere dal catalogo?'" size="sm" @close="emit('close')">
    <template v-if="trash">
      <p data-test="remove-text">Sposto {{ subject }} nel Cestino di Windows: tutti i file dello scatto (JPEG, RAW, XMP, video) e la foto sparisce da Mosaic.</p>
      <p class="hint">Dal Cestino li puoi ripristinare: alla prossima ricerca di nuove foto Mosaic li ritrova, con titoli e preferiti. Se l'unità non ha un Cestino il file non viene toccato.</p>
    </template>
    <template v-else>
      <p data-test="remove-text">Tolgo {{ subject }} da Mosaic: spariscono da ricerca, album e persone.</p>
      <p class="hint">I file originali restano sul disco e non verranno più indicizzati. Da Libreria → «Foto tolte dal catalogo» puoi rimetterli.</p>
    </template>
    <template #footer>
      <button type="button" class="btn" :disabled="busy" @click="emit('close')">Annulla</button>
      <button type="button" class="btn danger" data-test="remove-confirm" :disabled="busy" @click="confirm">{{ trash ? 'Sposta nel Cestino' : 'Togli dal catalogo' }}</button>
    </template>
  </Dialog>
</template>
