import type {
  Album, AppConfig, CalendarYear, CloudStatus, CropFormat, CropPreviewShot, CurateParams, CurateResult, DropPreview, DuplicateExact, EnhanceOptions, ExportPreviewShot, ExportRequest, PosterFile, PosterLayout, PosterOptions, DuplicateNear, FaceGroup, Job, LabelInfo, PhotoDetail, PhotoList,
  ExcludedFile, LibraryRoots, RemoveResult, RenameBatch, RenamePreview, SimilarHit, Status, Subject, SuggestionGroup,
} from './types'

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message)
  }
}

async function request<T>(method: string, url: string, body?: unknown): Promise<T> {
  const res = await fetch(url, {
    method,
    headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (!res.ok) {
    let detail = res.statusText
    try {
      const data = await res.json()
      detail = typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail ?? data)
    } catch {
      /* corpo non JSON: resta il testo di stato */
    }
    throw new ApiError(res.status, detail)
  }
  return (res.status === 204 ? undefined : await res.json()) as T
}

const get = <T>(url: string) => request<T>('GET', url)
const post = <T>(url: string, body?: unknown) => request<T>('POST', url, body ?? {})
const patch = <T>(url: string, body: unknown) => request<T>('PATCH', url, body)

export interface PhotoParams { q?: string; count?: number; offset?: number; order?: string; reverse?: boolean }

export function queryString(params: Record<string, string | number | boolean | undefined>): string {
  const usp = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== '' && v !== false) usp.set(k, String(v))
  const s = usp.toString()
  return s ? `?${s}` : ''
}

export type BatchActionName = 'favorite' | 'unfavorite' | 'private' | 'public' | 'archive' | 'restore' | 'add_to_album' | 'remove_from_album'

