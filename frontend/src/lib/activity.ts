import type { Job } from './types'

// Attività della libreria (D-23): fasi della pipeline, progresso complessivo e tempo rimanente.

export interface StageInfo { type: string; label: string; icon: string; unit: string }

/** Le fasi nell'ordine in cui la pipeline le esegue. */
export const PIPELINE: StageInfo[] = [
  { type: 'index', label: 'Indicizzazione', icon: 'folderOpen', unit: 'file' },
  { type: 'process', label: 'Metadati e miniature', icon: 'image', unit: 'foto' },
  { type: 'analyze', label: 'Analisi AI', icon: 'sparkle', unit: 'foto' },
  { type: 'faces', label: 'Volti', icon: 'people', unit: 'volti' },
  { type: 'moments', label: 'Album automatici', icon: 'album', unit: '' },
  { type: 'caption', label: 'Didascalie', icon: 'edit', unit: 'didascalie' },
]

const OTHER: Record<string, Omit<StageInfo, 'type'>> = {
  import: { label: 'Importazione', icon: 'download', unit: 'foto' },
  upload: { label: 'Caricamento', icon: 'upload', unit: 'file' },
  rename: { label: 'Rinomina', icon: 'edit', unit: 'foto' },
  rollback: { label: 'Annullamento rinomina', icon: 'undo', unit: 'file' },
  transcode: { label: 'Conversione video', icon: 'video', unit: '' },
  cleanup: { label: 'Pulizia', icon: 'trash', unit: '' },
  unlink: { label: 'Scollegamento cartella', icon: 'folder', unit: '' },
  backup: { label: 'Backup dei dati manuali', icon: 'archive', unit: '' },
  drop: { label: 'Aggiunta dei file rilasciati', icon: 'plus', unit: 'elementi' },
  export: { label: 'Esportazione', icon: 'download', unit: 'foto' },
}

export function stageInfo(type: string): StageInfo {
  const found = PIPELINE.find((s) => s.type === type)
  if (found) return found
  return { type, ...(OTHER[type] ?? { label: type, icon: 'spinner', unit: '' }) }
}

export type StageState = 'idle' | 'queued' | 'running' | 'done' | 'failed'

export interface StageSummary extends StageInfo {
  state: StageState
  done: number
  total: number
  /** 0..1, oppure null se il totale non si conosce ancora (barra indeterminata) */
  fraction: number | null
  message: string
  jobs: Job[]
}

const isActive = (j: Job): boolean => j.state === 'queued' || j.state === 'running'
const RECENT_MS = 30 * 60 * 1000

function size(j: Job): number {
  return j.total || j.done
}

/** Riassunto di una fase: i job attivi di quel tipo più quelli finiti dopo la creazione del primo attivo (i blocchi della stessa ondata). */
export function summarizeStage(info: StageInfo, jobs: Job[], now = Date.now()): StageSummary {
  const ofType = jobs.filter((j) => j.type === info.type)
  const active = ofType.filter(isActive)
  if (!active.length) {
    const last = ofType[0]
    const recent = last?.finished_at ? now - Date.parse(last.finished_at) < RECENT_MS : false
    const state: StageState = last && recent ? (last.state === 'done' ? 'done' : last.state === 'failed' ? 'failed' : 'idle') : 'idle'
    return { ...info, state, done: 0, total: 0, fraction: state === 'done' ? 1 : null, message: recent ? last?.message ?? '' : '', jobs: ofType.slice(0, 1) }
  }
  const since = Math.min(...active.map((j) => Date.parse(j.created_at) || now))
  const finished = ofType.filter((j) => j.state === 'done' && j.finished_at && Date.parse(j.finished_at) >= since)
  const done = finished.reduce((n, j) => n + size(j), 0) + active.reduce((n, j) => n + (j.state === 'running' ? j.done : 0), 0)
  const total = finished.reduce((n, j) => n + size(j), 0) + active.reduce((n, j) => n + j.total, 0)
  const running = active.find((j) => j.state === 'running')
  let fraction: number | null = total > 0 ? Math.min(1, done / total) : null
  if (fraction === null && running && running.progress > 0) fraction = running.progress
  return {
    ...info, state: running ? 'running' : 'queued', done, total: Math.max(total, done), fraction,
    message: running?.message ?? '', jobs: [...active, ...finished],
  }
}

