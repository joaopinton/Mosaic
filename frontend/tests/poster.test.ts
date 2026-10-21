import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mockMatchMedia } from './setup'

const api = vi.hoisted(() => ({ posterLayout: vi.fn(), createPoster: vi.fn(), revealPoster: vi.fn(), openPoster: vi.fn(), jobs: vi.fn() }))
vi.mock('../src/lib/api', async (original) => ({ ...(await original<typeof import('../src/lib/api')>()), api }))

import PosterDialog from '../src/components/PosterDialog.vue'
import * as store from '../src/lib/store'
import type { Job, PosterLayout } from '../src/lib/types'

const layout = (): PosterLayout => ({
  width_mm: 297, height_mm: 420, pixels: [2339, 3307], dpi: 200, line: 0.012, ink: '#141414',
  cells: [
    { x: 0, y: 0, w: 0.5, h: 1, uid: 'p1', hash: 'h1', color: null, crop: [0.25, 0, 0.75, 1], text: null },
    { x: 0.5, y: 0, w: 0.5, h: 0.5, uid: null, hash: null, color: '#d52b1e', crop: [0, 0, 1, 1],
      text: { fg: '#fbf8f1', label: 'DATA', lines: ['13–14', 'settembre 2025'], value: 0.2, label_size: 0.08, pad: 0.09 } },
    { x: 0.5, y: 0.5, w: 0.5, h: 0.5, uid: 'p2', hash: 'h2', color: null, crop: [0, 0, 1, 1], text: null },
  ],
  font: 'bahnschrift',
  facts: [{ label: '', value: 'Festa' }, { label: 'Data', value: '13–14 settembre 2025' }, { label: 'Foto', value: '2' }],
})
const job = (extra: Partial<Job>): Job => ({ id: 7, type: 'poster', state: 'running', progress: 0, message: '', error: '', created_at: '',
  finished_at: null, done: 0, total: 0, ...extra })

beforeEach(() => {
  vi.useFakeTimers()
  mockMatchMedia(() => false)
  Object.values(api).forEach((fn) => fn.mockReset())
  api.posterLayout.mockResolvedValue(layout())
  api.jobs.mockResolvedValue([])
  store.jobs.value = []
})
afterEach(() => { document.body.innerHTML = '' })

const rows = () => [...document.querySelectorAll<HTMLElement>('[data-test="poster-text"]')]
const lastOptions = () => api.posterLayout.mock.lastCall![1]

async function change(input: HTMLInputElement, value: string) {
  input.value = value
  input.dispatchEvent(new Event('change'))
  await settle()
}

async function settle() {
  await vi.advanceTimersByTimeAsync(300)
  await flushPromises()
}

async function mountDialog() {
  const wrapper = mount(PosterDialog, { props: { open: true, uids: ['p1', 'p2'], title: 'Festa', album: 'a1' }, attachTo: document.body })
  await vi.advanceTimersByTimeAsync(300)
  await flushPromises()
  return wrapper
}