export const api = {
  status: () => get<Status>('/api/status'),
  config: () => get<AppConfig>('/api/config'),
  putConfig: (changes: Record<string, unknown>) => request<AppConfig>('PUT', '/api/config', { changes }),
  cloudStatus: () => get<CloudStatus>('/api/llm/cloud'),
  putCloudKey: (provider: string, key: string) => request<CloudStatus>('PUT', '/api/llm/cloud/key', { provider, key }),

  photos: (p: PhotoParams) => get<PhotoList>(`/api/photos${queryString({ ...p })}`),
  photo: (uid: string) => get<PhotoDetail>(`/api/photos/${uid}`),
  patchPhoto: (uid: string, body: Record<string, unknown>) => patch<PhotoDetail>(`/api/photos/${uid}`, body),
  batch: (uids: string[], action: BatchActionName, album?: string) => post<{ changed: number }>('/api/photos/batch', { uids, action, album }),
  removePhotos: (uids: string[]) => post<RemoveResult>('/api/photos/remove', { uids }),
  similar: (uid: string, k = 8) => get<{ ready: boolean; hits: SimilarHit[] }>(`/api/photos/${uid}/similar?k=${k}`),
  suggest: (q: string) => get<{ keys: string[]; values: string[] }>(`/api/search/suggest${queryString({ q })}`),

  albums: (type?: string, hidden = false) => get<Album[]>(`/api/albums${queryString({ type, hidden })}`),
  album: (uid: string) => get<Album>(`/api/albums/${uid}`),
  createAlbum: (title: string, uids: string[] = [], source?: string) => post<Album>('/api/albums', { title, uids, source }),
  albumSelections: (uid: string) => get<Album[]>(`/api/albums/${uid}/selections`),
  albumPosters: (uid: string) => get<{ folder: string; posters: PosterFile[] }>(`/api/albums/${uid}/posters`),
  patchAlbum: (uid: string, body: Record<string, unknown>) => patch<Album>(`/api/albums/${uid}`, body),
  deleteAlbum: (uid: string) => request<void>('DELETE', `/api/albums/${uid}`),
  restoreAlbumTitle: (uid: string) => post<Album>(`/api/albums/${uid}/restore-title`),
  revealAlbum: (uid: string) => request<void>('POST', `/api/albums/${uid}/reveal`, {}),
  addToAlbum: (uid: string, uids: string[]) => post<{ changed: number }>(`/api/albums/${uid}/photos`, { uids }),
  curate: (uid: string, params: CurateParams) => post<CurateResult>(`/api/albums/${uid}/curate`, params),
  calendar: () => get<CalendarYear[]>('/api/calendar'),
  labels: () => get<LabelInfo[]>('/api/labels'),

  subjects: () => get<Subject[]>('/api/subjects'),
  faces: () => get<FaceGroup[]>('/api/faces'),
  nameFace: (face: string, name: string) => post<{ subject: string }>(`/api/faces/${face}/name`, { name }),
  nameMarker: (marker: string, name: string) => post<{ subject: string }>(`/api/markers/${marker}/name`, { name }),
  suggestions: () => get<SuggestionGroup[]>('/api/faces/suggestions'),
  confirmMarker: (marker: string) => post<{ subject: string }>(`/api/markers/${marker}/confirm`),
  rejectSuggestion: (marker: string) => request<void>('POST', `/api/markers/${marker}/suggestion/reject`, {}),
  confirmSuggestions: (uids: string[]) => post<{ confirmed: number; skipped: string[] }>('/api/faces/suggestions/confirm', { uids }),
  rejectMarker: (marker: string) => request<void>('POST', `/api/markers/${marker}/reject`, {}),
  patchSubject: (uid: string, body: Record<string, unknown>) => patch<Subject>(`/api/subjects/${uid}`, body),
  mergeSubjects: (keep: string, drop: string) => post<{ uid: string }>('/api/subjects/merge', { keep, drop }),
  duplicates: (kind: 'exact' | 'near') =>
    get<{ kind: string; groups: (DuplicateExact | DuplicateNear)[] }>(`/api/duplicates?kind=${kind}`),

  jobs: () => get<Job[]>('/api/jobs'),
  cancelJob: (id: number) => post<{ cancelled: boolean }>(`/api/jobs/${id}/cancel`),
  cancelJobType: (type: string) => post<{ cancelled: number }>(`/api/jobs/cancel-type/${encodeURIComponent(type)}`),
  restartJobType: (type: string) => post<{ job: number }>(`/api/jobs/restart-type/${encodeURIComponent(type)}`),
  index: (rescan = false) => post<{ job: number }>('/api/library/index', { rescan }),
  importFolder: (source: string, move: boolean) => post<{ job: number }>('/api/library/import', { source, move }),
  libraryRoots: () => get<LibraryRoots>('/api/library/roots'),
  unlinkRoot: (root: string) => post<{ job: number }>('/api/library/roots/unlink', { root }),
  excluded: () => get<ExcludedFile[]>('/api/excluded'),
  restoreExcluded: (sha1s?: string[]) => post<{ restored: number; job: number }>('/api/excluded/restore', { sha1s }),
  cleanup: (purge: boolean) => post<{ job: number }>('/api/library/cleanup', { purge }),
  analyze: () => post<{ job: number }>('/api/library/analyze'),
  captionAlbum: (uid: string) => post<{ job: number; photos: number }>(`/api/albums/${uid}/caption`, {}),
  caption: (force = false, uids?: string[]) => post<{ job: number }>('/api/library/caption', { force, uids }),

  dropInspect: (paths: string[]) => post<DropPreview>('/api/drop/inspect', { paths }),
  dropAdd: (paths: string[], folders: 'link' | 'copy') => post<{ linked: string[]; jobs: number[] }>('/api/drop/add', { paths, folders }),
  uploadFinish: (batch: string) => post<{ job: number }>(`/api/drop/upload/${batch}/finish`),
  uploadCancel: (batch: string) => request<void>('DELETE', `/api/drop/upload/${batch}`),

  posterLayout: (uids: string[], options: PosterOptions) => post<PosterLayout>('/api/posters/layout', { uids, ...options }),
  createPoster: (uids: string[], options: PosterOptions, title: string, album: string) =>
    post<{ job: number; name: string; folder: string }>('/api/posters', { uids, ...options, title, album }),
  exportPhotos: (body: ExportRequest) => post<{ job: number; folder: string }>('/api/exports', body),
  exportDefaultDir: (title: string) => get<{ folder: string }>(`/api/exports/default-dir${queryString({ title })}`),
  exportPreview: (uids: string[], enhance: EnhanceOptions) => post<{ photos: ExportPreviewShot[] }>('/api/exports/preview', { uids, enhance }),
  exportCropPreview: (uids: string[], crop: CropFormat) => post<{ photos: CropPreviewShot[] }>('/api/exports/crop-preview', { uids, crop }),
  revealExport: (job: number) => request<void>('POST', `/api/exports/${job}/reveal`, {}),
  pickFolder: (start: string) => post<{ folder: string | null }>('/api/dialog/folder', { start }),
  revealPoster: (album: string, name: string) => request<void>('POST', `/api/albums/${album}/posters/${encodeURIComponent(name)}/reveal`, {}),
  openPoster: (album: string, name: string) => request<void>('POST', `/api/albums/${album}/posters/${encodeURIComponent(name)}/open`, {}),

  renamePreview: (pattern?: string, force = false) => post<RenamePreview>('/api/rename/preview', { pattern, force }),
  renameApply: (pattern?: string, force = false) => post<{ job: number }>('/api/rename/apply', { pattern, force }),
  renameRollback: (batchId: string | null) => post<{ job: number }>('/api/rename/rollback', batchId ? { batch_id: batchId } : { all: true }),
  renameBatches: () => get<RenameBatch[]>('/api/rename/batches'),
}

