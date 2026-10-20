// Genera src/styles/tokens/palettes.css dai colori del logo (frontend/public/favicon.svg).
// Uso: npm run build:palette. Il file generato non si modifica a mano: si cambiano i semi qui sotto.
import { writeFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { fitChroma, hexToRgb, rgbToOklch } from './color.mjs'

/** Colori del logo: piastra, bordo e i quattro pallini. */
export const LOGO = { red: '#ff334b', orange: '#ff7a1a', cyan: '#00d2ff', yellow: '#ffc828', plate: '#0f1523' }

const STEPS = [0, 50, 100, 200, 300, 400, 500, 600, 700, 800, 900, 950, 975, 1000]
const LIGHTNESS = [99.6, 98, 95.5, 91, 84, 72, 61, 52, 44, 36, 27, 20.5, 16.5, 13.5]
// quanta parte della saturazione massima tiene ogni gradino (più alta al centro, quasi nulla ai bordi)
const COLORFUL = [0.05, 0.1, 0.22, 0.4, 0.62, 0.85, 1, 1, 0.92, 0.8, 0.66, 0.52, 0.42, 0.35]
// il rosso è molto saturo: ai gradini scuri si attenua, perché diventano sfondi (selezione, riquadri)
const RED = [0.05, 0.1, 0.22, 0.4, 0.62, 0.85, 1, 1, 0.9, 0.72, 0.5, 0.34, 0.27, 0.22]
const QUIET = [0.05, 0.25, 0.42, 0.4, 0.62, 0.85, 1, 1, 0.92, 0.8, 0.66, 0.55, 0.5, 0.45]

const seed = (hex) => rgbToOklch(hexToRgb(hex))
const red = seed(LOGO.red)
const plate = seed(LOGO.plate)

/** Famiglie di scale: nome, tonalità, saturazione massima, profilo. */
export const SCALES = [
  { name: 'brand', hue: red[2], chroma: red[1], profile: RED },
  { name: 'neutral', hue: plate[2], chroma: plate[1] * 0.87, profile: QUIET },
  // grigi dietro le foto: nessuna tinta, per non falsare i colori
  { name: 'stage', hue: 0, chroma: 0, profile: QUIET },
  { name: 'match', hue: 149, chroma: 0.15, profile: COLORFUL },
  { name: 'to_verify', hue: seed(LOGO.yellow)[2], chroma: 0.15, profile: COLORFUL },
  { name: 'deviates', hue: seed(LOGO.orange)[2], chroma: 0.17, profile: COLORFUL },
  { name: 'alternative', hue: seed(LOGO.cyan)[2], chroma: 0.13, profile: COLORFUL },
]

const round = (x, digits) => Number(x.toFixed(digits))

function scaleLines({ name, hue, chroma, profile }) {
  return STEPS.map((step, i) => {
    const L = LIGHTNESS[i]
    const c = round(fitChroma(L / 100, chroma * profile[i], hue), 4)
    return `  --${name}-${step}: oklch(${L}% ${c} ${round(hue, 1)});`
  })
}

export function buildCss() {
  const blocks = SCALES.map((scale) => scaleLines(scale).join('\n'))
  const logo = Object.entries(LOGO).map(([k, v]) => `  --logo-${k}: ${v};`).join('\n')
  return [
    '/* GENERATO da scripts/build-palette.mjs: non modificare a mano. I semi sono i colori del logo (public/favicon.svg). */',
    '',
    ':root {',
    '  /* Il logo è lo stesso in chiaro e in scuro */',
    logo,
    '',
    blocks.join('\n\n'),
    '}',
    '',
  ].join('\n')
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  const target = fileURLToPath(new URL('../src/styles/tokens/palettes.css', import.meta.url))
  writeFileSync(target, buildCss())
  console.log(`Scritto ${target}`)
}
