<script setup lang="ts">
import { computed, ref } from 'vue'
import { allUids, api } from '../lib/api'
import { hasSelection, refreshJobs, selected } from '../lib/store'
import { errorMessage, toast } from '../lib/toast'
import { pushUndo } from '../lib/undo'
import type { Album } from '../lib/types'
import Dialog from './Dialog.vue'
import Dropdown from './Dropdown.vue'
import ExportDialog from './ExportDialog.vue'
import Icon from './Icon.vue'

// Menu «⋯» di album, selezioni, cartelle e momenti (D-32): esporta, apri la cartella, rinomina, rimuovi.
// I dialoghi si montano al primo uso: in Cartelle le schede possono essere centinaia.
const props = withDefaults(defineProps<{ album: Album; btnClass?: string }>(), { btnClass: 'btn ghost icon sm' })
const emit = defineEmits<{ changed: [album: Album]; removed: [] }>()

const SECTION: Record<string, string> = { folder: 'Cartelle', moment: 'Momenti', month: 'Calendario' }
const NOUN: Record<string, string> = { folder: 'la cartella', moment: 'il momento', month: 'il mese' }

const used = ref(false)
const renaming = ref(false)
const removing = ref(false)
const exporting = ref(false)
const busy = ref(false)
const newTitle = ref('')
const exportUids = ref<string[]>([])

const manual = computed(() => props.album.type === 'album')
const noun = computed(() => {
  if (manual.value) return props.album.source ? 'la selezione' : "l'album"
  return NOUN[props.album.type] ?? "l'album"
})
const section = computed(() => SECTION[props.album.type] ?? 'Album')

function openDialog(which: typeof renaming): void {
  used.value = true
  which.value = true
}

/** Con una selezione attiva si esporta quella; altrimenti l'album intero (avvisando se qualche foto non è arrivata). */
async function exportAll(): Promise<void> {
  try {
    if (hasSelection.value) {
      exportUids.value = [...selected.value]
    } else {
      exportUids.value = await allUids(`album:${props.album.uid}`)
      if (exportUids.value.length < props.album.photo_count) {
        toast(`Attenzione: ne esporto ${exportUids.value.length} su ${props.album.photo_count}. Le altre non sono state caricate.`, 'error')
      }
    }
    openDialog(exporting)
  } catch (error) {
    toast(errorMessage(error), 'error')
  }
}

function askRemove(): void {
  openDialog(removing)
}

function askRename(): void {
  newTitle.value = props.album.title
  openDialog(renaming)
}

async function rename(): Promise<void> {
  const title = newTitle.value.trim()
  if (!title || busy.value) return
  busy.value = true
  try {
    const uid = props.album.uid
    const before = props.album.title
    emit('changed', await api.patchAlbum(uid, { title }))
    pushUndo({
      label: `Nome «${before}»`,
      undo: async () => emit('changed', await api.patchAlbum(uid, { title: before })),
      redo: async () => emit('changed', await api.patchAlbum(uid, { title })),
    })
    renaming.value = false
  } catch (error) {
    toast(errorMessage(error), 'error')
  } finally {
    busy.value = false
  }
}

async function restoreTitle(): Promise<void> {
  try {
    const updated = await api.restoreAlbumTitle(props.album.uid)
    emit('changed', updated)
    toast(`Di nuovo «${updated.title}»`)
  } catch (error) {
    toast(errorMessage(error), 'error')
  }
}

async function setHidden(hidden: boolean): Promise<void> {
  try {
    const updated = await api.patchAlbum(props.album.uid, { private: hidden })
    emit('changed', updated)
    removing.value = false
    if (hidden) toast(`«${updated.title}» non compare più in ${section.value}`, 'default', { label: 'Annulla', run: () => void setHidden(false) })
  } catch (error) {
    toast(errorMessage(error), 'error')
  }
}

async function remove(): Promise<void> {
  if (busy.value) return
  busy.value = true
  try {
    await api.deleteAlbum(props.album.uid)
    removing.value = false
    toast(`«${props.album.title}» non c'è più: le foto restano nella libreria`)
    emit('removed')
  } catch (error) {
    toast(errorMessage(error), 'error')
  } finally {
    busy.value = false
  }
}

async function unlink(): Promise<void> {
  const root = props.album.root
  if (!root || busy.value) return
  busy.value = true
  try {
    await api.unlinkRoot(root)
    removing.value = false
    toast(`Scollego «${props.album.title}»: le foto escono da Mosaic, i file restano sul disco`)
    void refreshJobs()
    emit('removed')
  } catch (error) {
    toast(errorMessage(error), 'error')
  } finally {
    busy.value = false
  }
}

async function reveal(): Promise<void> {
  try {
    await api.revealAlbum(props.album.uid)
  } catch (error) {
    toast(errorMessage(error), 'error')
  }
}

