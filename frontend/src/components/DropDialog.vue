<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { api } from '../lib/api'
import { describeItem, summarize, uploadAll, type PickedFile } from '../lib/drop'
import { formatBytes, formatCount } from '../lib/format'
import { openActivity, refreshJobs, refreshStatus } from '../lib/store'
import { errorMessage, toast } from '../lib/toast'
import type { DropItem, DropKind } from '../lib/types'
import Dialog from './Dialog.vue'
import Icon from './Icon.vue'

// Conferma di cosa è stato rilasciato. Con i percorsi (app desktop) le cartelle si possono collegare dove sono;
// con i file caricati dal browser si copiano nella libreria.
const props = defineProps<{ paths: string[] | null; files: PickedFile[] | null }>()
const emit = defineEmits<{ close: [] }>()

const items = ref<DropItem[]>([])
const dest = ref<string | null>(null)
const loading = ref(false)
const busy = ref(false)
const folders = ref<'link' | 'copy'>('link')

const open = computed(() => props.paths !== null || props.files !== null)
const hasFolders = computed(() => items.value.some((i) => i.kind === 'folder' && !i.in_library))
const usable = computed(() => items.value.filter((i) => i.kind === 'folder' || i.kind === 'media' || i.kind === 'zip'))
const upload = computed(() => (props.files ? summarize(props.files) : null))
const needsDest = computed(() => !dest.value && (props.files !== null || usable.value.some((i) => i.kind !== 'folder' || folders.value === 'copy')))

const ICONS: Record<DropKind, string> = { folder: 'folder', media: 'image', zip: 'archive', unsupported: 'x', missing: 'alert' }

const described = computed(() => items.value.map((item) => ({ item, ...describeItem(item, folders.value) })))
const nestedZips = computed(() => items.value.some((i) => i.kind === 'folder' && i.zips > 0))

let generation = 0
watch(() => props.paths, async (paths) => {
  const mine = ++generation // due rilasci ravvicinati: vale solo la risposta dell'ultimo
  items.value = []
  if (!paths) return
  loading.value = true
  try {
    const preview = await api.dropInspect(paths)
    if (mine !== generation) return
    items.value = preview.items
    dest.value = preview.dest
  } catch (error) {
    if (mine !== generation) return
    toast(errorMessage(error), 'error')
    emit('close')
  } finally {
    if (mine === generation) loading.value = false
  }
}, { immediate: true })

watch(() => props.files, async (files) => {
  if (!files) return
  try {
    dest.value = (await api.status()).library_roots[0] ?? null
  } catch {
    dest.value = null
  }
}, { immediate: true })

