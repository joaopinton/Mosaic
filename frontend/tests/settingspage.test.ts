import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mockMatchMedia } from './setup'

const api = vi.hoisted(() => ({ config: vi.fn(), putConfig: vi.fn(), cloudStatus: vi.fn(), putCloudKey: vi.fn() }))
vi.mock('../src/lib/api', async (original) => ({ ...(await original<typeof import('../src/lib/api')>()), api }))

import SettingsPage from '../src/pages/SettingsPage.vue'
import type { AppConfig, CloudStatus } from '../src/lib/types'

const baseConfig = (cloud: Partial<AppConfig['llm']['cloud']> = {}): AppConfig => ({
  library_roots: [], data_dir: 'data', import_dir: '', workers: 0, port: 8000, stack_sequences: false, stack_uuid: true, stack_meta: false,
  auto_index_delay: 0, delete_mode: 'catalog', thumbs: { pregenerate: [], quality: 83 },
  ai: { enabled: true, model: 'siglip2', threads: 0, provider: 'cpu', detect_nsfw: false, faces: true, label_threshold: 0.35, max_labels: 5, batch_size: 8 },
  llm: {
    mode: 'newly-indexed', model: 'qwen3vl-2b', idle_seconds: 300, threads: 0, context: 2048, parallel: 2, language: 'it', image_size: 448, about: '', caption: false,
    cloud: { enabled: false, providers: ['nvidia', 'openrouter'], nvidia_model: 'google/gemma-4-31b-it', openrouter_model: 'google/gemma-4-31b-it:free', parallel: 4, send_names: true, ...cloud },
  },
  rename: { auto: true, pattern: '{seq:04}_{slug}', slug_max: 60, seq_start: 1 },
}) as AppConfig

const status = (nvidia: 'file' | 'env' | null = null, openrouter: 'file' | 'env' | null = null): CloudStatus => ({
  providers: {
    nvidia: { label: 'NVIDIA', key: nvidia, model: 'google/gemma-4-31b-it' },
    openrouter: { label: 'OpenRouter', key: openrouter, model: 'google/gemma-4-31b-it:free' },
  },
})

const mounted: { unmount: () => void }[] = []

async function mountPage(config: AppConfig, cloud: CloudStatus) {
  api.config.mockResolvedValue(config)
  api.cloudStatus.mockResolvedValue(cloud)
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: SettingsPage }] })
  await router.push('/')
  await router.isReady()
  const wrapper = mount(SettingsPage, { global: { plugins: [router] }, attachTo: document.body })
  mounted.push(wrapper)
  await flushPromises()
  return wrapper
}

beforeEach(() => {
  mockMatchMedia(() => false)
  Object.values(api).forEach((fn) => fn.mockReset())
})

afterEach(() => {
  while (mounted.length) mounted.pop()!.unmount()
})

