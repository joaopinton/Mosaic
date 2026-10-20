// Costanti di movimento di sift-argus-v3 (src/lib/motion.ts). Le durate vivono nei token CSS e si leggono dal DOM:
// con prefers-reduced-motion i token valgono 0 ms e quindi anche le animazioni JavaScript si azzerano.

const FALLBACK_MS: Record<string, number> = {
  '--dur-instant': 80,
  '--dur-fast': 140,
  '--dur-base': 220,
  '--dur-slow': 360,
  '--dur-reorder': 420,
}

export function durationToken(name: string): number {
  if (typeof document === 'undefined') return FALLBACK_MS[name] ?? 0
  const raw = getComputedStyle(document.documentElement).getPropertyValue(name).trim()
  if (raw.endsWith('ms')) return parseFloat(raw)
  if (raw.endsWith('s')) return parseFloat(raw) * 1000
  return FALLBACK_MS[name] ?? 0
}

export const seconds = (name: string): number => durationToken(name) / 1000

export const MORPH_SPRING = { type: 'spring', stiffness: 520, damping: 38, mass: 0.9 } as const
export const TAB_SPRING = { type: 'spring', stiffness: 500, damping: 38 } as const
export const BAR_SPRING = { type: 'spring', stiffness: 420, damping: 38 } as const
export const CARD_SPRING = { type: 'spring', stiffness: 320, damping: 30 } as const
export const EASE_OUT = [0.16, 1, 0.3, 1] as const
export const STAGGER_S = 0.03
export const STAGGER_DELAY_S = 0.08
