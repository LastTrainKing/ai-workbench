import { useEffect, useRef, useState } from 'react'
import { useNavigate, useLocation } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import {
  Command,
  Menu,
  Moon,
  PanelRightClose,
  PanelRightOpen,
  Search,
  Settings,
  Sun,
  TriangleAlert,
} from 'lucide-react'
import { api } from '@/lib/api'
import { cn } from '@/lib/utils'
import { useUiStore } from '@/store/ui'
import { IconButton } from '@/components/ui/Button'

/** Honest status pill: the offline engine is always surfaced, never hidden. */
export function ProviderBadge({ className }: { className?: string }) {
  const { data, isError } = useQuery({
    queryKey: ['health', 'deps'],
    queryFn: ({ signal }) => api.health.deps(signal),
    // This label answers "what is it running on right now", so it is fetched
    // fresh rather than cached: a 60s staleTime meant saving a key left the bar
    // contradicting the page the user was standing on. Settings invalidates
    // ['health'] on save; this makes the readable window short either way.
    staleTime: 5_000,
    refetchInterval: 30_000,
    refetchOnWindowFocus: true,
    retry: 1,
  })

  if (isError) {
    return (
      <span
        className={cn(
          'inline-flex items-center gap-1.5 rounded border border-danger/40 bg-danger-soft px-1.5 py-0.5 text-2xs font-medium text-danger',
          className,
        )}
        title="无法读取 /health/deps"
      >
        <TriangleAlert className="size-3" />
        后端不可用
      </span>
    )
  }

  if (!data) return null

  const degraded = data.ai_degraded
  const model = data.ai?.llm?.model || data.ai?.llm?.kind || 'unknown'
  const label = degraded ? '离线启发式引擎（未配置云端 Key）' : `${data.ai?.llm?.kind ?? 'llm'} · ${model}`

  return (
    <span
      className={cn(
        'inline-flex max-w-[260px] items-center gap-1.5 truncate rounded border px-1.5 py-0.5 text-2xs font-medium',
        degraded
          ? 'border-warn/40 bg-warn-soft text-warn'
          : 'border-success/40 bg-success-soft text-success',
        className,
      )}
      title={degraded ? data.ai?.llm?.reason || label : label}
    >
      <TriangleAlert className="size-3 shrink-0" />
      <span className="truncate">{label}</span>
    </span>
  )
}

export function TopBar() {
  const navigate = useNavigate()
  const setMobileNav = useUiStore((state) => state.setMobileNav)
  const theme = useUiStore((state) => state.theme)
  const toggleTheme = useUiStore((state) => state.toggleTheme)
  const rightPanelOpen = useUiStore((state) => state.rightPanelOpen)
  const toggleRightPanel = useUiStore((state) => state.toggleRightPanel)
  const setCommandPalette = useUiStore((state) => state.setCommandPalette)

  // The conversation screen gets a minimal bar: model status, theme, settings. The
  // global search box and the command-palette button are still there on every other
  // page, and Ctrl+K still opens the palette from anywhere — this is the top bar
  // matching the one screen that is supposed to feel like a chat window, not the
  // search going away.
  const { pathname } = useLocation()
  const minimal = pathname === '/'

  const [query, setQuery] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)

  // "/" focuses the global search unless the user is already typing somewhere.
  useEffect(() => {
    if (minimal) return undefined
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== '/') return
      const target = event.target as HTMLElement | null
      const tag = target?.tagName?.toLowerCase()
      if (tag === 'input' || tag === 'textarea' || target?.isContentEditable) return
      event.preventDefault()
      inputRef.current?.focus()
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [minimal])

  const submit = (event: React.FormEvent) => {
    event.preventDefault()
    const trimmed = query.trim()
    if (!trimmed) return
    navigate(`/search?q=${encodeURIComponent(trimmed)}`)
    setQuery('')
  }

  return (
    <header className="flex h-12 shrink-0 items-center gap-2 border-b border-subtle bg-surface px-3">
      <IconButton label="打开导航" size="sm" className="md:hidden" onClick={() => setMobileNav(true)}>
        <Menu className="size-4" />
      </IconButton>

      {minimal ? (
        <span className="truncate text-[13px] font-medium text-muted">跟它说一件你想完成的事</span>
      ) : (
        <form onSubmit={submit} className="relative min-w-0 flex-1 max-w-lg">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-faint" />
          <input
            ref={inputRef}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="全局检索文档片段…"
            aria-label="全局检索"
            className="aiw-input h-8 pl-8 pr-16"
          />
          <span className="pointer-events-none absolute right-2 top-1/2 hidden -translate-y-1/2 items-center gap-1 sm:flex">
            <kbd className="aiw-kbd font-sans">/</kbd>
          </span>
        </form>
      )}

      {!minimal && (
        <button
          type="button"
          onClick={() => setCommandPalette(true)}
          className="hidden h-8 items-center gap-2 rounded border border-subtle bg-inset px-2 text-xs text-muted transition-colors hover:border-strong hover:text-primary sm:inline-flex"
        >
          <Command className="size-3.5" />
          命令面板
          <span className="flex items-center gap-0.5">
            <kbd className="aiw-kbd">Ctrl</kbd>
            <kbd className="aiw-kbd">K</kbd>
          </span>
        </button>
      )}

      <div className="ml-auto flex items-center gap-1.5">
        <ProviderBadge className={minimal ? 'inline-flex' : 'hidden lg:inline-flex'} />
        <IconButton label="切换主题" size="sm" onClick={toggleTheme}>
          {theme === 'dark' ? <Sun className="size-4" /> : <Moon className="size-4" />}
        </IconButton>
        {minimal ? (
          <IconButton label="设置" size="sm" onClick={() => navigate('/settings')}>
            <Settings className="size-4" />
          </IconButton>
        ) : (
          <IconButton
            label={rightPanelOpen ? '收起右侧面板' : '展开右侧面板'}
            size="sm"
            className="hidden lg:inline-flex"
            onClick={toggleRightPanel}
          >
            {rightPanelOpen ? <PanelRightClose className="size-4" /> : <PanelRightOpen className="size-4" />}
          </IconButton>
        )}
      </div>
    </header>
  )
}
