import { api, type BatchActionName } from './api'
import { libraryVersion, viewer } from './store'
import { toast, errorMessage } from './toast'
import { pushUndo, undoAction } from './undo'

// Azioni di gruppo sulle foto con «Annulla» (Ctrl+Z): un solo punto per barra della selezione e dettaglio.

const INVERSE: Partial<Record<BatchActionName, BatchActionName>> = {
  archive: 'restore', restore: 'archive', add_to_album: 'remove_from_album', remove_from_album: 'add_to_album',
  favorite: 'unfavorite', unfavorite: 'favorite', private: 'public', public: 'private',
}
const WAS: Partial<Record<BatchActionName, 'favorite' | 'private'>> = { favorite: 'favorite', unfavorite: 'favorite', private: 'private', public: 'private' }
const SETS: Partial<Record<BatchActionName, boolean>> = { favorite: true, unfavorite: false, private: true, public: false }

/** Le foto che l'azione cambia davvero, per poterla annullare senza toccare quelle già nello stato voluto (non si conosce → fuori). */
function affected(uids: string[], action: BatchActionName): string[] {
  const flag = WAS[action]
  if (!flag) return uids
  const known = new Map(viewer.photos.map((p) => [p.uid, p[flag]]))
  return uids.filter((u) => known.has(u) && known.get(u) !== SETS[action])
}

export interface BatchOutcome { changed: number; undoable: boolean }

/** Esegue l'azione, la registra per l'annullamento e mostra il toast con «Annulla». Lancia l'errore del server. */
export async function runBatch(uids: string[], action: BatchActionName, label: string, album?: string): Promise<BatchOutcome> {
  const targets = affected(uids, action)
  const { changed } = await api.batch(uids, action, album)
  const inverse = INVERSE[action]
  // con le album il server non dice quali foto c'erano già: si annulla solo se sono cambiate tutte
  const sure = action === 'add_to_album' || action === 'remove_from_album' ? changed === uids.length : true
  const undoable = Boolean(inverse) && sure && targets.length > 0
  libraryVersion.value++
  if (!undoable || !inverse) {
    toast(`${label}: ${changed} foto`)
    return { changed, undoable: false }
  }
  const entry = pushUndo({
    label: `${label} (${targets.length} foto)`,
    undo: async () => void (await api.batch(targets, inverse, album)),
    redo: async () => void (await api.batch(targets, action, album)),
  })
  toast(`${label}: ${changed} foto`, 'default', undoAction(entry), 8000)
  return { changed, undoable: true }
}

export async function runBatchSafe(uids: string[], action: BatchActionName, label: string, album?: string): Promise<BatchOutcome | null> {
  try {
    return await runBatch(uids, action, label, album)
  } catch (error) {
    toast(errorMessage(error), 'error')
    return null
  }
}
