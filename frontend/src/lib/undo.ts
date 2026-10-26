import { computed, ref } from 'vue'
import { libraryVersion } from './store'
import { errorMessage, toast } from './toast'

// Annulla e ripeti (Ctrl+Z, Ctrl+Y): ogni azione reversibile registra come tornare indietro e come rifarla.
// Le griglie si ricaricano da sole perché dopo ogni passo `libraryVersion` cresce.

export interface UndoEntry {
  label: string
  undo: () => Promise<void>
  redo: () => Promise<void>
}

const LIMIT = 50
const past = ref<UndoEntry[]>([])
const future = ref<UndoEntry[]>([])
let working = false

export const canUndo = computed(() => past.value.length > 0)
export const canRedo = computed(() => future.value.length > 0)

/** Registra un'azione appena fatta (azzera il «ripeti»); ritorna la voce, per offrire «Annulla» nel toast. */
export function pushUndo(entry: UndoEntry): UndoEntry {
  past.value = [...past.value, entry].slice(-LIMIT)
  future.value = []
  return entry
}

export function clearUndo(): void {
  past.value = []
  future.value = []
}

async function step(from: typeof past, to: typeof past, direction: 'undo' | 'redo'): Promise<boolean> {
  const entry = from.value[from.value.length - 1]
  if (!entry || working) return false
  working = true
  try {
    await entry[direction]()
    from.value = from.value.slice(0, -1)
    to.value = [...to.value, entry].slice(-LIMIT)
    libraryVersion.value++
    const verb = direction === 'undo' ? 'Annullato' : 'Ripetuto'
    const other = direction === 'undo' ? { label: 'Ripeti', run: () => void redo() } : { label: 'Annulla', run: () => void undo() }
    toast(`${verb}: ${entry.label}`, 'default', other)
    return true
  } catch (error) {
    toast(`Non si può ${direction === 'undo' ? 'annullare' : 'ripetere'} «${entry.label}»: ${errorMessage(error)}`, 'error')
    return false
  } finally {
    working = false
  }
}

export const undo = (): Promise<boolean> => step(past, future, 'undo')
export const redo = (): Promise<boolean> => step(future, past, 'redo')

/** «Annulla» dal toast di una certa azione: vale solo se è ancora l'ultima fatta, altrimenti si annullerebbe un'altra cosa. */
export function undoEntry(entry: UndoEntry): void {
  if (past.value[past.value.length - 1] === entry) void undo()
  else toast('Questa azione non è più l\'ultima: annulla prima le più recenti con Ctrl+Z')
}

/** Azione con «Annulla» nel toast: l'uso tipico dopo aver eseguito e registrato un'azione. */
export function undoAction(entry: UndoEntry): { label: string; run: () => void } {
  return { label: 'Annulla', run: () => undoEntry(entry) }
}
