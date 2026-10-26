<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import Dialog from '../components/Dialog.vue'
import Icon from '../components/Icon.vue'
import JobsList from '../components/JobsList.vue'
import PageFrame from '../components/PageFrame.vue'
import { api } from '../lib/api'
import { formatCount } from '../lib/format'
import { libraryVersion, refreshJobs, refreshStatus, status } from '../lib/store'
import { errorMessage, toast } from '../lib/toast'
import type { ExcludedFile, LibraryRoot, LibraryRoots, RenameBatch, RenamePreview } from '../lib/types'

const newRoot = ref('')
const importSource = ref('')
const importMove = ref(false)
const pattern = ref('{seq:04}_{slug}')
const preview = ref<RenamePreview | null>(null)
const batches = ref<RenameBatch[]>([])
const purgeAsk = ref(false)
const rootInfo = ref<LibraryRoots>({ roots: [], unlinked: [] })
const unlinkAsk = ref<LibraryRoot | null>(null)
const excluded = ref<ExcludedFile[]>([])
const busy = ref(false)

const roots = computed(() => status.value?.library_roots ?? [])
// Cartelle configurate, più quelle rimaste nel catalogo senza essere più nella configurazione (da scollegare del tutto).
const folders = computed<LibraryRoot[]>(() => (rootInfo.value.roots.length ? rootInfo.value.roots
  : roots.value.map((path) => ({ path, linked: true, photos: 0 }))))
const ASK_PHOTOS = (n: number): string => `${formatCount(n)} foto`
const ASSET_LABELS: Record<string, string> = {
  siglip2: 'SigLIP 2 (ricerca per contenuto)', yunet: 'YuNet (volti)', sface: 'SFace (riconoscimento volti)',
  exiftool: 'exiftool (metadati)', ffmpeg: 'ffmpeg (video)', llama: 'llama.cpp (AI locale)', qwen25vl: 'Qwen2.5-VL (descrizioni)',
}

async function run<T>(action: () => Promise<T>, done: string): Promise<T | undefined> {
  if (busy.value) return undefined
  busy.value = true
  try {
    const result = await action()
    toast(done)
    void refreshJobs()
    void refreshStatus()
    return result
  } catch (error) {
    toast(errorMessage(error), 'error')
    return undefined
  } finally {
    busy.value = false
  }
}

async function addRoot(): Promise<void> {
  const path = newRoot.value.trim()
  if (!path) return
  const ok = await run(() => api.putConfig({ library_roots: [...roots.value, path] }), 'Cartella aggiunta')
  if (ok) {
    newRoot.value = ''
    await run(() => api.index(false), 'Indicizzazione avviata')
  }
}

/** La cartella è stata spostata o rinominata: si sceglie dov'è adesso e il catalogo (titoli, persone, album) la segue. */
const relinkPath = ref('')
const relinkFor = ref<LibraryRoot | null>(null)
const canPickFolder = ref(true)

async function askRelink(root: LibraryRoot): Promise<void> {
  relinkFor.value = root
  relinkPath.value = ''
  try {
    const { folder } = await api.pickFolder(root.path)
    if (folder) relinkPath.value = folder
  } catch {
    canPickFolder.value = false // fuori dall'app desktop: si scrive il percorso
  }
}

async function relinkMoved(): Promise<void> {
  const root = relinkFor.value
  const to = relinkPath.value.trim()
  if (!root || !to) return
  const ok = await run(() => api.relinkRoot(root.path, to), 'Cartella ricollegata: controllo le foto')
  if (ok) relinkFor.value = null
  await loadRoots()
}

async function unlinkRoot(): Promise<void> {
  const target = unlinkAsk.value
  unlinkAsk.value = null
  if (!target) return
  await run(() => api.unlinkRoot(target.path), 'Scollegamento avviato: i file restano dove sono')
  await loadRoots()
}

/** «Ricollega»: la cartella torna nella libreria e l'analisi riparte da zero (i dati manuali tornano dal backup). */
async function relink(path: string): Promise<void> {
  const ok = await run(() => api.putConfig({ library_roots: [...roots.value, path] }), 'Cartella ricollegata')
  if (ok) await run(() => api.index(false), 'Indicizzazione avviata')
  await loadRoots()
}

