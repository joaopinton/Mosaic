import { api, uploadFile } from './api'
import { uploads, type Upload } from './store'

// Trascina e rilascia (D-25). Nell'app desktop i percorsi veri arrivano da pywebview (window.mosaicDrop);
// nel browser si leggono i file dal DataTransfer (cartelle comprese) e si caricano.

export const ACCEPTED = /\.(jpe?g|png|gif|webp|bmp|tiff?|heic|heif|avif|cr2|cr3|crw|nef|nrw|arw|srf|sr2|dng|orf|rw2|raf|pef|srw|3fr|rwl|mp4|mov|m4v|avi|mkv|webm|3gp|mts|m2ts|mpe?g|wmv|xmp|zip)$/i

export interface PickedFile { file: File; path: string }

export const isDesktop = (): boolean => typeof window !== 'undefined' && 'pywebview' in window

/** Il trascinamento porta file dal computer (e non, per esempio, testo o una foto della griglia). */
export const carriesFiles = (event: DragEvent): boolean => Array.from(event.dataTransfer?.types ?? []).includes('Files')

type Entry = FileSystemEntry
const readAll = (reader: FileSystemDirectoryReader): Promise<Entry[]> =>
  new Promise((resolve, reject) => {
    const out: Entry[] = []
    const next = (): void => reader.readEntries((batch) => (batch.length ? (out.push(...batch), next()) : resolve(out)), reject)
    next()
  })
const fileOf = (entry: FileSystemFileEntry): Promise<File> => new Promise((resolve, reject) => entry.file(resolve, reject))

async function walk(entry: Entry, prefix: string, out: PickedFile[]): Promise<void> {
  if (entry.name.startsWith('.')) return
  if (entry.isFile) {
    if (ACCEPTED.test(entry.name)) out.push({ file: await fileOf(entry as FileSystemFileEntry), path: `${prefix}${entry.name}` })
    return
  }
  if (entry.isDirectory) {
    for (const child of await readAll((entry as FileSystemDirectoryEntry).createReader())) await walk(child, `${prefix}${entry.name}/`, out)
  }
}

/** File rilasciati, con il percorso relativo dentro le cartelle trascinate. Solo foto, video, XMP e zip. */
export async function collectFiles(transfer: DataTransfer): Promise<PickedFile[]> {
  const entries = Array.from(transfer.items ?? []).map((item) => item.webkitGetAsEntry?.()).filter((e): e is Entry => !!e)
  if (!entries.length) {
    return Array.from(transfer.files ?? []).filter((f) => ACCEPTED.test(f.name)).map((file) => ({ file, path: file.name }))
  }
  const out: PickedFile[] = []
  for (const entry of entries) await walk(entry, '', out)
  return out
}

const newBatch = (): string => Array.from(crypto.getRandomValues(new Uint8Array(8)), (b) => b.toString(16).padStart(2, '0')).join('')
let uploadSeq = 0

function patch(id: number, changes: Partial<Upload>): void {
  uploads.value = uploads.value.map((u) => (u.id === id ? { ...u, ...changes } : u))
}

/** Carica i file uno alla volta con l'avanzamento complessivo, poi accoda l'import. Ritorna l'id del job. */
export async function uploadAll(files: PickedFile[]): Promise<number> {
  const batch = newBatch()
  const id = ++uploadSeq
  const total = files.reduce((n, f) => n + f.file.size, 0)
  const name = files.length === 1 ? files[0].path : `${files.length} file`
  uploads.value = [...uploads.value, { id, name, loaded: 0, total, state: 'uploading', error: '' }]
  let before = 0
  try {
    for (const { file, path } of files) {
      await uploadFile(batch, path, file, (loaded) => patch(id, { loaded: before + loaded }))
      before += file.size
      patch(id, { loaded: before })
    }
    const { job } = await api.uploadFinish(batch)
    patch(id, { state: 'done', loaded: total })
    setTimeout(() => { uploads.value = uploads.value.filter((u) => u.id !== id) }, 4000)
    return job
  } catch (error) {
    patch(id, { state: 'failed', error: error instanceof Error ? error.message : String(error) })
    void api.uploadCancel(batch).catch(() => undefined)
    throw error
  }
}

export function summarize(files: PickedFile[]): { photos: number; zips: number; folders: number; bytes: number } {
  const folders = new Set(files.map((f) => f.path.split('/')[0]).filter((_, i) => files[i].path.includes('/')))
  return {
    photos: files.filter((f) => !/\.(zip|xmp)$/i.test(f.path)).length,
    zips: files.filter((f) => /\.zip$/i.test(f.path)).length,
    folders: folders.size,
    bytes: files.reduce((n, f) => n + f.file.size, 0),
  }
}
