// Scorciatoie globali: `/` cerca, `?` elenco, sequenze `g` + lettera (timeout 1 s). Non scattano mentre si digita.

export interface HotkeyHandlers {
  focusSearch: () => void
  showHelp: () => void
  go: (path: string) => void
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

export function installHotkeys(handlers: HotkeyHandlers): () => void {
  let pending = false
  let timer: ReturnType<typeof setTimeout> | undefined
  const reset = () => {
    pending = false
    if (timer) clearTimeout(timer)
  }
  const onKey = (e: KeyboardEvent) => {
    if (e.ctrlKey || e.altKey || e.metaKey || isTyping(e.target)) return
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
