import { useEffect } from 'react'
import { Outlet } from 'react-router-dom'
import { useUiStore } from '@/store/ui'
import { Sidebar } from './Sidebar'
import { TopBar } from './TopBar'
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

export function AppShell() {
  useGlobalShortcuts()

  return (
    <div className="flex h-full overflow-hidden bg-base">
      <Sidebar />
      <div className="flex min-w-0 flex-1 flex-col">
        <TopBar />
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
