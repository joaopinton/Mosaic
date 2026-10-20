import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mockMatchMedia } from './setup'

const api = vi.hoisted(() => ({
  libraryRoots: vi.fn(), unlinkRoot: vi.fn(), excluded: vi.fn(), restoreExcluded: vi.fn(), putConfig: vi.fn(), index: vi.fn(),
  renameBatches: vi.fn(), config: vi.fn(), status: vi.fn(), jobs: vi.fn(),
}))
vi.mock('../src/lib/api', async (original) => ({ ...(await original<typeof import('../src/lib/api')>()), api }))

import LibraryPage from '../src/pages/LibraryPage.vue'
import { libraryVersion, status } from '../src/lib/store'
import { toasts } from '../src/lib/toast'
import type { ExcludedFile, LibraryRoots, Status } from '../src/lib/types'

const baseStatus = (roots: string[]): Status => ({
  first_run: false, library_roots: roots, photos: 120, videos: 0, analyzed: 100, people: 2, albums: 4, jobs: {}, busy: false,
  assets: {}, ai_ready: true, llm_ready: false, caption_auto: false, rename_auto: true, delete_mode: 'catalog',
})

const excludedFile = (n: number, extra: Partial<ExcludedFile> = {}): ExcludedFile => ({
  sha1: `${n}`.repeat(40).slice(0, 40), name: `foto${n}.jpg`, path: `D:\\Foto\\foto${n}.jpg`, size: 1000, excluded_at: '2026-10-19T10:00:00Z',
  exists: true, ...extra,
})

const mounted: { unmount: () => void }[] = []

async function mountPage(roots: LibraryRoots, excluded: ExcludedFile[] = [], configured = roots.roots.filter((r) => r.linked).map((r) => r.path)) {
  api.libraryRoots.mockResolvedValue(roots)
  api.excluded.mockResolvedValue(excluded)
  status.value = baseStatus(configured)
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: LibraryPage }] })
  await router.push('/')
  await router.isReady()
  const wrapper = mount(LibraryPage, { global: { plugins: [router] }, attachTo: document.body })
  mounted.push(wrapper)
  await flushPromises()
  return wrapper
}

const click = async (selector: string, index = 0): Promise<void> => {
  document.body.querySelectorAll<HTMLElement>(selector)[index].click()
  await flushPromises()
}

beforeEach(() => {
  mockMatchMedia(() => false)
  Object.values(api).forEach((fn) => fn.mockReset())
  api.renameBatches.mockResolvedValue([])
  api.config.mockResolvedValue({ rename: { pattern: '{seq:04}_{slug}' } })
  api.status.mockResolvedValue(baseStatus([]))
  api.jobs.mockResolvedValue([])
  api.unlinkRoot.mockResolvedValue({ job: 5 })
  api.putConfig.mockResolvedValue({})
  api.index.mockResolvedValue({ job: 6 })
  api.restoreExcluded.mockResolvedValue({ restored: 1, job: 7 })
  libraryVersion.value = 0
  toasts.value = []
})

afterEach(() => {
  while (mounted.length) mounted.pop()!.unmount()
})

describe('Libreria: scollegare una cartella', () => {
  const linked: LibraryRoots = { roots: [{ path: 'D:\\Foto', linked: true, photos: 120 }], unlinked: [] }

  it('la × non scollega subito: prima chiede conferma e dice cosa succede ai file e ai dati manuali', async () => {
    await mountPage(linked)
    await click('[data-test="root-unlink"]')
    expect(api.unlinkRoot).not.toHaveBeenCalled()
    const text = document.body.querySelector('[data-test="unlink-text"]')!.textContent!
    expect(text).toContain('D:\\Foto')
    expect(text).toContain('120 foto')
    expect(document.body.textContent).toContain('I file sul disco non vengono toccati')
    expect(document.body.textContent).toContain('album manuali restano nel backup')
  })

  it('confermando parte lo scollegamento della cartella scelta', async () => {
    await mountPage(linked)
    await click('[data-test="root-unlink"]')
    await click('[data-test="unlink-confirm"]')
    expect(api.unlinkRoot).toHaveBeenCalledWith('D:\\Foto')
    expect(toasts.value.some((t) => t.text.includes('i file restano dove sono'))).toBe(true)
  })

  it('una cartella già fuori dalla configurazione ma ancora nel catalogo si può togliere del tutto', async () => {
    await mountPage({ roots: [{ path: 'E:\\Vecchie', linked: false, photos: 30 }], unlinked: [] }, [], [])
    expect(document.body.textContent).toContain('non più collegata')
    await click('[data-test="root-unlink"]')
    expect(document.body.textContent).toContain('Togliere la cartella dal catalogo?')
    await click('[data-test="unlink-confirm"]')
    expect(api.unlinkRoot).toHaveBeenCalledWith('E:\\Vecchie')
  })

  it('Annulla non tocca nulla', async () => {
    await mountPage(linked)
    await click('[data-test="root-unlink"]')
    const cancel = [...document.body.querySelectorAll<HTMLElement>('dialog button')].find((b) => b.textContent?.trim() === 'Annulla')!
    cancel.click()
    await flushPromises()
    expect(api.unlinkRoot).not.toHaveBeenCalled()
  })
})

describe('Libreria: ricollegare una cartella', () => {
  it('«Ricollega» rimette la cartella nella configurazione e rilancia l\'indicizzazione', async () => {
    await mountPage({ roots: [], unlinked: ['D:\\Foto'] }, [], [])
    expect(document.body.querySelector('[data-test="unlinked"]')!.textContent).toContain('D:\\Foto')
    await click('[data-test="root-relink"]')
    expect(api.putConfig).toHaveBeenCalledWith({ library_roots: ['D:\\Foto'] })
    expect(api.index).toHaveBeenCalledWith(false)
  })

  it('senza cartelle scollegate non compare nessun «Ricollega»', async () => {
    await mountPage({ roots: [{ path: 'D:\\Foto', linked: true, photos: 1 }], unlinked: [] })
    expect(document.body.querySelector('[data-test="root-relink"]')).toBeNull()
  })
})

describe('Libreria: foto tolte dal catalogo', () => {
  const none: LibraryRoots = { roots: [], unlinked: [] }

  it('la sezione compare solo se ci sono foto escluse', async () => {
    await mountPage(none, [])
    expect(document.body.querySelector('[data-test="excluded"]')).toBeNull()
  })

  it('elenca le foto, segnala i file non trovati e rimette nel catalogo una sola foto', async () => {
    const files = [excludedFile(1), excludedFile(2, { exists: false })]
    await mountPage({ roots: [{ path: 'D:\\Foto', linked: true, photos: 1 }], unlinked: [] }, files)
    const section = document.body.querySelector('[data-test="excluded"]')!
    expect(section.textContent).toContain('foto1.jpg')
    expect(section.textContent).toContain('file non trovato')
    await click('[data-test="excluded-restore"]', 1)
    expect(api.restoreExcluded).toHaveBeenCalledWith([files[1].sha1])
  })

  it('«Rimetti tutte» non passa nessun elenco', async () => {
    await mountPage({ roots: [{ path: 'D:\\Foto', linked: true, photos: 1 }], unlinked: [] }, [excludedFile(1)])
    await click('[data-test="excluded-restore-all"]')
    expect(api.restoreExcluded).toHaveBeenCalledWith(undefined)
  })
})
