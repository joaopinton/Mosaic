import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mockMatchMedia } from './setup'

const api = vi.hoisted(() => ({ dropInspect: vi.fn(), dropAdd: vi.fn(), uploadFinish: vi.fn(), uploadCancel: vi.fn(), status: vi.fn(), jobs: vi.fn() }))
const upload = vi.hoisted(() => vi.fn())
vi.mock('../src/lib/api', async (original) => ({ ...(await original<typeof import('../src/lib/api')>()), api, uploadFile: upload }))

import DropDialog from '../src/components/DropDialog.vue'
import DropZone from '../src/components/DropZone.vue'
import { collectFiles, describeItem, summarize, uploadAll } from '../src/lib/drop'
import * as store from '../src/lib/store'
import type { DropItem } from '../src/lib/types'

const item = (extra: Partial<DropItem>): DropItem => ({
  path: 'D:\\Foto\\Festa', name: 'Festa', kind: 'folder', size: 5_000_000, media: 12, in_library: false, contains_library: false,
  folders: 3, zips: 0, dest_name: 'Festa', ...extra,
})

beforeEach(() => {
  mockMatchMedia(() => false)
  Object.values(api).forEach((fn) => fn.mockReset())
  upload.mockReset()
  store.uploads.value = []
  store.sidebar.panel = null
  api.jobs.mockResolvedValue([])
  api.status.mockResolvedValue({ library_roots: ['D:\\Libreria'] })
})
afterEach(() => {
  document.body.innerHTML = ''
  delete (window as { pywebview?: unknown }).pywebview
})

function dragEvent(type: string): DragEvent {
  const event = new Event(type, { bubbles: true, cancelable: true }) as DragEvent
  Object.defineProperty(event, 'dataTransfer', { value: { types: ['Files'], items: [], files: [], dropEffect: 'none' } })
  return event
}

describe('dialogo di conferma', () => {
  it('con i percorsi collega le cartelle dove sono, oppure le copia', async () => {
    api.dropInspect.mockResolvedValue({ items: [item({}), item({ path: 'D:\\x.txt', name: 'x.txt', kind: 'unsupported' })], dest: 'D:\\Libreria' })
    api.dropAdd.mockResolvedValue({ linked: [], jobs: [5] })
    const wrapper = mount(DropDialog, { props: { paths: ['D:\\Foto\\Festa', 'D:\\x.txt'], files: null }, attachTo: document.body })
    await flushPromises()
    const text = document.body.textContent ?? ''
    expect(text).toContain('12 foto e video')
    expect(text).toContain('lo salto')
    expect(text).toContain('Collega dove sono')
    const copy = document.querySelector<HTMLInputElement>('input[value="copy"]')!
    copy.click()
    await flushPromises()
    document.querySelector<HTMLButtonElement>('[data-test="drop-confirm"]')!.click()
    await flushPromises()
    expect(api.dropAdd).toHaveBeenCalledWith(['D:\\Foto\\Festa', 'D:\\x.txt'], 'copy')
    expect(store.sidebar.panel).toBe('activity')
    expect(wrapper.emitted('close')).toBeTruthy()
    wrapper.unmount()
  })

  it('senza una cartella della libreria non si possono fare copie', async () => {
    api.dropInspect.mockResolvedValue({ items: [item({ kind: 'media', name: 'a.jpg', media: 1 })], dest: null })
    const wrapper = mount(DropDialog, { props: { paths: ['D:\\a.jpg'], files: null }, attachTo: document.body })
    await flushPromises()
    expect(document.body.textContent).toContain('Prima collega una cartella')
    expect(document.querySelector<HTMLButtonElement>('[data-test="drop-confirm"]')!.disabled).toBe(true)
    wrapper.unmount()
  })

  it('nel browser carica i file e mostra il riepilogo', async () => {
    api.uploadFinish.mockResolvedValue({ job: 9 })
    upload.mockImplementation(async (_b: string, _p: string, file: Blob, onProgress: (n: number) => void) => onProgress(file.size))
    const files = [{ file: new File(['xx'], 'a.jpg'), path: 'Gita/a.jpg' }, { file: new File(['yyyy'], 'b.zip'), path: 'b.zip' }]
    const wrapper = mount(DropDialog, { props: { paths: null, files }, attachTo: document.body })
    await flushPromises()
    expect(document.body.textContent).toContain('2 file')
    expect(document.body.textContent).toContain('1 zip da estrarre')
    document.querySelector<HTMLButtonElement>('[data-test="drop-confirm"]')!.click()
    await flushPromises()
    expect(upload).toHaveBeenCalledTimes(2)
    expect(api.uploadFinish).toHaveBeenCalled()
    wrapper.unmount()
  })
})