export const thumbUrl = (hash: string | null | undefined, size = 'fit_720') => (hash ? `/t/${hash}/${size}` : '')
export const originalUrl = (uid: string) => `/api/files/${uid}/original`
export const videoUrl = (uid: string) => `/api/videos/${uid}`
export const liveUrl = (uid: string) => `/api/live/${uid}`
const PAGE = 500

/** Tutti gli uid di una ricerca (per esempio `album:<uid>`), a pagine, fino a `limit`: servono a esportare un album. */
export async function allUids(q: string, limit = 5000): Promise<string[]> {
  const uids: string[] = []
  for (let offset = 0; offset < limit; offset += PAGE) {
    const page = await api.photos({ q, count: Math.min(PAGE, limit - offset), offset })
    uids.push(...page.photos.map((p) => p.uid))
    if (page.photos.length < PAGE || uids.length >= page.total) break
  }
  return uids
}

export const posterUrl = (album: string, name: string) => `/api/albums/${album}/posters/${encodeURIComponent(name)}`
export const cropUrl = (marker: string, size = 96) => `/api/markers/${marker}/crop?size=${size}`

/** Carica un file con il suo avanzamento (XMLHttpRequest: fetch non dà il progresso dell'invio). */
export function uploadFile(batch: string, path: string, file: Blob, onProgress: (loaded: number) => void): Promise<void> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('PUT', `/api/drop/upload/${batch}${queryString({ path })}`)
    xhr.upload.onprogress = (event) => onProgress(event.loaded)
    xhr.onload = () => {
      if (xhr.status < 300) return resolve()
      let detail = xhr.statusText
      try {
        detail = JSON.parse(xhr.responseText).detail ?? detail
      } catch {
        /* corpo non JSON */
      }
      reject(new ApiError(xhr.status, String(detail)))
    }
    xhr.onerror = () => reject(new ApiError(0, 'connessione interrotta durante il caricamento'))
    xhr.send(file)
  })
}
