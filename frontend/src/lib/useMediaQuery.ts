import { onBeforeUnmount, ref, type Ref } from 'vue'

/** Segue una media query (breakpoint della shell: stretto < 960 px, ampio ≥ 1280 px). */
export function useMediaQuery(query: string): Ref<boolean> {
  const list = typeof window !== 'undefined' && window.matchMedia ? window.matchMedia(query) : null
  const matches = ref(list?.matches ?? false)
  const onChange = (e: MediaQueryListEvent) => {
    matches.value = e.matches
  }
  list?.addEventListener('change', onChange)
  onBeforeUnmount(() => list?.removeEventListener('change', onChange))
  return matches
}
