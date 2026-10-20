import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mockMatchMedia } from './setup'

async function freshTheme() {
  vi.resetModules()
  return import('../src/lib/theme')
}

const root = document.documentElement

beforeEach(() => {
  root.removeAttribute('data-theme')
  root.removeAttribute('data-viewer')
  root.removeAttribute('data-theme-fade')
  mockMatchMedia(() => false)
})

afterEach(() => {
  delete (document as { startViewTransition?: unknown }).startViewTransition
})

describe('theme', () => {
  it('parte dal sistema e dal visore nero quando non c\'è nulla di salvato', async () => {
    const theme = await freshTheme()
    theme.applyAppearance()
    expect(theme.scheme.value).toBe('system')
    expect(root.hasAttribute('data-theme')).toBe(false)
    expect(theme.viewerBg.value).toBe('black')
    expect(root.hasAttribute('data-viewer')).toBe(false)
  })

  it('rilegge le preferenze salvate', async () => {
    localStorage.setItem('mosaic.theme', 'dark')
    localStorage.setItem('mosaic.viewer', 'grey')
    const theme = await freshTheme()
    theme.applyAppearance()
    expect(root.getAttribute('data-theme')).toBe('dark')
    expect(root.getAttribute('data-viewer')).toBe('grey')
  })

  it('ignora un valore di tema non valido', async () => {
    localStorage.setItem('mosaic.theme', 'fucsia')
    expect((await freshTheme()).scheme.value).toBe('system')
  })

  it('setScheme applica subito e salva senza View Transitions', async () => {
    const theme = await freshTheme()
    theme.setScheme('dark')
    expect(root.getAttribute('data-theme')).toBe('dark')
    expect(localStorage.getItem('mosaic.theme')).toBe('dark')
    theme.setScheme('system')
    expect(root.hasAttribute('data-theme')).toBe(false)
    expect(localStorage.getItem('mosaic.theme')).toBe('system')
  })

  it('con le View Transitions cambia dentro la transizione e toglie il marcatore alla fine', async () => {
    let resolve!: () => void
    const finished = new Promise<void>((r) => (resolve = r))
    const start = vi.fn((cb: () => void) => { cb(); return { finished } })
    ;(document as unknown as { startViewTransition: typeof start }).startViewTransition = start
    const theme = await freshTheme()
    theme.setScheme('light')
    expect(start).toHaveBeenCalledOnce()
    expect(root.getAttribute('data-theme')).toBe('light')
    expect(root.hasAttribute('data-theme-fade')).toBe(true)
    resolve()
    await finished
    await Promise.resolve()
    expect(root.hasAttribute('data-theme-fade')).toBe(false)
  })

  it('con movimento ridotto niente transizione', async () => {
    mockMatchMedia((q) => q.includes('prefers-reduced-motion'))
    const start = vi.fn()
    ;(document as unknown as { startViewTransition: typeof start }).startViewTransition = start
    const theme = await freshTheme()
    theme.setScheme('dark')
    expect(start).not.toHaveBeenCalled()
    expect(root.getAttribute('data-theme')).toBe('dark')
  })

  it('setViewerBg applica e salva; localStorage bloccato non rompe nulla', async () => {
    const theme = await freshTheme()
    theme.setViewerBg('grey')
    expect(root.getAttribute('data-viewer')).toBe('grey')
    expect(localStorage.getItem('mosaic.viewer')).toBe('grey')
    theme.setViewerBg('black')
    expect(root.hasAttribute('data-viewer')).toBe(false)

    const blocked = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('bloccato') })
    expect(() => theme.setViewerBg('grey')).not.toThrow()
    expect(root.getAttribute('data-viewer')).toBe('grey')
    blocked.mockRestore()
  })

  it('ignora uno sfondo del visore non valido', async () => {
    localStorage.setItem('mosaic.viewer', 'fucsia')
    expect((await freshTheme()).viewerBg.value).toBe('black')
  })

  it('le scelte per il visore hanno valore e nome', async () => {
    const { VIEWER_BGS } = await freshTheme()
    expect(VIEWER_BGS.map((v) => v.value)).toEqual(['black', 'grey'])
    for (const v of VIEWER_BGS) expect(v.label).toBeTruthy()
  })
})
