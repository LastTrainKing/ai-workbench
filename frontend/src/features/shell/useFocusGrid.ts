/**
 * Keyboard focus management for a grid of cards.
 *
 * The home screen is meant to be navigable with the arrow keys, the way a
 * console dashboard is — but the abstraction matters more than the feature. The
 * brief asks for a FocusManager rather than scattered `onKeyDown` handlers, and
 * the reason is concrete: a second grid, or a gamepad, or a remote, should not
 * require touching every card component again.
 *
 * What this owns:
 *
 * - which item is focused, as an index into a flat list;
 * - arrow-key movement across a known column count, with the edges wrapping
 *   rather than dead-ending (a d-pad that stops at the last card feels broken);
 * - Enter to activate, Escape to release focus back to the page;
 * - hover to focus, so the mouse and the keyboard agree about where "here" is.
 *
 * What this deliberately does not own: what a card looks like, what activating
 * one does, and whether the grid is even visible. Those belong to the caller.
 *
 * The input handling is written against a small set of "intents" (move, commit,
 * cancel) rather than raw key codes, so binding a gamepad later means mapping its
 * buttons to the same three intents instead of reimplementing the grid.
 */

import { useCallback, useEffect, useRef, useState } from 'react'

export type FocusIntent = 'left' | 'right' | 'up' | 'down' | 'commit' | 'cancel'

/** Maps a keyboard event to an intent, or null if the key is not ours. */
export function intentFromKey(event: KeyboardEvent): FocusIntent | null {
  switch (event.key) {
    case 'ArrowLeft':
      return 'left'
    case 'ArrowRight':
      return 'right'
    case 'ArrowUp':
      return 'up'
    case 'ArrowDown':
      return 'down'
    case 'Enter':
    case ' ':
      return 'commit'
    case 'Escape':
      return 'cancel'
    default:
      return null
  }
}

export interface FocusGridOptions {
  /** How many items are in the grid. */
  count: number
  /** Columns at the current breakpoint. Movement wraps against this. */
  columns: number
  /** Called on Enter or Space. */
  onCommit?: (index: number) => void
  /** Called on Escape, after focus is released. */
  onCancel?: () => void
  /** When false, the grid ignores keys entirely. */
  enabled?: boolean
}

export interface FocusGrid {
  /** -1 means nothing is focused; the grid is idle. */
  activeIndex: number
  setActiveIndex: (index: number) => void
  /** True when the focused item should show its ring. */
  isFocused: (index: number) => boolean
  /** Props to spread onto each item. */
  itemProps: (index: number) => {
    onMouseEnter: () => void
    onFocus: () => void
    tabIndex: number
    'data-focused': boolean
  }
  containerProps: {
    onKeyDown: (event: React.KeyboardEvent) => void
    role: string
  }
}

export function useFocusGrid({
  count,
  columns,
  onCommit,
  onCancel,
  enabled = true,
}: FocusGridOptions): FocusGrid {
  const [activeIndex, setActiveIndex] = useState(-1)
  const columnsRef = useRef(Math.max(1, columns))
  columnsRef.current = Math.max(1, columns)

  // Clamp when the list shrinks (a filter, a deleted item), so the ring cannot
  // end up pointing at nothing.
  useEffect(() => {
    if (activeIndex >= count) setActiveIndex(count > 0 ? count - 1 : -1)
  }, [count, activeIndex])

  const move = useCallback(
    (intent: FocusIntent) => {
      if (count <= 0) return
      const cols = columnsRef.current
      setActiveIndex((current) => {
        // First keypress from idle focuses the first card rather than jumping.
        if (current < 0) return 0
        switch (intent) {
          case 'left':
            return current % cols === 0 ? current + cols - 1 : current - 1
          case 'right':
            return current % cols === cols - 1 ? current - cols + 1 : current + 1
          case 'up': {
            const next = current - cols
            return next < 0 ? Math.min(current + cols, count - 1) : next
          }
          case 'down': {
            const next = current + cols
            return next >= count ? current % cols : next
          }
          default:
            return current
        }
      })
    },
    [count],
  )

  const handleIntent = useCallback(
    (intent: FocusIntent) => {
      if (intent === 'commit') {
        if (activeIndex >= 0) onCommit?.(activeIndex)
        return
      }
      if (intent === 'cancel') {
        setActiveIndex(-1)
        onCancel?.()
        return
      }
      move(intent)
    },
    [activeIndex, move, onCancel, onCommit],
  )

  // Listen on the document, not on the container.
  //
  // A container listener only fires once something inside it has DOM focus, and
  // at rest nothing does — so the first arrow press did nothing at all. A console
  // dashboard is navigable from wherever you happen to be, which means the
  // listener has to be global while the grid is on screen.
  //
  // Two things keep that from being rude: keys are ignored while the user is
  // typing, and only the six keys the grid actually uses are intercepted, so Tab
  // and every application shortcut still work.
  useEffect(() => {
    if (!enabled) return undefined

    const onKeyDown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null
      const tag = target?.tagName?.toLowerCase()
      if (tag === 'input' || tag === 'textarea' || tag === 'select' || target?.isContentEditable) {
        return
      }
      const intent = intentFromKey(event)
      if (!intent) return
      event.preventDefault()
      handleIntent(intent)
    }

    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [enabled, handleIntent])

  const onKeyDown = useCallback(() => {
    // Kept on the container so a caller that renders inside a focus trap still
    // works; the document listener above is what actually drives the grid.
  }, [])

  return {
    activeIndex,
    setActiveIndex,
    isFocused: (index: number) => index === activeIndex,
    itemProps: (index: number) => ({
      onMouseEnter: () => setActiveIndex(index),
      onFocus: () => setActiveIndex(index),
      tabIndex: index === activeIndex ? 0 : -1,
      'data-focused': index === activeIndex,
    }),
    containerProps: { onKeyDown, role: 'grid' },
  }
}

/**
 * Columns currently rendered, derived from the container width.
 *
 * Read from the DOM rather than from a media query so the grid and the movement
 * logic cannot disagree — a mismatch there means pressing Down skips a row.
 */
export function useColumnCount(ref: React.RefObject<HTMLElement | null>, fallback = 3): number {
  const [columns, setColumns] = useState(fallback)

  useEffect(() => {
    const node = ref.current
    if (!node) return undefined

    const measure = () => {
      const style = window.getComputedStyle(node)
      const template = style.gridTemplateColumns
      // "repeat(3, ...)" resolves to three track sizes, so counting them is
      // exact where parsing the CSS text is guesswork.
      const tracks = template.split(' ').filter((part) => part.trim() !== '')
      setColumns(Math.max(1, tracks.length || fallback))
    }

    measure()
    const observer = new ResizeObserver(measure)
    observer.observe(node)
    return () => observer.disconnect()
  }, [ref, fallback])

  return columns
}
