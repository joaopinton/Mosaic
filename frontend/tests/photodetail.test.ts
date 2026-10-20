import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mockMatchMedia } from './setup'

const mounted: { unmount: () => void }[] = []
const track = <T extends { unmount: () => void }>(wrapper: T): T => {
  mounted.push(wrapper)
  return wrapper
}

const api = vi.hoisted(() => ({
  photo: vi.fn(), similar: vi.fn(), albums: vi.fn(), patchPhoto: vi.fn(), batch: vi.fn(),
  nameMarker: vi.fn(), rejectMarker: vi.fn(), caption: vi.fn(), removePhotos: vi.fn(),
}))
vi.mock('../src/lib/api', async (original) => ({ ...(await original<typeof import('../src/lib/api')>()), api }))
// PhotoSwipe non gira in jsdom: qui conta solo che il dettaglio non vada in errore
vi.mock('photoswipe', () => ({ default: class { init() {} on() {} destroy() {} } }))
vi.mock('photoswipe/style.css', () => ({}))

import PhotoDetail from '../src/components/PhotoDetail.vue'
import { libraryVersion, viewer } from '../src/lib/store'
import { toasts } from '../src/lib/toast'
import type { PhotoDetail as Detail, PhotoSummary } from '../src/lib/types'

const summary = (n: number): PhotoSummary => ({
  uid: `p${n}`, type: 'image', title: `Foto ${n}`, caption: '', taken_at_local: '2024-05-01T10:00:00', width: 1500, height: 1000,
  duration: 0, main_color: 'Rosso', quality: 3, hash: String(n).repeat(40).slice(0, 40), favorite: false, private: false, archived: false,
})

const detail = (uid = 'p2', extra: Partial<Detail> = {}): Detail => ({
  uid, type: 'image', title: 'Moto rossa', title_source: 'llm', caption: 'Una Ducati', caption_source: 'llm',
  taken_at: '2024-05-01T08:00:00Z', time_zone: '', taken_source: 'meta', lat: null, lng: null,
  camera: { make: 'Nikon', model: 'D850' }, lens: '', iso: 200, exposure: '1/250', f_number: 2.8, focal_length: 85,
  width: 1500, height: 1000, duration: 0, rating: 0, favorite: false, private: false, archived: false, quality: 4, review: false,
  main_color: 'Rosso', colors: ['Rosso'], hash: 'a'.repeat(40), seq: 2,
  files: [{ name: '0002_moto.jpg', original_name: 'IMG_1.jpg', path: 'D:/Foto/0002_moto.jpg', media_type: 'jpeg', size: 2048,
    width: 1500, height: 1000, codec: '', primary: true, missing: false }],
  labels: [{ name: 'Moto', slug: 'moto', category: 'Veicoli', confidence: 91, source: 'onnx' }],
  keywords: ['ducati'], people: [], albums: [], ...extra,
})

let router: Router

async function open(uid: string | null = 'p2') {
  router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/:rest(.*)*', component: { template: '<div />' } }] })
  await router.push(uid ? `/?photo=${uid}` : '/')
  await router.isReady()
  const wrapper = track(mount(PhotoDetail, { props: { uid }, global: { plugins: [router] }, attachTo: document.body }))
  await flushPromises()
  return wrapper
}

const byLabel = (label: string) => document.body.querySelector<HTMLElement>(`[aria-label="${label}"]`)

/** Apre il menu con l'etichetta data e restituisce la voce con quel testo. */
async function menuItem(menu: string, text: string): Promise<HTMLElement | undefined> {
  byLabel(menu)!.click()
  await flushPromises()
  return [...document.body.querySelectorAll<HTMLElement>('.menu-item')].find((el) => el.textContent?.includes(text))
}

// il "server" ricorda le modifiche: dopo un salvataggio il dettaglio viene riletto (libraryVersion) e deve restare coerente
const server = new Map<string, Detail>()
const stored = (uid: string): Detail => server.get(uid) ?? detail(uid)

beforeEach(() => {
  Object.values(api).forEach((fn) => fn.mockReset())
  server.clear()
  api.photo.mockImplementation(async (uid: string) => stored(uid))
  api.similar.mockResolvedValue({ ready: true, hits: [] })
  api.albums.mockResolvedValue([{ uid: 'a1', type: 'album', slug: 'viaggio', title: 'Viaggio', description: '', smart: false, filter: '',
    favorite: false, private: false, photo_count: 3, cover: null }])
  api.patchPhoto.mockImplementation(async (uid: string, body: Record<string, unknown>) => {
    const next = { ...stored(uid), ...body }
    server.set(uid, next)
    return next
  })
  api.batch.mockResolvedValue({ updated: 1 })
  viewer.photos = [summary(1), summary(2), summary(3)]
  libraryVersion.value = 0
  mockMatchMedia(() => false)
})

