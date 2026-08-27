declare global {
  interface Window {
    hoplyraDesktop?: {
      isDesktop: boolean
      quit: () => void
    }
  }
}

/** True inside the Hoplyra Electron shell (preload or userAgent). */
export function isDesktopApp(): boolean {
  if (window.hoplyraDesktop?.isDesktop) return true
  return typeof navigator !== 'undefined' && /Electron/i.test(navigator.userAgent)
}

export function quitDesktopApp(): void {
  window.hoplyraDesktop?.quit()
}
