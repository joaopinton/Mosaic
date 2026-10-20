import { ref } from 'vue'

export interface Toast {
  id: number
  text: string
  tone: 'default' | 'error'
  action?: { label: string; run: () => void }
}

const MAX_VISIBLE = 3
const DEFAULT_MS = 5000

export const toasts = ref<Toast[]>([])
let nextId = 1

export function dismiss(id: number): void {
  toasts.value = toasts.value.filter((t) => t.id !== id)
}

/** Mostra un avviso: al massimo tre alla volta, si chiude da solo dopo cinque secondi. */
export function toast(text: string, tone: Toast['tone'] = 'default', action?: Toast['action'], durationMs = DEFAULT_MS): number {
  const id = nextId++
  toasts.value = [...toasts.value, { id, text, tone, action }].slice(-MAX_VISIBLE)
  if (durationMs > 0) setTimeout(() => dismiss(id), durationMs)
  return id
}

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'Errore imprevisto'
}
