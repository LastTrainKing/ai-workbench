import { useEffect } from 'react'
import { Outlet } from 'react-router-dom'
import { useUiStore } from '@/store/ui'
import { Sidebar } from './Sidebar'
import { TopBar } from './TopBar'
import { ConnectionBanner } from '@/features/shell/ConnectionBanner'
import { useConnection } from '@/features/shell/useConnection'
import { RightPanel } from './RightPanel'
import { CommandPalette } from '@/components/CommandPalette'
import { ShortcutsOverlay } from '@/components/ShortcutsOverlay'
import { ConfirmHost } from '@/components/ui/overlay'
import { Toaster } from '@/components/ui/feedback'

function useGlobalShortcuts() {
  const toggleCommandPalette = useUiStore((state) => state.toggleCommandPalette)
  const setCommandPalette = useUiStore((state) => state.setCommandPalette)
  const setShortcuts = useUiStore((state) => state.setShortcuts)

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null
      const tag = target?.tagName?.toLowerCase()
      const isTyping =
        tag === 'input' || tag === 'textarea' || tag === 'select' || target?.isContentEditable

      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault()
        toggleCommandPalette()
        return
      }

      if (isTyping) return

      if (event.key === '?') {
        event.preventDefault()
        setShortcuts(true)
      } else if (event.key === 'Escape') {
        setCommandPalette(false)
      }
    }

    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [toggleCommandPalette, setCommandPalette, setShortcuts])
}


/**
 * Native menu events.
 *
 * The menu lives in Rust because a desktop application has a menu bar; two of
 * its items open things that live in the frontend (the command palette) or need
 * a real dialog (about). Rather than reimplementing either in Rust, the shell
 * emits an event and this listens for it.
 *
 * The listener is registered only when the Tauri bridge exists, so the same code
 * runs unchanged in a browser during development.
 */
function useNativeMenu() {
  const setCommandPalette = useUiStore((state) => state.setCommandPalette)

  useEffect(() => {
    const bridge = (window as unknown as {
      __TAURI__?: { event?: { listen: (name: string, cb: (e: unknown) => void) => Promise<() => void> } }
    }).__TAURI__
    if (!bridge?.event?.listen) return undefined

    const disposers: (() => void)[] = []
    let cancelled = false

    void (async () => {
      const off1 = await bridge.event!.listen('menu://focus-search', () => setCommandPalette(true))
      const off2 = await bridge.event!.listen('menu://about', () => {
        // The version is the one thing worth showing; anything more is a
        // changelog nobody reads from a menu item.
        window.alert('AI Workbench 2.0.0\n\n本地优先的私人 AI Agent。')
      })
      if (cancelled) {
        off1()
        off2()
        return
      }
      disposers.push(off1, off2)
    })()

    return () => {
      cancelled = true
      for (const dispose of disposers) dispose()
    }
  }, [setCommandPalette])
}

export function AppShell() {
  useGlobalShortcuts()
  useNativeMenu()

  const connection = useConnection()

  return (
    <div className="flex h-full overflow-hidden bg-base">
      <Sidebar />
      <div className="flex min-w-0 flex-1 flex-col">
        <TopBar />
        <ConnectionBanner connection={connection} />
        <div className="flex min-h-0 flex-1">
          <main className="aiw-scroll min-w-0 flex-1">
            <Outlet />
          </main>
          <RightPanel />
        </div>
      </div>

      <CommandPalette />
      <ShortcutsOverlay />
      <ConfirmHost />
      <Toaster />
    </div>
  )
}
