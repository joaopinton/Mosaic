// Layout giustificato: righe di altezza uniforme riempite fino alla larghezza del contenitore.

export interface LayoutInput {
  width: number
  height: number
}

export interface LayoutTile {
  index: number
  width: number
  height: number
}

export interface LayoutRow {
  height: number
  tiles: LayoutTile[]
}

const FALLBACK_RATIO = 1.5
const MIN_RATIO = 0.4
const MAX_RATIO = 3.5

export function aspect(item: LayoutInput): number {
  if (!item.width || !item.height) return FALLBACK_RATIO
  return Math.min(MAX_RATIO, Math.max(MIN_RATIO, item.width / item.height))
}

/**
 * Dispone gli elementi in righe. Ogni riga si ridimensiona per riempire `containerWidth`; l'ultima, se incompleta,
 * non si allunga oltre l'altezza di riferimento.
 */
export function justify(items: LayoutInput[], containerWidth: number, targetHeight: number, gap: number): LayoutRow[] {
  if (containerWidth <= 0 || items.length === 0) return []
  const rows: LayoutRow[] = []
  let current: { index: number; ratio: number }[] = []
  let ratioSum = 0

  const flush = (stretch: boolean): void => {
    if (current.length === 0) return
    const usable = containerWidth - gap * (current.length - 1)
    const fitted = usable / ratioSum
    const height = stretch ? fitted : Math.min(fitted, targetHeight)
    rows.push({
      height: Math.floor(height),
      tiles: current.map((c) => ({ index: c.index, width: Math.floor(height * c.ratio), height: Math.floor(height) })),
    })
    current = []
    ratioSum = 0
  }

  items.forEach((item, index) => {
    const ratio = aspect(item)
    current.push({ index, ratio })
    ratioSum += ratio
    if (ratioSum * targetHeight + gap * (current.length - 1) >= containerWidth) flush(true)
  })
  flush(false)
  return rows
}
