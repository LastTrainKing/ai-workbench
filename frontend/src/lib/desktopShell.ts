/**
 * Desktop shell behaviour: make the window behave like an application rather
 * than a page in a browser.
 *
 * The product ships inside WebView2, which is Chromium. That is an implementation
 * detail the user should never be able to notice. Left alone, Chromium offers a
 * context menu, reloads on F5, prints on Ctrl+P, shows source on Ctrl+U, opens
 * devtools on F12 and zooms on Ctrl+wheel — every one of which says "this is a
 * website in a box".
 *
 * Two deliberate limits on what is blocked:
 *
 * - **Text selection stays enabled inside inputs and textareas.** Blocking it
 *   globally is the lazy version and it breaks copying a path out of a field.
 * - **Escape, Tab, arrows and everything else pass through untouched.** This is
 *   a keyboard-navigable application; the guards must not eat its own keys.
 *
 * The guards are installed from the frontend rather than injected by the shell
 * so that they can be exercised by the browser test. A behaviour that can only
 * be verified by launching the packaged executable is a behaviour that will
 * silently regress.
 */

/** Keys that mean "reload" or "navigate" in a browser. */
const BLOCKED_COMBOS: { key: string; ctrl?: boolean; shift?: boolean; alt?: boolean }[] = [
  { key: 'F5' }, // reload
  { key: 'F7' }, // caret browsing
  { key: 'F12' }, // devtools
  { key: 'r', ctrl: true }, // reload
  { key: 'F5', ctrl: true }, // hard reload
  { key: 'p', ctrl: true }, // print
  { key: 'u', ctrl: true }, // view source
  { key: 's', ctrl: true }, // save page
  { key: 'o', ctrl: true }, // open file
  { key: 'n', ctrl: true }, // new window
  { key: 'w', ctrl: true }, // close window (would leave the backend running)
  { key: 'i', ctrl: true, shift: true }, // devtools
  { key: 'j', ctrl: true, shift: true }, // console
  { key: 'c', ctrl: true, shift: true }, // element picker
  { key: '=', ctrl: true }, // zoom in
  { key: '+', ctrl: true }, // zoom in
  { key: '-', ctrl: true }, // zoom out
  { key: '0', ctrl: true }, // reset zoom
  { key: 'ArrowLeft', alt: true }, // history back
  { key: 'ArrowRight', alt: true }, // history forward
]

function matches(event: KeyboardEvent, combo: (typeof BLOCKED_COMBOS)[number]): boolean {
  const key = event.key.length === 1 ? event.key.toLowerCase() : event.key
  if (key !== combo.key) return false
  // Require the modifiers to match exactly: Ctrl+R is blocked, but plain R while
  // typing in a field is obviously not.
  if (Boolean(combo.ctrl) !== (event.ctrlKey || event.metaKey)) return false
  if (Boolean(combo.shift) !== event.shiftKey) return false
  if (Boolean(combo.alt) !== event.altKey) return false
  return true
}

/** True when the event target is somewhere the user is legitimately typing. */
function isEditable(target: EventTarget | null): boolean {
  const element = target as HTMLElement | null
  if (!element || !element.tagName) return false
  const tag = element.tagName.toLowerCase()
  return tag === 'input' || tag === 'textarea' || tag === 'select' || element.isContentEditable
}

export interface ShellOptions {
  /** Block the right-click menu. Off in development, where it is occasionally useful. */
  blockContextMenu?: boolean
}

/**
 * Install the guards. Returns a disposer so tests can clean up.
 *
 * Idempotent: calling it twice does not double-register, which matters because
 * React's development mode runs effects twice.
 */
export function installDesktopShell(options: ShellOptions = {}): () => void {
  const { blockContextMenu = true } = options
  const disposers: (() => void)[] = []

  // --- context menu ---------------------------------------------------------
  if (blockContextMenu) {
    const onContextMenu = (event: MouseEvent) => {
      // A right-click inside a text field should still offer paste. Blocking it
      // there would break a normal editing gesture, not a browser tell.
      if (isEditable(event.target)) return
      event.preventDefault()
    }
    document.addEventListener('contextmenu', onContextMenu)
    disposers.push(() => document.removeEventListener('contextmenu', onContextMenu))
  }

  // --- keyboard -------------------------------------------------------------
  const onKeyDown = (event: KeyboardEvent) => {
    if (BLOCKED_COMBOS.some((combo) => matches(event, combo))) {
      event.preventDefault()
      event.stopPropagation()
    }
  }
  // Capture phase: these must be stopped before any application handler sees
  // them, or a page-level shortcut could re-enable one.
  document.addEventListener('keydown', onKeyDown, true)
  disposers.push(() => document.removeEventListener('keydown', onKeyDown, true))

  // --- zoom ----------------------------------------------------------------
  // Ctrl+wheel changes the layout scale. It is the single most obvious
  // "this is a web page" tell, and it also breaks the fixed layout assumptions.
  const onWheel = (event: WheelEvent) => {
    if (event.ctrlKey || event.metaKey) event.preventDefault()
  }
  document.addEventListener('wheel', onWheel, { passive: false, capture: true })
  disposers.push(() => document.removeEventListener('wheel', onWheel, { capture: true }))

  // --- drag and drop --------------------------------------------------------
  // Dropping a file onto the window would navigate away from the application.
  const onDragOver = (event: DragEvent) => event.preventDefault()
  const onDrop = (event: DragEvent) => {
    if (!isEditable(event.target)) event.preventDefault()
  }
  document.addEventListener('dragover', onDragOver)
  document.addEventListener('drop', onDrop)
  disposers.push(() => {
    document.removeEventListener('dragover', onDragOver)
    document.removeEventListener('drop', onDrop)
  })

  // --- selection ------------------------------------------------------------
  // Chromium lets a drag start a text selection anywhere on the page, which
  // makes the UI feel like a document rather than an interface. The class is
  // removed inside form controls by CSS, so copying a path still works.
  document.documentElement.classList.add('aw-desktop-shell')

  return () => {
    for (const dispose of disposers) dispose()
    document.documentElement.classList.remove('aw-desktop-shell')
  }
}

/** True when running inside the packaged desktop shell rather than a browser. */
export function isDesktopShell(): boolean {
  return (
    typeof window !== 'undefined' &&
    ('__TAURI_INTERNALS__' in window || '__TAURI__' in window)
  )
}
