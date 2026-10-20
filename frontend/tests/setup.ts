// Polyfill degli strumenti del browser che jsdom non implementa.
import { afterEach, vi } from 'vitest'

class FakeResizeObserver {
  constructor(private cb: ResizeObserverCallback) {}
  observe(target: Element): void {
    this.cb([{ target, contentRect: { width: 1000, height: 600 } } as unknown as ResizeObserverEntry], this as unknown as ResizeObserver)
  }
  unobserve(): void {}
  disconnect(): void {}
}
class FakeIntersectionObserver {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
  takeRecords(): IntersectionObserverEntry[] { return [] }
}

vi.stubGlobal('ResizeObserver', FakeResizeObserver)
vi.stubGlobal('IntersectionObserver', FakeIntersectionObserver)

HTMLDialogElement.prototype.showModal = function showModal(this: HTMLDialogElement) { this.setAttribute('open', '') }
HTMLDialogElement.prototype.close = function close(this: HTMLDialogElement) { this.removeAttribute('open') }
Element.prototype.scrollTo = (() => undefined) as typeof Element.prototype.scrollTo

export function mockMatchMedia(matching: (query: string) => boolean): void {
  window.matchMedia = ((query: string) => ({
    matches: matching(query), media: query, onchange: null,
    addEventListener: () => undefined, removeEventListener: () => undefined,
    addListener: () => undefined, removeListener: () => undefined, dispatchEvent: () => false,
  })) as typeof window.matchMedia
}
mockMatchMedia(() => false)

afterEach(() => {
  document.body.innerHTML = ''
  localStorage.clear()
  vi.useRealTimers()
})
