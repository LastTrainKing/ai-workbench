import { NavLink } from 'react-router-dom'
import {
  ChevronRight,
  LogOut,
  Moon,
  PanelLeftClose,
  PanelLeftOpen,
  Settings,
  Sparkles,
  Sun,
  X,
} from 'lucide-react'
import { useState } from 'react'

import { cn } from '@/lib/utils'
import { useAuthStore } from '@/store/auth'
import { useUiStore } from '@/store/ui'
import { useToastStore } from '@/store/toast'
import { Avatar } from '@/components/ui/primitives'
import { IconButton } from '@/components/ui/Button'
import { ConversationList } from '@/features/conversation/ConversationList'
import { MENU_SECTIONS, type NavItem } from './nav'

function NavEntry({ item, onNavigate }: { item: NavItem; onNavigate: () => void }) {
  const Icon = item.icon
  return (
    <NavLink
      to={item.to}
      end={!item.prefix}
      onClick={onNavigate}
      className={({ isActive }) =>
        cn(
          'flex items-center gap-2.5 rounded-md px-2 py-1.5 text-[13px] transition-colors',
          isActive
            ? 'bg-accent-soft font-medium text-accent'
            : 'text-muted hover:bg-elevated hover:text-primary',
        )
      }
    >
      <Icon className="size-3.5 shrink-0" />
      <span className="truncate">{item.label}</span>
    </NavLink>
  )
}

/**
 * Everything that is not the conversation, behind one gear.
 *
 * See `nav.ts` for why this exists and what the user asked for. The menu is a real list of
 * links — every route it names still resolves — rather than a set of labels that go
 * nowhere, which is the shape a "we hid the menus" change usually takes.
 */
function MenuPanel({ onNavigate }: { onNavigate: () => void }) {
  const [openSections, setOpenSections] = useState<Record<string, boolean>>({})

  return (
    <div
      data-testid="app-menu"
      className="aiw-scroll absolute bottom-2 left-2 z-30 max-h-[70vh] w-[210px] overflow-y-auto rounded-xl border border-border bg-surface p-2 shadow-lg"
    >
      {MENU_SECTIONS.map((section) => {
        const closed = Boolean(section.collapsible) && !openSections[section.title]
        return (
          <div key={section.title} className="mb-1.5 last:mb-0">
            {section.collapsible ? (
              <button
                type="button"
                onClick={() =>
                  setOpenSections((prev) => ({ ...prev, [section.title]: !prev[section.title] }))
                }
                aria-expanded={!closed}
                className="mb-1 flex w-full items-center gap-1 px-2 text-2xs font-semibold uppercase tracking-wider text-faint transition-colors hover:text-muted"
              >
                <ChevronRight className={cn('size-3 transition-transform', !closed && 'rotate-90')} />
                {section.title}
              </button>
            ) : (
              <p className="mb-1 px-2 text-2xs font-semibold uppercase tracking-wider text-faint">
                {section.title}
              </p>
            )}
            {!closed ? (
              <div className="space-y-0.5">
                {section.items.map((item) => (
                  <NavEntry key={item.to} item={item} onNavigate={onNavigate} />
                ))}
              </div>
            ) : null}
          </div>
        )
      })}
    </div>
  )
}