describe('caricamento', () => {
  it('somma l\'avanzamento dei file e segnala gli errori annullando il lotto', async () => {
    api.uploadFinish.mockResolvedValue({ job: 3 })
    const seen: number[] = []
    upload.mockImplementation(async (_b: string, _p: string, file: Blob, onProgress: (n: number) => void) => {
      onProgress(file.size / 2)
      seen.push(store.uploads.value[0].loaded)
    })
    const job = await uploadAll([{ file: new File(['1234'], 'a.jpg'), path: 'a.jpg' }, { file: new File(['12'], 'b.jpg'), path: 'b.jpg' }])
    expect(job).toBe(3)
    expect(seen).toEqual([2, 5])
    expect(store.uploads.value[0].state).toBe('done')

    upload.mockRejectedValue(new Error('disco pieno'))
    api.uploadCancel.mockResolvedValue(undefined)
    await expect(uploadAll([{ file: new File(['1'], 'c.jpg'), path: 'c.jpg' }])).rejects.toThrow('disco pieno')
    expect(store.uploads.value.at(-1)).toMatchObject({ state: 'failed', error: 'disco pieno' })
    expect(api.uploadCancel).toHaveBeenCalled()
  })

  it('tiene solo foto, video, XMP e zip, anche senza voci di cartella', async () => {
    const transfer = { items: [], files: [new File(['x'], 'a.JPG'), new File(['x'], 'doc.pdf'), new File(['x'], 'z.zip')] } as unknown as DataTransfer
    const files = await collectFiles(transfer)
    expect(files.map((f) => f.path)).toEqual(['a.JPG', 'z.zip'])
    expect(summarize(files)).toMatchObject({ photos: 1, zips: 1, folders: 0 })
  })
})

describe('zona di rilascio', () => {
  it('mostra il velo mentre trascini file e lo toglie quando esci', async () => {
    const wrapper = mount(DropZone, { attachTo: document.body })
    window.dispatchEvent(dragEvent('dragenter'))
    await flushPromises()
    expect(document.querySelector('[data-test="drop-veil"]')).not.toBeNull()
    window.dispatchEvent(dragEvent('dragleave'))
    await flushPromises()
    await new Promise((r) => setTimeout(r, 0))
    expect(document.body.textContent).not.toContain('Rilascia per aggiungere')
    wrapper.unmount()
  })

  it('nell\'app desktop usa i percorsi veri che arrivano da pywebview', async () => {
    ;(window as { pywebview?: unknown }).pywebview = {}
    api.dropInspect.mockResolvedValue({ items: [item({})], dest: 'D:\\Libreria' })
    const wrapper = mount(DropZone, { attachTo: document.body })
    window.dispatchEvent(dragEvent('drop'))
    await flushPromises()
    window.mosaicDrop?.(['D:\\Foto\\Festa'])
    await flushPromises()
    expect(api.dropInspect).toHaveBeenCalledWith(['D:\\Foto\\Festa'])
    expect(document.body.textContent).toContain('Collega dove sono')
    wrapper.unmount()
  })
})

describe('cosa succederà (D-31)', () => {
  it('una cartella collegata: le sottocartelle diventano cartelle, gli zip si estraggono accanto a sé', () => {
    const { summary, plan } = describeItem(item({ zips: 2 }), 'link')
    expect(summary).toContain('12 foto e video')
    expect(plan).toContain('Si collega dov\'è')
    expect(plan).toContain('compaiono 3 cartelle')
    expect(plan).toContain('I 2 zip che contiene si estraggono accanto a sé')
    expect(plan).toContain('gli zip restano')
  })

  it('una cartella copiata va in una cartella con il suo nome, gli zip dentro la copia', () => {
    const { plan } = describeItem(item({ zips: 1 }), 'copy')
    expect(plan).toContain('Si copia nella libreria in «Festa», con le stesse sottocartelle')
    expect(plan).toContain('Lo zip che contiene si estrae nella copia')
    expect(describeItem(item({ contains_library: true }), 'link').plan).toContain('Contiene una cartella della libreria, quindi si copia')
  })

  it('uno zip si estrae in una cartella con il suo nome; foto sciolte per data', () => {
    const zip = describeItem(item({ kind: 'zip', name: 'Gita.zip', dest_name: 'Gita', folders: 1 }), 'link')
    expect(zip.plan).toBe('Si estrae nella libreria in «Gita», con le sue sottocartelle. In «Cartelle» compare una cartella.')
    expect(describeItem(item({ kind: 'media', media: 1 }), 'link').plan).toBe('Si copia nella libreria, per data.')
    expect(describeItem(item({ kind: 'unsupported' }), 'link').plan).toBe('')
  })

  it('il dialogo mostra il piano e lo aggiorna quando si sceglie di copiare', async () => {
    api.dropInspect.mockResolvedValue({ items: [item({ zips: 1 })], dest: 'D:\\Libreria' })
    const wrapper = mount(DropDialog, { props: { paths: ['D:\\Foto\\Festa'], files: null }, attachTo: document.body })
    await flushPromises()
    const plan = (): string => document.querySelector('[data-test="drop-plan"]')?.textContent ?? ''
    expect(plan()).toContain('accanto a sé')
    expect(document.body.textContent).toContain("è l'unica cosa che Mosaic scrive lì")
    document.querySelector<HTMLInputElement>('input[value="copy"]')!.click()
    await flushPromises()
    expect(plan()).toContain('nella copia')
    wrapper.unmount()
  })
})
