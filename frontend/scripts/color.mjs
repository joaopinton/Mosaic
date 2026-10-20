// Colore per gli script dei token: OKLCH <-> sRGB, gamut, contrasto WCAG e composizione con trasparenza.
// Nessuna dipendenza: lo usano sia il generatore della palette sia il controllo di contrasto del lint.

const clamp01 = (x) => Math.min(1, Math.max(0, x))
const toGamma = (c) => (c <= 0.0031308 ? 12.92 * c : 1.055 * Math.abs(c) ** (1 / 2.4) - 0.055)
const toLinear = (c) => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4)

/** OKLCH (L 0-1, C, H in gradi) -> sRGB non tagliato, componenti 0-1 (fuori da [0,1] = fuori gamut). */
export function oklchToRgb(L, C, H) {
  const h = (H * Math.PI) / 180
  const a = C * Math.cos(h)
  const b = C * Math.sin(h)
  const l = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3
  const m = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3
  const s = (L - 0.0894841775 * a - 1.291485548 * b) ** 3
  return [
    toGamma(4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s),
    toGamma(-1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s),
    toGamma(-0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s),
  ]
}

/** sRGB 0-1 -> [L, C, H]. */
export function rgbToOklch([r, g, b]) {
  const [lr, lg, lb] = [r, g, b].map(toLinear)
  const l = Math.cbrt(0.4122214708 * lr + 0.5363325363 * lg + 0.0514459929 * lb)
  const m = Math.cbrt(0.2119034982 * lr + 0.6806995451 * lg + 0.1073969566 * lb)
  const s = Math.cbrt(0.0883024619 * lr + 0.2817188376 * lg + 0.6299787005 * lb)
  const L = 0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s
  const a = 1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s
  const bb = 0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s
  const C = Math.hypot(a, bb)
  const H = C < 1e-4 ? 0 : ((Math.atan2(bb, a) * 180) / Math.PI + 360) % 360
  return [L, C, H]
}

export function hexToRgb(hex) {
  const v = hex.replace('#', '')
  return [0, 2, 4].map((i) => parseInt(v.slice(i, i + 2), 16) / 255)
}

export const inGamut = (rgb) => rgb.every((c) => c >= -0.0005 && c <= 1.0005)

/** Riduce la saturazione finché il colore sta in sRGB (la luminosità e la tonalità restano). */
export function fitChroma(L, C, H) {
  let c = C
  while (c > 0 && !inGamut(oklchToRgb(L, c, H))) c -= 0.0005
  return Math.max(0, c)
}

/** Luminanza relativa WCAG di un colore sRGB 0-1. */
export function luminance(rgb) {
  const [r, g, b] = rgb.map((c) => toLinear(clamp01(c)))
  return 0.2126 * r + 0.7152 * g + 0.0722 * b
}

export function contrast(rgbA, rgbB) {
  const [hi, lo] = [luminance(rgbA), luminance(rgbB)].sort((x, y) => y - x)
  return (hi + 0.05) / (lo + 0.05)
}

/** Sovrappone un colore con trasparenza {rgb, alpha} a uno sfondo opaco. */
export function composite({ rgb, alpha }, backdrop) {
  return rgb.map((c, i) => clamp01(c) * alpha + backdrop[i] * (1 - alpha))
}

/** Legge `oklch(L% C H)` o `oklch(L% C H / A)`; restituisce {rgb, alpha} oppure null. */
export function parseOklch(text) {
  const m = /^oklch\(\s*([\d.]+)%\s+([\d.]+)\s+([\d.]+)\s*(?:\/\s*([\d.]+)\s*)?\)$/.exec(text.trim())
  if (!m) return null
  const [, l, c, h, a] = m
  return { rgb: oklchToRgb(Number(l) / 100, Number(c), Number(h)), alpha: a === undefined ? 1 : Number(a) }
}