describe('Impostazioni: didascalie nel cloud', () => {
  it('mostra i campi delle chiavi accanto all\'interruttore, con il cloud spento', async () => {
    const page = await mountPage(baseConfig(), status())
    const toggle = page.get('[data-test="cloud-enabled"]')
    expect((toggle.element as HTMLInputElement).checked).toBe(false)
    expect(page.find('[data-test="key-nvidia"] input').attributes('type')).toBe('password')
    expect(page.find('[data-test="key-openrouter"] input').exists()).toBe(true)
    expect(page.find('[data-test="cloud-nokey"]').exists()).toBe(false)   // spento: nessun avviso
  })

  it('avvisa se il cloud è acceso senza nessuna chiave, e l\'avviso sparisce appena se ne scrive una', async () => {
    const page = await mountPage(baseConfig({ enabled: true }), status())
    expect(page.find('[data-test="cloud-nokey"]').exists()).toBe(true)
    await page.get('[data-test="key-nvidia"] input').setValue('nvapi-prova')
    expect(page.find('[data-test="cloud-nokey"]').exists()).toBe(false)
  })

  it('con una chiave già salvata non avvisa e non la mostra', async () => {
    const page = await mountPage(baseConfig({ enabled: true }), status('file'))
    expect(page.find('[data-test="cloud-nokey"]').exists()).toBe(false)
    const input = page.get('[data-test="key-nvidia"] input')
    expect((input.element as HTMLInputElement).value).toBe('')
    expect(page.get('[data-test="key-nvidia"]').text()).toContain('Salvata')
  })

  it('salva prima le chiavi digitate, poi le impostazioni, senza mai mettere la chiave nella configurazione', async () => {
    const order: string[] = []
    api.putCloudKey.mockImplementation(async (provider: string) => { order.push(`key:${provider}`); return status('file') })
    api.putConfig.mockImplementation(async (changes: Record<string, unknown>) => { order.push('config'); return { ...baseConfig({ enabled: true }), changes } })
    const page = await mountPage(baseConfig(), status())
    await page.get('[data-test="cloud-enabled"]').setValue(true)
    await page.get('[data-test="key-nvidia"] input').setValue('  nvapi-segreta  ')
    await page.get('button.btn.primary').trigger('click')
    await flushPromises()
    expect(api.putCloudKey).toHaveBeenCalledTimes(1)
    expect(api.putCloudKey).toHaveBeenCalledWith('nvidia', 'nvapi-segreta')
    expect(order).toEqual(['key:nvidia', 'config'])
    const sent = JSON.stringify(api.putConfig.mock.calls[0][0])
    expect(sent).not.toContain('nvapi-segreta')
    expect(api.putConfig.mock.calls[0][0].llm.cloud).toEqual({ enabled: true, send_names: true })
    expect((page.get('[data-test="key-nvidia"] input').element as HTMLInputElement).value).toBe('')   // il campo si svuota dopo il salvataggio
  })

  it('non invia chiavi vuote e permette di rimuovere quella salvata', async () => {
    api.putConfig.mockResolvedValue(baseConfig())
    api.putCloudKey.mockResolvedValue(status())
    const page = await mountPage(baseConfig(), status('file'))
    await page.get('button.btn.primary').trigger('click')
    await flushPromises()
    expect(api.putCloudKey).not.toHaveBeenCalled()
    const remove = page.findAll('[data-test="key-nvidia"] button').find((b) => b.text() === 'Rimuovi')!
    await remove.trigger('click')
    await flushPromises()
    expect(api.putCloudKey).toHaveBeenCalledWith('nvidia', '')
    expect(page.findAll('[data-test="key-nvidia"] button').some((b) => b.text() === 'Rimuovi')).toBe(false)
  })

  it('Sincronizzazione: «Solo interfaccia» è il predefinito, «Sincronizzata» si salva con il controllo delle cartelle', async () => {
    api.putConfig.mockResolvedValue(baseConfig())
    const page = await mountPage(baseConfig(), status())
    const section = page.get('[data-test="delete-section"]')
    expect(section.find('[data-test="delete-hint-catalog"]').exists()).toBe(true)
    expect(section.text()).toContain('il file resta dov')
    const pulizia = section.findAll('input[type="radio"]')[1]
    await pulizia.setValue(true)
    expect(section.find('[data-test="delete-hint-trash"]').exists()).toBe(true)
    expect(section.text()).toContain('Cestino di Windows')
    await section.get('[data-test="sync-watch"]').setValue(true)
    await page.findAll('button').find((b) => b.text() === 'Salva')!.trigger('click')
    await flushPromises()
    expect(api.putConfig.mock.calls[0][0]).toMatchObject({ delete_mode: 'trash', auto_index_delay: 30 })
  })

  it('mostra e nasconde la chiave digitata', async () => {
    const page = await mountPage(baseConfig(), status())
    const input = page.get('[data-test="key-nvidia"] input')
    const toggle = page.findAll('[data-test="key-nvidia"] button').find((b) => b.text() === 'Mostra')!
    await toggle.trigger('click')
    expect(input.attributes('type')).toBe('text')
    await toggle.trigger('click')
    expect(input.attributes('type')).toBe('password')
  })
})