async function copyPath(): Promise<void> {
  try {
    await navigator.clipboard.writeText(props.album.path ?? '')
    toast('Percorso copiato')
  } catch {
    toast('Non riesco a copiare il percorso', 'error')
  }
}

defineExpose({ exportAll })
</script>

<template>
  <Dropdown :label="`Azioni su ${noun} «${album.title}»`" :btn-class="btnClass">
    <button type="button" class="menu-item" role="menuitem" data-test="am-export" :disabled="album.photo_count === 0" @click="exportAll">
      <Icon name="download" /> {{ hasSelection ? `Scarica / Esporta le ${selected.length} selezionate…` : 'Scarica / Esporta…' }}
    </button>
    <template v-if="album.path">
      <button type="button" class="menu-item" role="menuitem" data-test="am-reveal" @click="reveal"><Icon name="folderOpen" /> Apri nella cartella</button>
      <button type="button" class="menu-item" role="menuitem" @click="copyPath"><Icon name="copy" /> Copia il percorso</button>
    </template>
    <button type="button" class="menu-item" role="menuitem" data-test="am-rename" @click="askRename"><Icon name="edit" /> Rinomina</button>
    <button v-if="!manual && album.renamed" type="button" class="menu-item" role="menuitem" data-test="am-restore-title" @click="restoreTitle">
      <Icon name="undo" /> Ripristina il nome originale
    </button>
    <div class="menu-sep" />
    <button v-if="!manual && album.private" type="button" class="menu-item" role="menuitem" data-test="am-show" @click="setHidden(false)">
      <Icon name="eye" /> Mostra di nuovo in {{ section }}
    </button>
    <button v-else type="button" class="menu-item danger" role="menuitem" data-test="am-remove" @click="askRemove">
      <Icon name="trash" /> Rimuovi…
    </button>
  </Dropdown>

  <template v-if="used">
    <ExportDialog :open="exporting" :uids="exportUids" :title="album.title" @close="exporting = false" />

    <Dialog :open="renaming" :title="`Rinomina ${noun}`" size="sm" @close="renaming = false">
      <form class="field" @submit.prevent="rename">
        <label :for="`am-title-${album.uid}`">Nome</label>
        <input :id="`am-title-${album.uid}`" v-model="newTitle" class="input" maxlength="200" autocomplete="off" data-test="am-title" />
        <p v-if="album.type === 'folder'" class="hint">Cambia solo il nome in Mosaic: la cartella sul disco resta com'è. Il nome resta anche quando Mosaic aggiorna le cartelle e finisce nel backup.</p>
        <p v-else-if="!manual" class="hint">Il nome resta anche quando Mosaic aggiorna {{ section }}.</p>
      </form>
      <template #footer>
        <button type="button" class="btn" @click="renaming = false">Annulla</button>
        <button type="button" class="btn primary" data-test="am-rename-save" :disabled="!newTitle.trim() || busy" @click="rename">Salva</button>
      </template>
    </Dialog>

    <Dialog :open="removing" :title="`Rimuovere «${album.title}»?`" size="sm" @close="removing = false">
      <div class="remove">
        <template v-if="manual">
          <p>Elimino {{ noun }}. <strong>Le foto restano</strong> nella libreria e nelle loro cartelle sul disco: non si cancella nessun file.</p>
        </template>
        <template v-else>
          <p><strong>Nascondi</strong>: {{ noun }} sparisce da {{ section }}. Le foto restano in Esplora e nelle ricerche, i file non si toccano.
            La ritrovi con «Mostra le nascoste».</p>
          <p v-if="album.root" data-test="am-unlink-hint"><strong>Scollega dalla libreria</strong>: Mosaic smette di guardare questa cartella e le sue foto
            escono dal catalogo. I file in <span class="mono">{{ album.path }}</span> restano dove sono; titoli, persone e album fatti a mano
            tornano se la ricolleghi.</p>
        </template>
      </div>
      <template #footer>
        <button type="button" class="btn" @click="removing = false">Annulla</button>
        <button v-if="manual" type="button" class="btn danger" data-test="am-delete" :disabled="busy" @click="remove">Elimina {{ noun }}</button>
        <template v-else>
          <button v-if="album.root" type="button" class="btn danger" data-test="am-unlink" :disabled="busy" @click="unlink"><Icon name="unlink" /> Scollega</button>
          <button type="button" class="btn primary" data-test="am-hide" @click="setHidden(true)"><Icon name="eyeOff" /> Nascondi</button>
        </template>
      </template>
    </Dialog>
  </template>
</template>

<style scoped>
.remove { display: flex; flex-direction: column; gap: var(--space-2); }
.remove .mono { word-break: break-all; }
</style>
