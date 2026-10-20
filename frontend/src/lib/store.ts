import { computed, reactive, ref } from 'vue'
import { api } from './api'
import type { CurateParams, Job, JobEvent, PhotoSummary, Status } from './types'

// --- stato della libreria e dei job ---------------------------------------------------------------

export const status = ref<Status | null>(null)
export const jobs = ref<Job[]>([])
/** Cresce a ogni job concluso: le griglie lo osservano per ricaricarsi. */
export const libraryVersion = ref(0)

export async function refreshStatus(): Promise<void> {
  try {
    status.value = await api.status()
  } catch {
    /* il server può essere momentaneamente occupato: si riprova al prossimo evento */
  }
}

export async function refreshJobs(): Promise<void> {
  try {
    jobs.value = await api.jobs()
  } catch {
    /* idem */
  }
}

let statusTimer: ReturnType<typeof setTimeout> | undefined
function scheduleStatus(): void {
  if (statusTimer) return
  statusTimer = setTimeout(() => {
    statusTimer = undefined
    void refreshStatus()
  }, 600)
}

const FINISHED_KEPT = 40

/** Tutti i job attivi (anche centinaia di blocchi in coda: servono al totale della fase) e gli ultimi finiti. */
export function keepJobs(list: Job[]): Job[] {
  let finished = 0
  return list.filter((j) => j.state === 'queued' || j.state === 'running' || ++finished <= FINISHED_KEPT)
}

function applyEvent(event: JobEvent): void {
  const index = jobs.value.findIndex((j) => j.id === event.id)
  const now = new Date().toISOString()
  const base: Job = index >= 0 ? jobs.value[index] : {
    id: event.id, type: event.type, state: event.state, progress: 0, message: '', error: '', created_at: now, finished_at: null,
    done: 0, total: 0,
  }
  const ended = event.state === 'done' || event.state === 'failed' || event.state === 'cancelled'
  const next: Job = {
    ...base, state: event.state, progress: event.progress ?? base.progress, message: event.message ?? base.message,
    error: event.error ?? base.error, done: event.done ?? base.done, total: event.total ?? base.total,
    started_at: base.started_at ?? (event.state === 'running' ? now : null),
    finished_at: ended ? base.finished_at ?? now : base.finished_at,
  }
  jobs.value = keepJobs(index >= 0 ? jobs.value.map((j, i) => (i === index ? next : j)) : [next, ...jobs.value])
  scheduleStatus()
  if (event.state === 'done' || event.state === 'failed' || event.state === 'cancelled') libraryVersion.value++
}

/** Si abbona agli eventi del server (SSE). Ritorna la funzione che chiude la connessione. */
export function startEvents(): () => void {
  if (typeof EventSource === 'undefined') return () => undefined
  const source = new EventSource('/api/events')
  source.onmessage = (message) => {
    try {
      const event = JSON.parse(message.data) as JobEvent
      if (event.event === 'job') applyEvent(event)
    } catch {
      /* evento non valido: ignorato */
    }
  }
  return () => source.close()
}

/** File che si stanno caricando dal trascina e rilascia (nel browser, dove i percorsi non sono disponibili). */
export interface Upload { id: number; name: string; loaded: number; total: number; state: 'uploading' | 'done' | 'failed'; error: string }
export const uploads = ref<Upload[]>([])

export const activeJobs = computed(() => jobs.value.filter((j) => j.state === 'queued' || j.state === 'running'))
export const isBusy = computed(() => activeJobs.value.length > 0)

// --- sidebar -------------------------------------------------------------------------------------------

function storage(key: string): string | null {
  try {
    return localStorage.getItem(key)
  } catch {
    return null
  }
}

function persist(key: string, value: string): void {
  try {
    localStorage.setItem(key, value)
  } catch {
    /* non persistente: va bene */
  }
}

const savedSidebar = storage('mosaic.sidebar')
export const sidebar = reactive({
  preference: (savedSidebar === 'collapsed' || savedSidebar === 'open' ? savedSidebar : null) as 'collapsed' | 'open' | null,
  drawerOpen: false,
  panel: null as 'filters' | 'curate' | 'activity' | null,
})

/** Pannello Attività nella sidebar (dalla barra in cima al contenuto o dall'anello nella sidebar). */
export function openActivity(): void {
  sidebar.panel = 'activity'
}

/** Album o cartella di cui si sta creando la selezione, con i valori da cui riparte il pannello. */
export const curate = reactive({ source: null as { uid: string; title: string } | null, draft: null as CurateParams | null })

export function openCurate(source: { uid: string; title: string }, draft: CurateParams | null = null): void {
  curate.source = source
  curate.draft = draft
  sidebar.panel = 'curate'
}

export function setSidebarPreference(value: 'collapsed' | 'open'): void {
  sidebar.preference = value
  persist('mosaic.sidebar', value)
}

// --- ricerche recenti -------------------------------------------------------------------------------------

export interface HistoryItem {
  q: string
  ts: number
  pinned: boolean
  title?: string
}

const HISTORY_KEY = 'mosaic.history'
const HISTORY_MAX = 40

function loadHistory(): HistoryItem[] {
  try {
    const parsed = JSON.parse(storage(HISTORY_KEY) ?? '[]')
    return Array.isArray(parsed) ? parsed.filter((h) => typeof h?.q === 'string' && typeof h?.ts === 'number') : []
  } catch {
    return []
  }
}

export const history = ref<HistoryItem[]>(loadHistory())

function saveHistory(): void {
  persist(HISTORY_KEY, JSON.stringify(history.value))
}

export function pushHistory(q: string): void {
  const text = q.trim()
  if (!text) return
  const existing = history.value.find((h) => h.q === text)
  const rest = history.value.filter((h) => h.q !== text)
  history.value = [{ q: text, ts: Date.now(), pinned: existing?.pinned ?? false, title: existing?.title }, ...rest].slice(0, HISTORY_MAX)
  saveHistory()
}

export function updateHistory(q: string, changes: Partial<Pick<HistoryItem, 'pinned' | 'title'>>): void {
  history.value = history.value.map((h) => (h.q === q ? { ...h, ...changes } : h))
  saveHistory()
}

export function removeHistory(q: string): void {
  history.value = history.value.filter((h) => h.q !== q)
  saveHistory()
}

// --- foto visibili e selezione -----------------------------------------------------------------------------

/** Foto della griglia corrente, per scorrere avanti e indietro nel dettaglio. */
export const viewer = reactive({ photos: [] as PhotoSummary[] })

/** Incrementato dalla scorciatoia /: la sidebar apre il campo di ricerca. */
export const searchRequest = ref(0)

/** Sezione della sidebar della pagina aperta sotto /albums/ (Album, Cartelle, Momenti o Calendario). */
export const currentSection = ref<string | null>(null)

/** Album manuale aperto in questo momento: la barra della selezione offre «Togli dall'album» solo lì. */
export const currentAlbum = ref<{ uid: string; title: string } | null>(null)

export const selected = ref<string[]>([])
export const hasSelection = computed(() => selected.value.length > 0)

export function toggleSelected(uid: string): void {
  selected.value = selected.value.includes(uid) ? selected.value.filter((u) => u !== uid) : [...selected.value, uid]
}

export function clearSelection(): void {
  selected.value = []
}
