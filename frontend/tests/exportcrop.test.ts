import { flushPromises, mount } from '@vue/test-utils'
import axe from 'axe-core'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// «Ritaglia a formato» nell'esportazione (D-34).
const api = vi.hoisted(() => ({
  albums: vi.fn(), exportPhotos: vi.fn(), exportDefaultDir: vi.fn(), exportPreview: vi.fn(), exportCropPreview: vi.fn(),
  revealExport: vi.fn(), pickFolder: vi.fn(), jobs: vi.fn(),
}))
vi.mock('../src/lib/api', async (original) => ({ ...(await original<typeof import('../src/lib/api')>()), api }))

import ExportCrop from '../src/components/ExportCrop.vue'
import ExportDialog from '../src/components/ExportDialog.vue'
import SelectionBar from '../src/components/SelectionBar.vue'
import * as store from '../src/lib/store'

const SHOT = { uid: 'p1', image: 'data:image/jpeg;base64,AA', box: [0.25, 0, 0.75, 1] as [number, number, number, number] }
const mounted: { unmount: () => void }[] = []
beforeEach(() => {
  Object.values(api).forEach((fn) => fn.mockReset())
  api.exportDefaultDir.mockResolvedValue({ folder: 'C:\\Users\\me\\Desktop\\Festa' })
  api.exportCropPreview.mockResolvedValue({ photos: [SHOT] })
  api.exportPhotos.mockResolvedValue({ job: 3, folder: 'C:\\Users\\me\\Desktop\\Festa' })
  api.jobs.mockResolvedValue([])
  api.albums.mockResolvedValue([])
  vi.useFakeTimers()
})
afterEach(() => {
  vi.useRealTimers()
  mounted.splice(0).forEach((w) => w.unmount())
  store.selected.value = []
})

function open(crop = true) {
  const w = mount(ExportDialog, { props: { open: true, uids: ['p1', 'p2'], title: 'Festa', crop, mode: 'folder' }, attachTo: document.body })
  mounted.push(w)
  return w
}

async function settle(): Promise<void> {
  await flushPromises()
  await vi.advanceTimersByTimeAsync(400)
  await flushPromises()
}

describe('ExportDialog con il ritaglio', () => {
  it('opens with the crop on, previews the frame and sends the format', async () => {
    const w = open()
    await settle()
    expect((w.get('[data-test="export-crop"]').element as HTMLInputElement).checked).toBe(true)
    expect(api.exportCropPreview).toHaveBeenLastCalledWith(['p1', 'p2'], { w: 1, h: 1, pixels: false })
    const frame = w.get('.frame').attributes('style')
    expect(frame).toContain('left: 25%')
    expect(frame).toContain('width: 50%')
    expect(w.findAll('input[type="checkbox"][disabled]')).toHaveLength(2) // video e RAW restano fuori

    await w.get('[data-test="crop-9:16"]').setValue(true)
    await settle()
    expect(api.exportCropPreview).toHaveBeenLastCalledWith(['p1', 'p2'], { w: 9, h: 16, pixels: false })
    await w.get('[data-test="export-start"]').trigger('click')
    await flushPromises()
    expect(api.exportPhotos).toHaveBeenCalledWith({
      uids: ['p1', 'p2'], dest: 'C:\\Users\\me\\Desktop\\Festa', raw: false, videos: false, enhance: null, crop: { w: 9, h: 16, pixels: false },
      zip: false, zip_name: 'Festa', fmt: 'original', quality: 92, max_side: null, numbered: false,
    })
  })

  it('takes a free size in pixels or as a ratio and refuses impossible ones', async () => {
    const w = open()
    await settle()
    await w.get('[data-test="crop-free"]').setValue(true)
    await w.get('[data-test="crop-w"]').setValue('1080')
    await w.get('[data-test="crop-h"]').setValue('1920')
    await settle()
    expect(api.exportCropPreview).toHaveBeenLastCalledWith(['p1', 'p2'], { w: 1080, h: 1920, pixels: true })
    expect(w.text()).toContain('Ogni copia esce di 1080×1920 pixel')

    await w.get('input[type="radio"][value="ratio"]').setValue(true)
    await settle()
    expect(api.exportCropPreview).toHaveBeenLastCalledWith(['p1', 'p2'], { w: 1080, h: 1920, pixels: false })

    await w.get('[data-test="crop-w"]').setValue('30000')
    await flushPromises()
    expect(w.find('[data-test="crop-problem"]').exists()).toBe(true)
    expect(w.get('[data-test="export-start"]').attributes('disabled')).toBeDefined()
    await w.get('[data-test="crop-w"]').setValue('25')
    await w.get('[data-test="crop-h"]').setValue('1')
    await flushPromises()
    expect(w.find('[data-test="crop-problem"]').exists()).toBe(true) // più di 10:1
  })

  it('stays off when opened from «Esporta»', async () => {
    const w = open(false)
    await settle()
    expect((w.get('[data-test="export-crop"]').element as HTMLInputElement).checked).toBe(false)
    expect(api.exportCropPreview).not.toHaveBeenCalled()
  })
})

describe('SelectionBar', () => {
  it('opens the export dialog with the crop on from «Ritaglia…»', async () => {
    store.selected.value = ['p1']
    const w = mount(SelectionBar, { attachTo: document.body })
    mounted.push(w)
    await flushPromises()
    await w.get('[data-test="bar-crop"]').trigger('click')
    await flushPromises()
    expect(w.findComponent(ExportDialog).props('crop')).toBe(true)
    await w.findComponent(ExportDialog).vm.$emit('close')
    await w.get('[data-test="bar-export"]').trigger('click')
    expect(w.findComponent(ExportDialog).props('crop')).toBe(false)
  })
})

describe('ExportCrop', () => {
  it('has no accessibility violations (axe)', async () => {
    vi.useRealTimers()
    const w = mount(ExportCrop, { props: { uids: ['p1'], modelValue: null }, attachTo: document.body })
    mounted.push(w)
    await new Promise((resolve) => setTimeout(resolve, 350))
    await flushPromises()
    const result = await axe.run(w.element as HTMLElement, { rules: { 'color-contrast': { enabled: false }, region: { enabled: false } } })
    expect(result.violations).toEqual([])
    expect(w.emitted('update:modelValue')?.[0]).toEqual([{ w: 1, h: 1, pixels: false }])
  })
})
