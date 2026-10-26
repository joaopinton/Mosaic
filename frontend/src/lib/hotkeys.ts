// Scorciatoie globali: `/` cerca, `?` elenco, sequenze `g` + lettera (timeout 1 s), Ctrl+A seleziona tutto, Ctrl+Z / Ctrl+Y
// annulla e ripeti, Esc deseleziona. Non scattano mentre si digita (lì valgono quelle del campo di testo).

export interface HotkeyHandlers {
  focusSearch: () => void
  showHelp: () => void
  go: (path: string) => void
  selectAll?: () => void
  undo?: () => void
  redo?: () => void
  escape?: () => void
}

export const GO_KEYS: Record<string, { path: string; label: string }> = {
  e: { path: '/', label: 'Esplora' },
  a: { path: '/albums', label: 'Album' },
  p: { path: '/people', label: 'Persone' },
  l: { path: '/labels', label: 'Etichette' },
  c: { path: '/calendar', label: 'Calendario' },
  v: { path: '/videos', label: 'Video' },
  f: { path: '/favorites', label: 'Preferiti' },
  r: { path: '/review', label: 'Da rivedere' },
  d: { path: '/duplicates', label: 'Duplicati' },
  i: { path: '/library', label: 'Libreria' },
  s: { path: '/settings', label: 'Impostazioni' },
}

const SEQUENCE_MS = 1000

export function isTyping(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null
  if (!el || !el.tagName) return false
  return el.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName)
}

function onChord(e: KeyboardEvent, handlers: HotkeyHandlers): void {
  const key = e.key.toLowerCase()
  const action = key === 'a' ? handlers.selectAll : key === 'z' ? (e.shiftKey ? handlers.redo : handlers.undo) : key === 'y' ? handlers.redo : undefined
  if (!action) return
  e.preventDefault()
  action()
}

export function installHotkeys(handlers: HotkeyHandlers): () => void {
  let pending = false
  let timer: ReturnType<typeof setTimeout> | undefined
  const reset = () => {
    pending = false
    if (timer) clearTimeout(timer)
  }
  const onKey = (e: KeyboardEvent) => {
    if (isTyping(e.target)) return
    if ((e.ctrlKey || e.metaKey) && !e.altKey) return onChord(e, handlers)
    if (e.altKey || e.ctrlKey || e.metaKey) return
    if (e.key === 'Escape') return handlers.escape?.()
    if (pending) {
      const target = GO_KEYS[e.key.toLowerCase()]
      reset()
      if (target) {
        e.preventDefault()
        handlers.go(target.path)
      }
      return
    }
    if (e.key === '/') {
      e.preventDefault()
      handlers.focusSearch()
    } else if (e.key === '?') {
      e.preventDefault()
      handlers.showHelp()
    } else if (e.key === 'g') {
      pending = true
      timer = setTimeout(reset, SEQUENCE_MS)
    }
  }
  window.addEventListener('keydown', onKey)
  return () => {
    reset()
    window.removeEventListener('keydown', onKey)
  }
}