async function loadRoots(): Promise<void> {
  try {
    rootInfo.value = await api.libraryRoots()
  } catch {
    rootInfo.value = { roots: [], unlinked: [] }
  }
  try {
    excluded.value = await api.excluded()
  } catch {
    excluded.value = []
  }
}

const restoreExcluded = (sha1s?: string[]): Promise<unknown> =>
  run(() => api.restoreExcluded(sha1s), 'Foto rimesse nel catalogo: le cerco sul disco').then(loadRoots)

const reindex = (rescan: boolean): Promise<unknown> => run(() => api.index(rescan), rescan ? 'Rielaborazione avviata' : 'Indicizzazione avviata')
const startImport = (): Promise<unknown> => run(() => api.importFolder(importSource.value.trim(), importMove.value), 'Importazione avviata')
const analyzeNow = (): Promise<unknown> => run(() => api.analyze(), 'Analisi avviata')
const captionNow = (): Promise<unknown> => run(() => api.caption(false), 'Descrizioni in coda')
const rollbackBatch = (id: string): Promise<unknown> => run(() => api.renameRollback(id), 'Annullamento avviato')
const cleanup = (purge: boolean): Promise<unknown> => run(() => api.cleanup(purge), purge ? 'Pulizia avviata' : 'Controllo dei file avviato')

async function showPreview(): Promise<void> {
  const result = await run(() => api.renamePreview(pattern.value), 'Anteprima pronta')
  if (result) preview.value = result
}

async function applyRename(): Promise<void> {
  await run(() => api.renameApply(pattern.value), 'Rinomina avviata')
  preview.value = null
}

async function loadBatches(): Promise<void> {
  try {
    batches.value = await api.renameBatches()
  } catch {
    batches.value = []
  }
}

onMounted(async () => {
  void refreshStatus()
  void refreshJobs()
  await loadBatches()
  await loadRoots()
  try {
    pattern.value = (await api.config()).rename.pattern
  } catch {
    /* resta lo schema predefinito */
  }
})
watch(libraryVersion, () => { void loadBatches(); void loadRoots() })
</script>