async function confirm(): Promise<void> {
  busy.value = true
  try {
    if (props.files) {
      const files = props.files
      emit('close')
      openActivity()
      await uploadAll(files)
      toast(`${formatCount(files.length)} file caricati: li aggiungo alla libreria`)
    } else if (props.paths) {
      const result = await api.dropAdd(props.paths, folders.value)
      emit('close')
      openActivity()
      const linked = result.linked.length
      toast(linked ? `${linked === 1 ? 'Cartella collegata' : `${linked} cartelle collegate`}: le sto indicizzando` : 'Aggiungo i file alla libreria')
      void refreshStatus()
    }
    await refreshJobs()
  } catch (error) {
    toast(errorMessage(error), 'error')
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <Dialog :open="open" title="Aggiungi a Mosaic" size="lg" @close="emit('close')">
    <div class="drop-body">
      <p v-if="loading" class="muted row"><Icon name="spinner" class="spin" /> Guardo cosa hai rilasciato…</p>

      <template v-else-if="files && upload">
        <p class="lead">
          <strong>{{ formatCount(files.length) }} file</strong> · {{ formatBytes(upload.bytes) }}
          <template v-if="upload.folders"> da {{ upload.folders }} {{ upload.folders === 1 ? 'cartella' : 'cartelle' }}</template>
          <template v-if="upload.zips"> · {{ upload.zips }} zip da estrarre</template>
        </p>
        <p class="hint">Qui il browser non dice dove stanno i file sul computer: li carico e li copio nella libreria.
          Ogni cartella e ogni zip finiscono in una cartella con il loro nome, con le stesse sottocartelle (anche gli zip che
          contengono), e compaiono in «Cartelle»; foto e video sciolti vanno per data. Per collegare una cartella dov'è, usa l'app Mosaic.</p>
      </template>

      <template v-else>
        <ul class="items" aria-label="Elementi rilasciati">
          <li v-for="{ item, summary, plan } in described" :key="item.path" class="item" :data-kind="item.kind">
            <span class="kind"><Icon :name="ICONS[item.kind]" :size="16" /></span>
            <span class="what">
              <strong class="truncate" :title="item.path">{{ item.name }}</strong>
              <span class="path mono truncate" :title="item.path">{{ item.path }}</span>
              <span class="note">{{ summary }}</span>
              <span v-if="plan" class="plan" data-test="drop-plan">{{ plan }}</span>
            </span>
          </li>
        </ul>

        <fieldset v-if="hasFolders" class="choice">
          <legend class="caps">Le cartelle</legend>
          <label class="opt" :class="{ on: folders === 'link' }">
            <input v-model="folders" type="radio" value="link" name="drop-folders" />
            <span><strong>Collega dove sono</strong> <span class="tag">consigliato</span>
              <span class="hint">Nessuna copia: Mosaic legge le foto dalla cartella originale e la tiene d'occhio. Ogni sottocartella con foto
                diventa una cartella in «Cartelle».<template v-if="nestedZips"> Gli zip che contiene si estraggono accanto a sé, ognuno in una
                cartella con il suo nome: è l'unica cosa che Mosaic scrive lì.</template></span></span>
          </label>
          <label class="opt" :class="{ on: folders === 'copy' }">
            <input v-model="folders" type="radio" value="copy" name="drop-folders" />
            <span><strong>Copia nella libreria</strong>
              <span class="hint">La cartella si copia nella libreria con il suo nome e le stesse sottocartelle (gli zip che contiene
                si estraggono nella copia); la cartella originale resta com'è.</span></span>
          </label>
        </fieldset>
      </template>

      <p v-if="dest && !loading" class="hint dest"><Icon name="drive" :size="14" /> Le copie vanno in <span class="mono">{{ dest }}</span>: cartelle e zip con il loro nome, foto e video sciolti per anno e mese. Gli originali non si toccano e i doppioni si saltano.</p>
      <p v-if="needsDest && !loading" class="error-text" role="alert">Prima collega una cartella alla libreria (dalla pagina Libreria, o rilasciando una cartella): le copie vanno lì.</p>
    </div>
    <template #footer>
      <button type="button" class="btn" @click="emit('close')">Annulla</button>
      <button type="button" class="btn primary" data-test="drop-confirm" :disabled="busy || loading || needsDest || (!files && !usable.length)" @click="confirm">
        <Icon name="plus" /> {{ files ? 'Carica e aggiungi' : 'Aggiungi' }}
      </button>
    </template>
  </Dialog>
</template>

<style scoped>
.drop-body { display: flex; flex-direction: column; gap: var(--space-4); }
.lead { font-size: var(--text-md); }
.items { display: flex; flex-direction: column; gap: var(--space-2); max-height: 18rem; margin: 0; padding: 0; overflow-y: auto; list-style: none; }
.item { display: flex; align-items: flex-start; gap: var(--space-3); padding: var(--space-2) var(--space-3); border: 1px solid var(--border-subtle); border-radius: var(--radius-panel); }
.item[data-kind='unsupported'], .item[data-kind='missing'] { opacity: 0.6; }
.kind { display: grid; place-items: center; width: 2rem; height: 2rem; flex: none; border-radius: var(--radius-control); background: var(--accent-soft); color: var(--accent-soft-fg); }
.item[data-kind='unsupported'] .kind, .item[data-kind='missing'] .kind { background: var(--bg-sunken); color: var(--fg-subtle); }
.what { display: flex; flex-direction: column; gap: 2px; min-width: 0; font-size: var(--text-sm); }
.path { color: var(--fg-subtle); font-size: var(--text-xs); }
.note { color: var(--fg-muted); font-size: var(--text-xs); }
.plan { color: var(--fg-default); font-size: var(--text-xs); }
.choice { display: flex; flex-direction: column; gap: var(--space-2); margin: 0; padding: 0; border: 0; }
.choice legend { padding-bottom: var(--space-1); }
.opt { display: flex; align-items: flex-start; gap: var(--space-3); padding: var(--space-3); border: 1px solid var(--border-default); border-radius: var(--radius-panel); cursor: pointer;
  transition: border-color var(--dur-fast) var(--ease-out), background-color var(--dur-fast) var(--ease-out); }
.opt.on { border-color: var(--accent); background: var(--bg-selected); }
.opt input { margin-top: 3px; }
.opt > span { display: flex; flex-direction: column; gap: 2px; font-size: var(--text-sm); }
.tag { display: inline-block; margin-left: var(--space-1); padding: 0 var(--space-1-5); border-radius: var(--radius-chip); background: var(--accent-soft); color: var(--accent-soft-fg); font-size: var(--text-2xs); font-weight: var(--weight-semibold); vertical-align: middle; }
.dest { display: flex; align-items: center; gap: var(--space-1-5); flex-wrap: wrap; }
</style>