function SidebarBody({ collapsed, onNavigate }: { collapsed: boolean; onNavigate: () => void }) {
  const user = useAuthStore((state) => state.user)
  const logout = useAuthStore((state) => state.logout)
  const theme = useUiStore((state) => state.theme)
  const toggleTheme = useUiStore((state) => state.toggleTheme)
  const toggleSidebar = useUiStore((state) => state.toggleSidebar)
  const toast = useToastStore((state) => state.success)
  const [menuOpen, setMenuOpen] = useState(false)

  return (
    <div className="relative flex h-full flex-col bg-surface">
      {/* brand */}
      <div
        className={cn(
          'flex h-12 shrink-0 items-center gap-2 border-b border-subtle px-3',
          collapsed && 'justify-center px-0',
        )}
      >
        <span className="flex size-6 shrink-0 items-center justify-center rounded bg-accent text-accent-fg">
          <Sparkles className="size-3.5" />
        </span>
        {!collapsed ? (
          <span className="truncate text-sm font-semibold tracking-tight text-primary">
            AI Workbench
          </span>
        ) : null}
      </div>

      <ConversationList collapsed={collapsed} onNavigate={onNavigate} />

      {menuOpen ? (
        <>
          {/* Click-away. A menu that only closes by clicking its own button is a trap. */}
          <button
            type="button"
            aria-label="关闭菜单"
            className="absolute inset-0 z-20 cursor-default"
            onClick={() => setMenuOpen(false)}
          />
          <MenuPanel onNavigate={onNavigate} />
        </>
      ) : null}

      {/* footer */}
      <div className="shrink-0 border-t border-subtle p-2">
        <div className={cn('flex items-center gap-1', collapsed && 'flex-col')}>
          <IconButton
            label="菜单"
            size="xs"
            data-testid="open-app-menu"
            onClick={() => setMenuOpen((v) => !v)}
          >
            <Settings className="size-3.5" />
          </IconButton>
          <IconButton label="切换主题" size="xs" onClick={toggleTheme}>
            {theme === 'dark' ? <Sun className="size-3.5" /> : <Moon className="size-3.5" />}
          </IconButton>
          <IconButton
            label={collapsed ? '展开侧边栏' : '收起侧边栏'}
            size="xs"
            onClick={toggleSidebar}
            className="hidden md:inline-flex"
          >
            {collapsed ? <PanelLeftOpen className="size-3.5" /> : <PanelLeftClose className="size-3.5" />}
          </IconButton>
        </div>

        <div
          className={cn(
            'mt-2 flex items-center gap-2 rounded-md border border-subtle bg-elevated px-2 py-1.5',
            collapsed && 'justify-center border-transparent bg-transparent px-0',
          )}
        >
          <Avatar name={user?.display_name || user?.username || '?'} color={user?.avatar_color} size={24} />
          {!collapsed ? (
            <>
              <div className="min-w-0 flex-1">
                <p className="truncate text-xs font-medium text-primary">
                  {user?.display_name || user?.username}
                </p>
                <p className="truncate text-2xs text-faint">{user?.email}</p>
              </div>
              <IconButton
                label="退出登录"
                size="xs"
                onClick={async () => {
                  await logout()
                  toast('已退出登录')
                }}
              >
                <LogOut className="size-3.5" />
              </IconButton>
            </>
          ) : null}
        </div>
      </div>
    </div>
  )
}

export function Sidebar() {
  const collapsed = useUiStore((state) => state.sidebarCollapsed)
  const mobileNavOpen = useUiStore((state) => state.mobileNavOpen)
  const setMobileNav = useUiStore((state) => state.setMobileNav)

  return (
    <>
      {/* desktop rail */}
      <aside
        className={cn(
          'hidden shrink-0 border-r border-subtle transition-[width] duration-150 md:block',
          collapsed ? 'w-[52px]' : 'w-[212px]',
        )}
      >
        <SidebarBody collapsed={collapsed} onNavigate={() => undefined} />
      </aside>

      {/* mobile overlay drawer */}
      {mobileNavOpen ? (
        <div className="fixed inset-0 z-50 md:hidden">
          <div
            className="absolute inset-0 bg-overlay animate-fade-in"
            onClick={() => setMobileNav(false)}
            aria-hidden
          />
          <div className="relative h-full w-[240px] border-r border-subtle animate-slide-in-right">
            <button
              type="button"
              aria-label="关闭导航"
              onClick={() => setMobileNav(false)}
              className="absolute right-2 top-2.5 z-10 rounded p-1 text-faint hover:bg-elevated hover:text-primary"
            >
              <X className="size-4" />
            </button>
            <SidebarBody collapsed={false} onNavigate={() => setMobileNav(false)} />
          </div>
        </div>
      ) : null}
    </>
  )
}