<template>
  <PageFrame title="Libreria" subtitle="Cartelle, elaborazione, rinomina e manutenzione." width="reading">
    <section class="panel panel-pad block" aria-labelledby="h-roots">
      <h2 id="h-roots">Cartelle della libreria</h2>
      <ul v-if="folders.length" class="roots">
        <li v-for="r in folders" :key="r.path" data-test="root"><Icon name="folder" /><span class="mono grow truncate">{{ r.path }}</span>
          <span v-if="r.photos" class="subtle small">{{ ASK_PHOTOS(r.photos) }}</span>
          <span v-if="!r.linked" class="chip to-verify">non più collegata</span>
          <span v-if="r.exists === false" class="chip to-verify" data-test="root-missing">non trovata</span>
          <button v-if="r.exists === false" type="button" class="btn sm" data-test="root-relocate" :disabled="busy"
                  @click="askRelink(r)"><Icon name="folderOpen" /> Indica la nuova posizione…</button>
          <button type="button" class="btn ghost icon sm" data-test="root-unlink" :aria-label="`Scollega ${r.path} dalla libreria`"
                  :title="r.linked ? 'Scollega dalla libreria' : 'Togli dal catalogo'" :disabled="busy" @click="unlinkAsk = r"><Icon name="x" /></button></li>
      </ul>
      <p v-else class="muted">Nessuna cartella: aggiungine una per iniziare.</p>
      <ul v-if="rootInfo.unlinked.length" class="roots" aria-label="Cartelle scollegate">
        <li v-for="path in rootInfo.unlinked" :key="path" data-test="unlinked"><Icon name="folder" /><span class="mono grow truncate subtle">{{ path }}</span>
          <span class="chip">scollegata</span>
          <button type="button" class="btn sm" data-test="root-relink" :disabled="busy" @click="relink(path)"><Icon name="refresh" /> Ricollega</button></li>
      </ul>
      <form class="row" @submit.prevent="addRoot">
        <input v-model="newRoot" class="input grow" placeholder="D:\Foto" aria-label="Percorso della cartella" autocomplete="off" />
        <button type="submit" class="btn primary" :disabled="busy || !newRoot.trim()"><Icon name="plus" /> Aggiungi</button>
      </form>
      <div class="row">
        <button type="button" class="btn" :disabled="busy || !roots.length" @click="reindex(false)"><Icon name="refresh" /> Cerca nuove foto</button>
        <button type="button" class="btn" :disabled="busy || !roots.length" @click="reindex(true)">Rielabora tutto</button>
      </div>
      <p v-if="status" class="hint">{{ formatCount(status.photos) }} foto · {{ formatCount(status.analyzed) }} analizzate · {{ status.people }} persone · {{ status.albums }} album</p>
    </section>

    <section v-if="excluded.length" class="panel panel-pad block" aria-labelledby="h-excluded" data-test="excluded">
      <h2 id="h-excluded">Foto tolte dal catalogo</h2>
      <p class="hint">Questi file sono ancora sul disco ma Mosaic li ignora. Rimettendoli nel catalogo tornano con titoli e preferiti.</p>
      <ul class="roots">
        <li v-for="e in excluded" :key="e.sha1"><Icon name="image" /><span class="mono grow truncate">{{ e.path }}</span>
          <span v-if="!e.exists" class="chip to-verify">file non trovato</span>
          <button type="button" class="btn sm" data-test="excluded-restore" :disabled="busy" @click="restoreExcluded([e.sha1])">Rimetti</button></li>
      </ul>
      <div class="row"><button type="button" class="btn" data-test="excluded-restore-all" :disabled="busy" @click="restoreExcluded()">Rimetti tutte nel catalogo</button></div>
    </section>

    <section class="panel panel-pad block" aria-labelledby="h-jobs">
      <h2 id="h-jobs">Attività</h2>
      <JobsList />
    </section>

    <section class="panel panel-pad block" aria-labelledby="h-import">
      <h2 id="h-import">Importa da una cartella</h2>
      <p class="hint">Copia le foto nuove nella libreria, con nome per data (AAAA/MM/AAAAMMGG_HHMMSS). I duplicati vengono saltati.</p>
      <form class="row" @submit.prevent="startImport">
        <input v-model="importSource" class="input grow" placeholder="E:\DCIM" aria-label="Cartella da importare" autocomplete="off" />
        <label class="check"><input v-model="importMove" type="checkbox" /> Sposta</label>
        <button type="submit" class="btn primary" :disabled="busy || !importSource.trim() || !roots.length"><Icon name="upload" /> Importa</button>
      </form>
    </section>

    <section class="panel panel-pad block" aria-labelledby="h-ai">
      <h2 id="h-ai">Analisi e descrizioni</h2>
      <p class="hint">L'analisi AI parte da sola dopo l'indicizzazione. Qui puoi rilanciarla o chiedere le descrizioni.</p>
      <div class="row">
        <button type="button" class="btn" :disabled="busy || !status?.ai_ready" @click="analyzeNow"><Icon name="sparkle" /> Analizza le foto nuove</button>
        <button type="button" class="btn" :disabled="busy || !status?.llm_ready" @click="captionNow"><Icon name="wand" /> Descrivi con l'AI</button>
      </div>
      <p v-if="status && !status.ai_ready" class="chip to-verify">Modello AI non installato: esegui setup.ps1</p>
      <p v-if="status && !status.llm_ready" class="hint">L'AI locale per le descrizioni non è installata (<code>python -m mosaic.assets fetch --group llm</code>).</p>
    </section>

    <section class="panel panel-pad block" aria-labelledby="h-rename">
      <h2 id="h-rename">Rinomina dei file</h2>
      <p class="hint">Rinomina l'intero scatto insieme (JPEG, RAW, XMP, video). Ogni operazione si può annullare.
        Segnaposto: <code>{seq:04}</code> <code>{slug}</code> <code>{date:%Y%m%d}</code> <code>{year}</code> <code>{label}</code> <code>{name}</code></p>
      <div class="field"><label for="pattern">Schema</label><input id="pattern" v-model="pattern" class="input mono" autocomplete="off" /></div>
      <div class="row">
        <button type="button" class="btn" :disabled="busy" @click="showPreview">Anteprima</button>
        <span v-if="status" class="subtle small">Rinomina automatica: {{ status.rename_auto ? 'attiva' : 'disattivata' }}</span>
      </div>
      <h3 class="caps">Lotti eseguiti</h3>
      <p v-if="!batches.length" class="subtle small">Nessuna rinomina eseguita.</p>
      <table v-else class="table">
        <thead><tr><th>Lotto</th><th>Foto</th><th>File</th><th>Stato</th><th /></tr></thead>
        <tbody>
          <tr v-for="b in batches" :key="b.batch_id">
            <td class="mono">{{ b.batch_id }}</td><td>{{ b.photos }}</td><td>{{ b.files }}</td>
            <td><span v-if="b.done" class="chip match">{{ b.done }} fatti</span> <span v-if="b.rolled_back" class="chip">{{ b.rolled_back }} annullati</span> <span v-if="b.failed" class="chip deviates">{{ b.failed }} falliti</span></td>
            <td><button v-if="b.done" type="button" class="btn sm" :disabled="busy" @click="rollbackBatch(b.batch_id)"><Icon name="undo" /> Annulla</button></td>
          </tr>
        </tbody>
      </table>
    </section>

    <section class="panel panel-pad block" aria-labelledby="h-maint">
      <h2 id="h-maint">Manutenzione ed esportazione</h2>
      <div class="row">
        <button type="button" class="btn" :disabled="busy" @click="cleanup(false)">Controlla i file mancanti</button>
        <button type="button" class="btn danger" :disabled="busy" @click="purgeAsk = true">Elimina le righe dei file mancanti…</button>
      </div>
      <div class="row">
        <a class="btn" href="/api/export?format=json" download><Icon name="download" /> Catalogo JSON</a>
        <a class="btn" href="/api/export?format=csv" download><Icon name="download" /> Catalogo CSV</a>
      </div>
    </section>

    <section class="panel panel-pad block" aria-labelledby="h-assets">
      <h2 id="h-assets">Modelli e programmi</h2>
      <ul class="assets">
        <li v-for="(ok, key) in status?.assets ?? {}" :key="key"><Icon :name="ok ? 'check' : 'alert'" /> {{ ASSET_LABELS[key] ?? key }}
          <span class="chip" :class="ok ? 'match' : 'to-verify'">{{ ok ? 'installato' : 'manca' }}</span></li>
      </ul>
    </section>

    <Dialog :open="preview !== null" title="Anteprima della rinomina" size="lg" @close="preview = null">
      <template v-if="preview">
        <p>{{ preview.photos }} foto · {{ preview.files }} file<template v-if="preview.skipped"> · {{ preview.skipped }} saltate</template></p>
        <p v-if="!preview.ops.length" class="muted">Niente da rinominare.</p>
        <table v-else class="table">
          <thead><tr><th>Da</th><th>A</th></tr></thead>
          <tbody><tr v-for="(op, i) in preview.ops" :key="i"><td class="mono">{{ op.dir ? op.dir + '/' : '' }}{{ op.from }}</td><td class="mono">{{ op.to }}</td></tr></tbody>
        </table>
        <p v-if="preview.files > preview.ops.length" class="hint">Mostrate le prime {{ preview.ops.length }} operazioni.</p>
      </template>
      <template #footer>
        <button type="button" class="btn" @click="preview = null">Chiudi</button>
        <button type="button" class="btn primary" :disabled="!preview?.ops.length" @click="applyRename">Rinomina {{ preview?.files }} file</button>
      </template>
    </Dialog>

    <Dialog :open="relinkFor !== null" title="Dove si trova adesso la cartella?" size="sm" @close="relinkFor = null">
      <form v-if="relinkFor" class="field" @submit.prevent="relinkMoved">
        <p class="hint">Mosaic cercava le foto in <span class="mono">{{ relinkFor.path }}</span>, ma non c'è più. Scegli la nuova posizione della
          stessa cartella: titoli, persone, album e analisi restano e i percorsi si aggiornano da soli.</p>
        <label for="relink-path">Nuova cartella</label>
        <div class="row">
          <input id="relink-path" v-model="relinkPath" class="input mono grow" autocomplete="off" spellcheck="false" data-test="relink-path" />
          <button v-if="canPickFolder" type="button" class="btn" @click="askRelink(relinkFor)"><Icon name="folderOpen" /> Scegli…</button>
        </div>
      </form>
      <template #footer>
        <button type="button" class="btn" @click="relinkFor = null">Annulla</button>
        <button type="button" class="btn primary" data-test="relink-confirm" :disabled="!relinkPath.trim()" @click="relinkMoved">Ricollega</button>
      </template>
    </Dialog>

    <Dialog :open="unlinkAsk !== null" :title="unlinkAsk?.linked === false ? 'Togliere la cartella dal catalogo?' : 'Scollegare la cartella?'" size="sm" @close="unlinkAsk = null">
      <template v-if="unlinkAsk">
        <p data-test="unlink-text"><span class="mono">{{ unlinkAsk.path }}</span>: Mosaic toglie dal catalogo
          {{ unlinkAsk.photos ? `le ${ASK_PHOTOS(unlinkAsk.photos)}` : 'le foto' }} di questa cartella, con miniature, analisi, volti, didascalie e album automatici.</p>
        <p class="hint"><strong>I file sul disco non vengono toccati.</strong> Titoli, preferiti, nomi delle persone e album manuali restano nel backup:
          se ricolleghi la cartella tornano da soli, e l'analisi riparte da zero.</p>
      </template>
      <template #footer>
        <button type="button" class="btn" @click="unlinkAsk = null">Annulla</button>
        <button type="button" class="btn danger" data-test="unlink-confirm" @click="unlinkRoot">Scollega</button>
      </template>
    </Dialog>

    <Dialog :open="purgeAsk" title="Eliminare le righe dei file mancanti?" size="sm" @close="purgeAsk = false">
      <p>Vengono tolti dal database i file che non esistono più sul disco, con le foto rimaste senza file. I file veri non vengono toccati.</p>
      <template #footer>
        <button type="button" class="btn" @click="purgeAsk = false">Annulla</button>
        <button type="button" class="btn danger" @click="purgeAsk = false; cleanup(true)">Elimina</button>
      </template>
    </Dialog>
  </PageFrame>