/** Le didascalie sono un passo solo se automatiche (`withCaption`); chieste a mano compaiono finché se ne vede il job. */
export function summarizePipeline(jobs: Job[], now = Date.now(), withCaption = true): StageSummary[] {
  return PIPELINE.map((info) => summarizeStage(info, jobs, now))
    .filter((s) => s.type !== 'caption' || withCaption || s.state !== 'idle')
}

/** Lavori di servizio che partono da soli e durano un attimo (il backup YAML dopo ogni modifica manuale):
 * non compaiono come attività, solo tra i recenti se non riescono. */
export const QUIET_TYPES = new Set(['backup'])

/** Lavori attivi fuori dalla pipeline (import, rinomina, scollegamento…), uno per tipo. */
export function otherActivities(jobs: Job[], now = Date.now()): StageSummary[] {
  const types = [...new Set(jobs.filter((j) => isActive(j) && !QUIET_TYPES.has(j.type) && !PIPELINE.some((s) => s.type === j.type))
    .map((j) => j.type))]
  return types.map((t) => summarizeStage(stageInfo(t), jobs, now))
}

/** L'attività da mostrare in primo piano: prima quella in corso, poi quella in coda. */
export function currentActivity(stages: StageSummary[]): StageSummary | null {
  return stages.find((s) => s.state === 'running') ?? stages.find((s) => s.state === 'queued') ?? null
}

/** Ritmo misurato dal vivo: si tengono i campioni (istante, fatti) degli ultimi 90 s per ogni chiave. */
export class RateTracker {
  private samples = new Map<string, { t: number; done: number }[]>()

  record(key: string, done: number, t = Date.now()): void {
    const list = this.samples.get(key) ?? []
    const last = list[list.length - 1]
    if (last && done < last.done) list.length = 0 // ricominciato: i vecchi campioni non valgono più
    if (!last || done !== last.done) list.push({ t, done })
    this.samples.set(key, list.filter((s) => t - s.t <= 90_000))
  }

  /** Secondi rimanenti, oppure null se non c'è ancora abbastanza storia (servono almeno 4 s e un avanzamento). */
  remaining(key: string, done: number, total: number, t = Date.now()): number | null {
    const list = this.samples.get(key)
    if (!list || list.length < 2 || total <= 0 || done >= total) return null
    const first = list[0]
    const span = (t - first.t) / 1000
    const advanced = done - first.done
    if (span < 4 || advanced <= 0) return null
    return ((total - done) / advanced) * span
  }

  clear(key: string): void {
    this.samples.delete(key)
  }
}

export function formatRemaining(seconds: number | null): string {
  if (seconds === null || !Number.isFinite(seconds)) return ''
  if (seconds < 50) return 'meno di un minuto'
  const minutes = Math.round(seconds / 60)
  if (minutes < 60) return `circa ${minutes} min`
  const hours = Math.floor(minutes / 60)
  const rest = minutes % 60
  return rest ? `circa ${hours} h ${rest} min` : `circa ${hours} h`
}

export function formatCountOf(s: Pick<StageSummary, 'done' | 'total' | 'unit'>): string {
  if (!s.total) return s.done ? `${s.done.toLocaleString('it-IT')} ${s.unit}`.trim() : ''
  return `${s.done.toLocaleString('it-IT')} di ${s.total.toLocaleString('it-IT')} ${s.unit}`.trim()
}

export function formatAgo(iso: string | null, now = Date.now()): string {
  if (!iso) return ''
  const seconds = Math.max(0, (now - Date.parse(iso)) / 1000)
  if (seconds < 60) return 'adesso'
  if (seconds < 3600) return `${Math.round(seconds / 60)} min fa`
  if (seconds < 86_400) return `${Math.round(seconds / 3600)} h fa`
  return `${Math.round(seconds / 86_400)} g fa`
}
