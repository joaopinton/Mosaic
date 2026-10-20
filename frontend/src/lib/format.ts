const DATE = new Intl.DateTimeFormat('it-IT', { day: 'numeric', month: 'long', year: 'numeric' })
const DATE_TIME = new Intl.DateTimeFormat('it-IT', { day: 'numeric', month: 'long', year: 'numeric', hour: '2-digit', minute: '2-digit' })
const NUMBER = new Intl.NumberFormat('it-IT')

export function formatDate(iso: string | null | undefined, withTime = false): string {
  if (!iso) return '—'
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? '—' : (withTime ? DATE_TIME : DATE).format(date)
}

export function formatDuration(seconds: number): string {
  const total = Math.max(0, Math.round(seconds))
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = total % 60
  return h > 0 ? `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}` : `${m}:${String(s).padStart(2, '0')}`
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  const units = ['KB', 'MB', 'GB', 'TB']
  let value = bytes / 1024
  let i = 0
  while (value >= 1024 && i < units.length - 1) {
    value /= 1024
    i++
  }
  return `${value.toFixed(value < 10 ? 1 : 0).replace('.', ',')} ${units[i]}`
}

export const formatCount = (n: number): string => NUMBER.format(n)

export interface HistoryLike { q: string; ts: number; pinned: boolean }
export interface HistoryGroup<T extends HistoryLike> { title: string; items: T[] }

/** Raggruppa le ricerche recenti come la sidebar di sift: Fissate, Oggi, Ieri, Questa settimana, Prima. */
export function groupHistory<T extends HistoryLike>(items: T[], now = Date.now()): HistoryGroup<T>[] {
  const startOfDay = new Date(now)
  startOfDay.setHours(0, 0, 0, 0)
  const today = startOfDay.getTime()
  const day = 86_400_000
  const buckets: Record<string, T[]> = { Fissate: [], Oggi: [], Ieri: [], 'Questa settimana': [], Prima: [] }
  for (const item of [...items].sort((a, b) => b.ts - a.ts)) {
    const key = item.pinned ? 'Fissate' : item.ts >= today ? 'Oggi' : item.ts >= today - day ? 'Ieri' : item.ts >= today - 6 * day ? 'Questa settimana' : 'Prima'
    buckets[key].push(item)
  }
  return Object.entries(buckets).filter(([, list]) => list.length > 0).map(([title, list]) => ({ title, items: list }))
}