</template>

<style scoped>
.block { display: flex; flex-direction: column; gap: var(--space-3); margin-bottom: var(--space-4); }
h2 { font-size: var(--text-lg); }
.roots { display: grid; gap: var(--space-1); margin: 0; padding: 0; list-style: none; }
.roots li { display: flex; align-items: center; gap: var(--space-2-5); font-size: var(--text-sm); }
.check { display: inline-flex; align-items: center; gap: var(--space-2); font-size: var(--text-sm); }
.small { font-size: var(--text-xs); }
code { font-family: var(--font-mono); font-size: var(--text-xs); padding: 0 var(--space-1); border-radius: var(--radius-chip); background: var(--bg-sunken); }
.table { width: 100%; border-collapse: collapse; font-size: var(--text-sm); }
.table th { padding: var(--space-1-5) var(--space-2); border-bottom: 1px solid var(--border-default); color: var(--fg-muted); font-weight: var(--weight-medium); text-align: left; }
.table td { padding: var(--space-1-5) var(--space-2); border-bottom: 1px solid var(--border-subtle); overflow-wrap: anywhere; }
.assets { display: grid; gap: var(--space-2); margin: 0; padding: 0; list-style: none; font-size: var(--text-sm); }
.assets li { display: flex; align-items: center; gap: var(--space-2-5); }
</style>