afterEach(() => {
  while (mounted.length) mounted.pop()!.unmount()
})

describe('PhotoDetail', () => {
  it('carica il dettaglio e mostra titolo, etichette, dati e file', async () => {
    await open()
    expect(api.photo).toHaveBeenCalledWith('p2')
    expect((document.getElementById('d-title') as HTMLInputElement).value).toBe('Moto rossa')
    expect(document.body.textContent).toContain('Moto')
    expect(document.body.textContent).toContain('Nikon D850')
    expect(document.body.textContent).toContain('0002_moto.jpg')
    expect(document.body.textContent).toContain('era IMG_1.jpg')
  })

  it('salva il titolo modificato come modifica manuale', async () => {
    await open()
    const input = document.getElementById('d-title') as HTMLInputElement
    input.value = 'Panigale V4'
    input.dispatchEvent(new Event('input'))
    input.dispatchEvent(new Event('change'))
    await flushPromises()
    expect(api.patchPhoto).toHaveBeenCalledWith('p2', { title: 'Panigale V4' })
    expect(libraryVersion.value).toBe(1)
  })

  it('il tasto preferita invia il valore opposto e riflette lo stato', async () => {
    await open()
    byLabel('Preferita')!.click()
    await flushPromises()
    expect(api.patchPhoto).toHaveBeenCalledWith('p2', { favorite: true })
    expect(byLabel('Preferita')!.getAttribute('aria-pressed')).toBe('true')
  })

  it('il tasto F alterna il preferito ma non mentre si scrive in un campo', async () => {
    await open()
    document.getElementById('d-title')!.dispatchEvent(new KeyboardEvent('keydown', { key: 'f', bubbles: true }))
    await flushPromises()
    expect(api.patchPhoto).not.toHaveBeenCalled()
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'f' }))
    await flushPromises()
    expect(api.patchPhoto).toHaveBeenCalledWith('p2', { favorite: true })
  })

  it('le frecce scorrono la griglia e si fermano ai bordi', async () => {
    const middle = await open('p2')
    expect((byLabel('Foto precedente') as HTMLButtonElement).disabled).toBe(false)
    expect((byLabel('Foto successiva') as HTMLButtonElement).disabled).toBe(false)
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight' }))
    await flushPromises()
    expect(router.currentRoute.value.query.photo).toBe('p3')
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowLeft' }))
    await flushPromises()
    expect(router.currentRoute.value.query.photo).toBe('p1')
    middle.unmount()
    mounted.pop()
    document.body.innerHTML = ''

    const first = await open('p1')
    expect((byLabel('Foto precedente') as HTMLButtonElement).disabled).toBe(true)
    first.unmount()
    mounted.pop()
    document.body.innerHTML = ''
    await open('p3')
    expect((byLabel('Foto successiva') as HTMLButtonElement).disabled).toBe(true)
  })

  it('aggiunge e rimuove parole chiave ed etichette', async () => {
    await open()
    const keyword = byLabel('Nuova parola chiave') as HTMLInputElement
    keyword.value = ' moto gp '
    keyword.dispatchEvent(new Event('input'))
    keyword.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' }))
    await flushPromises()
    expect(api.patchPhoto).toHaveBeenCalledWith('p2', { keywords_add: ['moto gp'] })
    byLabel('Rimuovi ducati')!.click()
    await flushPromises()
    expect(api.patchPhoto).toHaveBeenCalledWith('p2', { keywords_remove: ['ducati'] })
    byLabel("Rimuovi l'etichetta Moto")!.click()
    await flushPromises()
    expect(api.patchPhoto).toHaveBeenCalledWith('p2', { labels_remove: ['moto'] })
  })

  it('una parola chiave vuota non genera richieste', async () => {
    await open()
    const keyword = byLabel('Nuova parola chiave') as HTMLInputElement
    keyword.value = '   '
    keyword.dispatchEvent(new Event('input'))
    keyword.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' }))
    await flushPromises()
    expect(api.patchPhoto).not.toHaveBeenCalled()
  })

  it('mostra le foto simili con la percentuale e ci si sposta con un clic', async () => {
    api.similar.mockResolvedValue({ ready: true, hits: [
      { photo: summary(3), score: 0.82, cosine: 0.9, reasons: { Soggetto: ['stessa etichetta: moto'] } },
    ] })
    await open()
    const sim = byLabel('Foto simile al 82%')
    expect(sim).not.toBeNull()
    sim!.click()
    await flushPromises()
    expect(router.currentRoute.value.query.photo).toBe('p3')
  })

  it('una foto non trovata mostra l\'errore senza rompere il dialogo', async () => {
    api.photo.mockRejectedValue(new Error('foto non trovata'))
    await open()
    expect(document.body.querySelector('[role="alert"]')?.textContent).toContain('Foto non disponibile')
  })

  it('con le persone mostra i volti e permette di assegnare un nome', async () => {
    api.photo.mockImplementation(async (uid: string) => detail(uid, { people: [
      { marker: 'm1', name: '', subject: null, face: 'f1', x: 0.1, y: 0.1, w: 0.2, h: 0.2 },
      { marker: 'm2', name: 'Anna Rossi', subject: 's1', face: 'f2', x: 0.5, y: 0.1, w: 0.2, h: 0.2 },
    ] }))
    api.nameMarker.mockResolvedValue({})
    await open()
    expect(document.body.textContent).toContain('Anna Rossi')
    const input = byLabel('Nome del volto m1') as HTMLInputElement
    input.value = 'Luca'
    input.dispatchEvent(new Event('input'))
    input.dispatchEvent(new Event('change'))
    await flushPromises()
    expect(api.nameMarker).toHaveBeenCalledWith('m1', 'Luca')
    byLabel('Non è un volto')!.click()
    await flushPromises()
    expect(api.rejectMarker).toHaveBeenCalledWith('m1')
  })

  it('aggiunge la foto a un album dal menu', async () => {
    await open()
    const item = await menuItem('Aggiungi a un album', 'Viaggio')
    expect(item).toBeTruthy()
    item!.click()
    await flushPromises()
    expect(api.batch).toHaveBeenCalledWith(['p2'], 'add_to_album', 'a1')
  })

  it('archivia dal menu e chiude il dettaglio', async () => {
    await open()
    const item = await menuItem('Altre azioni', 'Archivia')
    expect(item).toBeTruthy()
    item!.click()
    await flushPromises()
    expect(api.batch).toHaveBeenCalledWith(['p2'], 'archive')
    expect(router.currentRoute.value.query.photo).toBeUndefined()
  })

  it('elimina dal menu solo dopo la conferma e passa alla foto successiva', async () => {
    api.removePhotos.mockResolvedValue({ removed: 1, files: 1, mode: 'catalog', failed: [] })
    await open()
    const item = await menuItem('Altre azioni', 'Elimina')
    expect(item).toBeTruthy()
    item!.click()
    await flushPromises()
    expect(api.removePhotos).not.toHaveBeenCalled()
    expect(document.body.querySelector('[data-test="remove-text"]')?.textContent).toContain('Tolgo questa foto da Mosaic')
    document.body.querySelector<HTMLElement>('[data-test="remove-confirm"]')!.click()
    await flushPromises()
    expect(api.removePhotos).toHaveBeenCalledWith(['p2'])
    expect(router.currentRoute.value.query.photo).toBe('p3')
    expect(libraryVersion.value).toBeGreaterThan(0)
  })

  it('se eliminare fallisce (niente Cestino) resta sulla stessa foto', async () => {
    api.removePhotos.mockResolvedValue({ removed: 0, files: 0, mode: 'trash', failed: [{ uid: 'p2', error: 'unità senza Cestino' }] })
    await open()
    ;(await menuItem('Altre azioni', 'Elimina'))!.click()
    await flushPromises()
    document.body.querySelector<HTMLElement>('[data-test="remove-confirm"]')!.click()
    await flushPromises()
    expect(router.currentRoute.value.query.photo).toBe('p2')
    expect(toasts.value.some((t) => t.tone === 'error' && t.text.includes('Cestino'))).toBe(true)
  })

  it('un errore di salvataggio non cambia lo stato mostrato', async () => {
    api.patchPhoto.mockRejectedValue(new Error('disco pieno'))
    await open()
    byLabel('Preferita')!.click()
    await flushPromises()
    expect(byLabel('Preferita')!.getAttribute('aria-pressed')).toBe('false')
    expect(libraryVersion.value).toBe(0)
  })

  it('copia il percorso del file negli appunti e avvisa se non può', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
    await open()
    byLabel('Copia il percorso di 0002_moto.jpg')!.click()
    await flushPromises()
    expect(writeText).toHaveBeenCalledWith('D:/Foto/0002_moto.jpg')
    expect(toasts.value.at(-1)?.text).toBe('Percorso copiato')

    writeText.mockRejectedValue(new Error('negato'))
    byLabel('Copia il percorso di 0002_moto.jpg')!.click()
    await flushPromises()
    expect(toasts.value.at(-1)?.tone).toBe('error')
  })

  it('senza uid il dialogo resta chiuso e non chiede nulla', async () => {
    await open(null)
    expect(api.photo).not.toHaveBeenCalled()
    expect(document.body.querySelector('dialog[open]')).toBeNull()
  })
})