describe('poster a mosaico', () => {
  it('chiede l\'impaginazione e disegna l\'anteprima con i ritagli', async () => {
    const wrapper = await mountDialog()
    expect(api.posterLayout).toHaveBeenCalledWith(['p1', 'p2'], expect.objectContaining({ paper: 'a3', style: 'mondrian', occasion: 'Festa' }))
    const cells = document.querySelectorAll<HTMLElement>('[data-test="poster-preview"] .cell')
    expect(cells).toHaveLength(3)
    expect(cells[1].style.background).toBeTruthy()
    const img = cells[0].querySelector('img')!
    expect(img.style.width).toBe('200%')
    expect(img.style.left).toBe('-50%')
    expect(document.body.textContent).toContain('2339 × 3307 px')
    const fact = cells[1].querySelector<HTMLElement>('.fact')!
    expect(fact.textContent).toContain('DATA')
    expect([...fact.querySelectorAll('.fact-value span')].map((s) => s.textContent)).toEqual(['13–14', 'settembre 2025'])
    expect(fact.style.getPropertyValue('--value')).toBe('calc(0.2 * 100cqh)')
    expect(fact.style.fontFamily).toContain('Bahnschrift')
    expect(rows().map((r) => r.querySelector<HTMLInputElement>('input.value')!.value)).toEqual(['Festa', '13–14 settembre 2025', '2'])
    wrapper.unmount()
  })

  it('cambiare le opzioni rifà l\'anteprima', async () => {
    const wrapper = await mountDialog()
    const select = document.querySelector<HTMLSelectElement>('#p-paper')!
    select.value = 'a2'
    select.dispatchEvent(new Event('change'))
    await vi.advanceTimersByTimeAsync(300)
    await flushPromises()
    expect(api.posterLayout).toHaveBeenLastCalledWith(['p1', 'p2'], expect.objectContaining({ paper: 'a2' }))
    wrapper.unmount()
  })

  it('crea il poster in un job, mostra l\'avanzamento e poi le azioni sul file', async () => {
    api.createPoster.mockResolvedValue({ job: 7, name: 'poster-festa-1.jpg', folder: 'C:\\Users\\m\\Pictures\\Mosaic' })
    api.openPoster.mockResolvedValue(undefined)
    const wrapper = await mountDialog()
    document.querySelector<HTMLButtonElement>('[data-test="poster-create"]')!.click()
    await flushPromises()
    expect(api.createPoster).toHaveBeenCalledWith(['p1', 'p2'], expect.objectContaining({ seed: 1 }), 'Festa', 'a1')
    store.jobs.value = [job({ done: 1, total: 3, message: '1/3 riquadri' })]
    await flushPromises()
    expect(document.body.textContent).toContain('Compongo il poster')
    expect(document.body.textContent).toContain('1/3 riquadri')
    store.jobs.value = [job({ state: 'done', done: 3, total: 3 })]
    await flushPromises()
    expect(document.body.textContent).toContain('Poster pronto')
    expect(document.querySelector<HTMLImageElement>('.final')!.getAttribute('src')).toBe('/api/albums/a1/posters/poster-festa-1.jpg')
    const open = [...document.querySelectorAll('button')].find((b) => b.textContent?.includes('Apri'))!
    open.click()
    await flushPromises()
    expect(api.openPoster).toHaveBeenCalledWith('a1', 'poster-festa-1.jpg')
    wrapper.unmount()
  })

  it('le scritte si cambiano, si nascondono e si riordinano, e l\'anteprima le riceve', async () => {
    const wrapper = await mountDialog()
    expect(lastOptions().texts).toBeNull()
    await change(rows()[1].querySelector<HTMLInputElement>('input.value')!, 'estate 2025')
    expect(lastOptions().texts).toEqual([{ label: '', value: 'Festa' }, { label: 'Data', value: 'estate 2025' }, { label: 'Foto', value: '2' }])
    await change(rows()[1].querySelector<HTMLInputElement>('input:not(.value)')!, 'Quando')
    expect(lastOptions().texts[1]).toEqual({ label: 'Quando', value: 'estate 2025' })
    rows()[2].querySelector<HTMLButtonElement>('[aria-label="Nascondi la scritta 3"]')!.click()
    await settle()
    expect(lastOptions().texts).toEqual([{ label: '', value: 'Festa' }, { label: 'Quando', value: 'estate 2025' }])
    expect(rows()[2].classList).toContain('off')
    rows()[1].querySelector<HTMLButtonElement>('[aria-label="Sposta su la scritta 2"]')!.click()
    await settle()
    expect(lastOptions().texts.map((t: { value: string }) => t.value)).toEqual(['estate 2025', 'Festa'])
    wrapper.unmount()
  })

  it('aggiunge una scritta libera, le fa posto e torna alle automatiche', async () => {
    const wrapper = await mountDialog()
    const colors = document.querySelector<HTMLInputElement>('#p-colors')!
    colors.value = '3'
    colors.dispatchEvent(new Event('input'))
    await settle()
    const add = [...document.querySelectorAll('button')].find((b) => b.textContent?.includes('Aggiungi scritta'))!
    add.click()
    await flushPromises()
    const value = rows()[3].querySelector<HTMLInputElement>('input.value')!
    expect(document.activeElement).toBe(value)
    await change(value, 'Al garage')
    expect(lastOptions().texts.at(-1)).toEqual({ label: '', value: 'Al garage' })
    expect(lastOptions().colors).toBe(4)  // la quarta scritta entra: un riquadro in più
    rows()[3].querySelector<HTMLButtonElement>('[aria-label="Togli la scritta 4"]')!.click()
    await settle()
    expect(lastOptions().texts).toHaveLength(3)
    const reset = [...document.querySelectorAll('button')].find((b) => b.textContent?.includes('Automatiche'))!
    reset.click()
    await settle()
    expect(lastOptions().texts).toBeNull()
    expect(rows()).toHaveLength(3)
    wrapper.unmount()
  })

  it('carattere e maiuscole vanno nella richiesta', async () => {
    const wrapper = await mountDialog()
    const font = document.querySelector<HTMLSelectElement>('#p-font')!
    font.value = 'impact'
    font.dispatchEvent(new Event('change'))
    const letters = document.querySelector<HTMLSelectElement>('#p-case')!
    letters.value = 'upper'
    letters.dispatchEvent(new Event('change'))
    await settle()
    expect(lastOptions()).toEqual(expect.objectContaining({ font: 'impact', case: 'upper' }))
    wrapper.unmount()
  })
})
