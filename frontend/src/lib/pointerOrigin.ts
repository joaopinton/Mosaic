// I dialoghi nascono dal punto cliccato: scrive in --origin-dx / --origin-dy lo scarto del click dal centro della finestra.

export function trackPointerOrigin(): () => void {
  const root = document.documentElement
  const onPointer = (e: PointerEvent) => {
    root.style.setProperty('--origin-dx', `${Math.round(e.clientX - window.innerWidth / 2)}px`)
    root.style.setProperty('--origin-dy', `${Math.round(e.clientY - window.innerHeight / 2)}px`)
  }
  const onKey = () => {
    root.style.setProperty('--origin-dx', '0px')
    root.style.setProperty('--origin-dy', '0px')
  }
  window.addEventListener('pointerdown', onPointer, { capture: true })
  window.addEventListener('keydown', onKey, { capture: true })
  return () => {
    window.removeEventListener('pointerdown', onPointer, { capture: true })
    window.removeEventListener('keydown', onKey, { capture: true })
  }
}
