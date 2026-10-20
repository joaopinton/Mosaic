import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mockMatchMedia } from './setup'

const api = vi.hoisted(() => ({ removePhotos: vi.fn(), batch: vi.fn(), albums: vi.fn(), status: vi.fn(), createAlbum: vi.fn() }))
vi.mock('../src/lib/api', async (original) => ({ ...(await original<typeof import('../src/lib/api')>()), api }))

import RemovePhotosDialog from '../src/components/RemovePhotosDialog.vue'
import SelectionBar from '../src/components/SelectionBar.vue'
import { clearSelection, currentAlbum, libraryVersion, selected, status } from '../src/lib/store'
import { toasts } from '../src/lib/toast'
import type { Status } from '../src/lib/types'

const baseStatus = (mode: 'catalog' | 'trash'): Status => ({
  first_run: false, library_roots: ['D:/Foto'], photos: 3, videos: 0, analyzed: 3, people: 0, albums: 0, jobs: {}, busy: false,
  assets: {}, ai_ready: true, llm_ready: false, caption_auto: false, rename_auto: true, delete_mode: mode,
})

const mounted: { unmount: () => void }[] = []
const track = <T extends { unmount: () => void }>(w: T): T => (mounted.push(w), w)
const confirmButton = () => document.body.querySelector<HTMLElement>('[data-test="remove-confirm"]')!

beforeEach(() => {
  mockMatchMedia(() => false)
  Object.values(api).forEach((fn) => fn.mockReset())
  api.status.mockResolvedValue(baseStatus('catalog'))
  api.albums.mockResolvedValue([])
  status.value = baseStatus('catalog')
  libraryVersion.value = 0
  toasts.value = []
  currentAlbum.value = null
  clearSelection()
})

afterEach(() => {
  while (mounted.length) mounted.pop()!.unmount()
})

describe('RemovePhotosDialog', () => {
  it('modo Chiarezza: dice che i file restano sul disco e toglie solo dal catalogo', async () => {
    api.removePhotos.mockResolvedValue({ removed: 2, files: 2, mode: 'catalog', failed: [] })
    const wrapper = track(mount(RemovePhotosDialog, { props: { open: true, uids: ['a', 'b'] }, attachTo: document.body }))
    expect(document.body.textContent).toContain('Togliere dal catalogo?')
    expect(document.body.textContent).toContain('Tolgo 2 foto da Mosaic')
    expect(document.body.textContent).toContain('I file originali restano sul disco')
    expect(confirmButton().textContent).toContain('Togli dal catalogo')
    expect(api.removePhotos).not.toHaveBeenCalled()   // nessuna azione prima della conferma

    confirmButton().click()
    await flushPromises()

    expect(api.removePhotos).toHaveBeenCalledWith(['a', 'b'])
    expect(wrapper.emitted('done')?.[0][0]).toMatchObject({ removed: 2 })
    expect(libraryVersion.value).toBe(1)
    expect(toasts.value.at(-1)?.text).toBe('2 foto tolte dal catalogo')
  })

  it('modo Pulizia: parla del Cestino di Windows e lo dice anche nel pulsante', async () => {
    status.value = baseStatus('trash')
    api.removePhotos.mockResolvedValue({ removed: 1, files: 2, mode: 'trash', failed: [] })
    track(mount(RemovePhotosDialog, { props: { open: true, uids: ['a'] }, attachTo: document.body }))
    expect(document.body.textContent).toContain('Spostare nel Cestino?')
    expect(document.body.textContent).toContain('Sposto questa foto nel Cestino di Windows')
    expect(confirmButton().textContent).toContain('Sposta nel Cestino')
    confirmButton().click()
    await flushPromises()
    expect(toasts.value.at(-1)?.text).toBe('1 foto spostate nel Cestino')
  })

  it('le foto non eliminate (niente Cestino) compaiono come errore e non spariscono', async () => {
    api.removePhotos.mockResolvedValue({ removed: 0, files: 0, mode: 'trash', failed: [{ uid: 'a', error: 'unità senza Cestino' }] })
    const wrapper = track(mount(RemovePhotosDialog, { props: { open: true, uids: ['a'] }, attachTo: document.body }))
    confirmButton().click()
    await flushPromises()
    const last = toasts.value.at(-1)!
    expect(last.tone).toBe('error')
    expect(last.text).toContain('non eliminate')
    expect(last.text).toContain('unità senza Cestino')
    expect(wrapper.emitted('done')?.[0][0]).toMatchObject({ removed: 0 })
  })

  it('un errore del server resta un errore e il dialog non segnala «fatto»', async () => {
    api.removePhotos.mockRejectedValue(new Error('database occupato'))
    const wrapper = track(mount(RemovePhotosDialog, { props: { open: true, uids: ['a'] }, attachTo: document.body }))
    confirmButton().click()
    await flushPromises()
    expect(wrapper.emitted('done')).toBeUndefined()
    expect(toasts.value.at(-1)?.tone).toBe('error')
  })

  it('Annulla chiude senza chiamare il server', async () => {
    const wrapper = track(mount(RemovePhotosDialog, { props: { open: true, uids: ['a'] }, attachTo: document.body }))
    const cancel = [...document.body.querySelectorAll<HTMLElement>('button')].find((b) => b.textContent?.trim() === 'Annulla')!
    cancel.click()
    await flushPromises()
    expect(wrapper.emitted('close')).toHaveLength(1)
    expect(api.removePhotos).not.toHaveBeenCalled()
  })
})

describe('SelectionBar: Elimina', () => {
  const barButton = (test: string) => document.body.querySelector<HTMLElement>(`[data-test="${test}"]`)

  it('«Elimina…» apre la conferma con il numero di foto selezionate e poi svuota la selezione', async () => {
    api.removePhotos.mockResolvedValue({ removed: 2, files: 2, mode: 'catalog', failed: [] })
    selected.value = ['a', 'b']
    track(mount(SelectionBar, { attachTo: document.body }))
    await flushPromises()
    barButton('bar-remove')!.click()
    await flushPromises()
    expect(document.body.textContent).toContain('Tolgo 2 foto da Mosaic')
    expect(api.removePhotos).not.toHaveBeenCalled()
    confirmButton().click()
    await flushPromises()
    expect(api.removePhotos).toHaveBeenCalledWith(['a', 'b'])
    expect(selected.value).toEqual([])
  })

  it('«Togli dall\'album» c\'è solo dentro un album manuale e non elimina nulla dal catalogo', async () => {
    api.batch.mockResolvedValue({ changed: 1 })
    selected.value = ['a']
    track(mount(SelectionBar, { attachTo: document.body }))
    await flushPromises()
    expect(barButton('bar-out-of-album')).toBeNull()

    currentAlbum.value = { uid: 'al1', title: 'Vacanze' }
    await flushPromises()
    barButton('bar-out-of-album')!.click()
    await flushPromises()
    expect(api.batch).toHaveBeenCalledWith(['a'], 'remove_from_album', 'al1')
    expect(api.removePhotos).not.toHaveBeenCalled()
  })
})
