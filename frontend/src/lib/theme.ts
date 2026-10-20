import { ref } from 'vue'

export type Scheme = 'light' | 'dark' | 'system'
export type ViewerBg = 'black' | 'grey'
export const VIEWER_BGS = [
  { value: 'black', label: 'Nero' },
  { value: 'grey', label: 'Grigio 18%' },
] as const

function read(key: string): string | null {
  try {
    return localStorage.getItem(key)
  } catch {
    return null
  }
}

function write(key: string, value: string): void {
  try {
    localStorage.setItem(key, value)
  } catch {
    /* modalità privata: la preferenza dura solo per questa sessione */
  }
}

const storedScheme = read('mosaic.theme')
export const scheme = ref<Scheme>(storedScheme === 'light' || storedScheme === 'dark' ? storedScheme : 'system')
const storedViewer = read('mosaic.viewer')
export const viewerBg = ref<ViewerBg>(storedViewer === 'grey' ? 'grey' : 'black')

export function applyAppearance(): void {
  const root = document.documentElement
  if (scheme.value === 'system') root.removeAttribute('data-theme')
  else root.setAttribute('data-theme', scheme.value)
  if (viewerBg.value === 'grey') root.setAttribute('data-viewer', 'grey')
  else root.removeAttribute('data-viewer')
}

const reducedMotion = (): boolean => window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false

/** Cambia tema con una dissolvenza unica della pagina (View Transitions) e senza lampi di testo. */
export function setScheme(next: Scheme): void {
  const change = () => {
    scheme.value = next
    write('mosaic.theme', next)
    applyAppearance()
  }
  const doc = document as Document & { startViewTransition?: (cb: () => void) => unknown }
  if (!doc.startViewTransition || reducedMotion()) return change()
  document.documentElement.setAttribute('data-theme-fade', '')
  const transition = doc.startViewTransition(change) as { finished?: Promise<void> }
  void (transition.finished ?? Promise.resolve()).finally(() => document.documentElement.removeAttribute('data-theme-fade'))
}

export function setViewerBg(next: ViewerBg): void {
  viewerBg.value = next
  write('mosaic.viewer', next)
  applyAppearance()
}
