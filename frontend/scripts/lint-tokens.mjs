// Lint dei token: i componenti usano solo token semantici (vietati colori letterali, primitivi di palette,
// durate letterali nelle transizioni) e le coppie testo/sfondo dei token rispettano il contrasto WCAG.
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'
import { fileURLToPath } from 'node:url'
import { composite, contrast, parseOklch, hexToRgb } from './color.mjs'

// radice del frontend: dal file dello script; sotto vitest (import.meta.url non è un file:) vale la cartella corrente
const ROOT = (() => {
  try {
    return fileURLToPath(new URL('..', import.meta.url))
  } catch {
    return process.cwd()
  }
})()
const SCAN = ['src/components', 'src/pages', 'src/styles/controls.css']

const RULES = [
  { name: 'colore letterale', re: /#[0-9a-fA-F]{3,8}\b(?![^{]*\})|\b(?:rgba?|hsla?|oklch|oklab|lab|lch)\(/ },
  { name: 'primitivo di palette', re: /var\(--(?:brand|neutral|stage|match|to_verify|deviates|alternative)-\d+\)/ },
  { name: 'durata letterale in transition', re: /transition(?:-duration)?\s*:[^;]*\b\d*\.?\d+m?s\b(?!\s*var)/ },
  { name: 'colore con nome', re: /(?:^|[\s;{])(?:color|background|background-color|border-color|fill|stroke)\s*:\s*(?:white|black|red|green|blue|gray|grey)\b/ },
]

function walk(path) {
  const stat = statSync(path)
  if (stat.isFile()) return [path]
  return readdirSync(path).flatMap((name) => walk(join(path, name)))
}

/** Restituisce le violazioni di una porzione di CSS (il testo dei blocchi <style> o di un .css). */
export function lintCss(css, file = '') {
  const found = []
  css.split('\n').forEach((line, i) => {
    const clean = line.replace(/\/\*.*?\*\//g, '')
    for (const rule of RULES) if (rule.re.test(clean)) found.push({ file, line: i + 1, rule: rule.name, text: line.trim() })
  })
  return found
}

export function styleOf(source, file) {
  if (file.endsWith('.css')) return source
  return [...source.matchAll(/<style[^>]*>([\s\S]*?)<\/style>/g)].map((m) => m[1]).join('\n')
}

export function lintProject() {
  const violations = []
  for (const entry of SCAN) {
    for (const file of walk(join(ROOT, entry)).filter((f) => /\.(vue|css)$/.test(f))) {
      violations.push(...lintCss(styleOf(readFileSync(file, 'utf8'), file), relative(ROOT, file)))
    }
  }
  return violations
}

const TOKENS = 'src/styles/tokens'
const WHITE = [1, 1, 1]
const BLACK = [0, 0, 0]

const SURFACES = ['bg-surface', 'bg-canvas', 'bg-sidebar', 'bg-selected', 'bg-hover', 'bg-sunken']
const STATES = ['match', 'to-verify', 'deviates', 'alternative']
/** [primo piano, sfondo, contrasto minimo]: 4.5 per il testo, 3 per bordi ed elementi grafici (WCAG 2.2). */
const PAIRS = [
  ...['fg-default', 'fg-muted', 'fg-subtle'].flatMap((fg) => SURFACES.map((bg) => [fg, bg, 4.5])),
  ...['accent-text'].flatMap((fg) => ['bg-surface', 'bg-canvas', 'bg-sidebar', 'bg-selected'].map((bg) => [fg, bg, 4.5])),
  ['fg-on-accent', 'accent', 4.5],
  ['fg-on-accent', 'accent-hover', 4.5],
  ['accent-soft-fg', 'accent-soft', 4.5],
  ...STATES.map((state) => [`${state}-fg`, `${state}-bg`, 4.5]),
  ['fg-on-white', 'bg-field-white', 4.5],
  ['fg-subtle-on-white', 'bg-field-white', 4.5],
  ['border-focus', 'bg-surface', 3],
  ['border-strong', 'bg-surface', 3],
  ['accent', 'bg-surface', 3],
]

/** Divide `a, b` alla virgola di primo livello (le virgole dentro le parentesi non contano). */
function splitArgs(text) {
  const parts = []
  let depth = 0
  let start = 0
  for (let i = 0; i < text.length; i++) {
    if (text[i] === '(') depth++
    else if (text[i] === ')') depth--
    else if (text[i] === ',' && depth === 0) {
      parts.push(text.slice(start, i).trim())
      start = i + 1
    }
  }
  parts.push(text.slice(start).trim())
  return parts
}

function declarations(css) {
  const out = new Map()
  for (const m of css.replace(/\/\*[\s\S]*?\*\//g, '').matchAll(/--([\w-]+)\s*:\s*([^;]+);/g)) out.set(m[1], m[2].trim())
  return out
}

/** Risolve un token in {rgb, alpha} per un tema; null se il valore non è un colore semplice (gradienti, `oklch(from …)`). */
function resolver(decls) {
  const resolve = (name, mode, depth = 0) => {
    const raw = decls.get(name)
    if (raw === undefined || depth > 8) return null
    return value(raw, mode, depth)
  }
  const value = (raw, mode, depth) => {
    const ld = /^light-dark\(([\s\S]*)\)$/.exec(raw)
    if (ld) return value(splitArgs(ld[1])[mode === 'light' ? 0 : 1], mode, depth + 1)
    const ref = /^var\(--([\w-]+)\)$/.exec(raw)
    if (ref) return resolve(ref[1], mode, depth + 1)
    if (raw.startsWith('#')) return { rgb: hexToRgb(raw), alpha: 1 }
    return parseOklch(raw)
  }
  return resolve
}

/**
 * Controlla il contrasto delle coppie testo/sfondo dei token, in chiaro e in scuro, e del testo sopra le foto
 * (peggior caso: foto bianca e foto nera sotto lo scrim). `sources` permette ai test di sostituire i CSS letti
 * o di aggiungere (`append`) dichiarazioni che sovrascrivono quelle dei token.
 */
export function lintContrast(sources = {}) {
  const palettes = sources.palettes ?? readFileSync(join(ROOT, TOKENS, 'palettes.css'), 'utf8')
  const semantic = (sources.semantic ?? readFileSync(join(ROOT, TOKENS, 'semantic.css'), 'utf8')) + (sources.append ?? '')
  const resolve = resolver(new Map([...declarations(palettes), ...declarations(semantic)]))
  const found = []
  const check = (fgName, bgName, min, mode, compositeOver) => {
    const fg = resolve(fgName, mode)
    const bg = resolve(bgName, mode)
    if (!fg || !bg) {
      found.push({ file: `${TOKENS}/semantic.css`, line: 0, rule: 'contrasto', text: `--${fgName} o --${bgName} non si risolve (${mode})` })
      return
    }
    const bgRgb = compositeOver ? composite(bg, compositeOver) : bg.rgb
    const fgRgb = fg.alpha < 1 ? composite(fg, bgRgb) : fg.rgb
    const ratio = contrast(fgRgb, bgRgb)
    if (ratio < min) {
      const where = compositeOver ? (compositeOver === WHITE ? ' sopra bianco' : ' sopra nero') : ''
      found.push({ file: `${TOKENS}/semantic.css`, line: 0, rule: 'contrasto', text: `--${fgName} su --${bgName}${where} (${mode}): ${ratio.toFixed(2)} invece di ${min}` })
    }
  }
  for (const mode of ['light', 'dark']) {
    for (const [fg, bg, min] of PAIRS) check(fg, bg, min, mode)
    // sulle foto il tema non conta: si prova lo scrim sopra una foto bianca e una nera
    for (const base of [WHITE, BLACK]) check('overlay-fg', 'overlay-scrim', 4.5, mode, base)
    check('overlay-fg', 'viewer-bg', 3, mode)
  }
  // grigio 18% del visore: i comandi bianchi devono restare leggibili (3:1)
  const grey = /:root\[data-viewer="grey"\]\s*\{\s*--viewer-bg:\s*([^;]+);/.exec(semantic)
  const white = resolve('overlay-fg', 'light')
  const greyColor = grey && parseOklch(grey[1])
  if (!greyColor || !white) found.push({ file: `${TOKENS}/semantic.css`, line: 0, rule: 'contrasto', text: 'manca --viewer-bg per [data-viewer="grey"]' })
  else if (contrast(white.rgb, greyColor.rgb) < 3) found.push({ file: `${TOKENS}/semantic.css`, line: 0, rule: 'contrasto', text: '--overlay-fg su --viewer-bg grigio: sotto 3' })
  return found
}

if (process.argv[1] && import.meta.url.startsWith('file:') && fileURLToPath(import.meta.url) === process.argv[1]) {
  const violations = [...lintProject(), ...lintContrast()]
  for (const v of violations) console.error(`${v.file}:${v.line}  ${v.rule}: ${v.text}`)
  console.log(violations.length ? `${violations.length} violazioni` : 'Token: nessuna violazione')
  process.exit(violations.length ? 1 : 0)
}
