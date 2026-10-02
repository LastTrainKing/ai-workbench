import { NavLink } from 'react-router-dom'
import { LogOut, Moon, PanelLeftClose, PanelLeftOpen, Sparkles, Sun, X } from 'lucide-react'
import { cn } from '@/lib/utils'
import { useAuthStore } from '@/store/auth'
import { useUiStore } from '@/store/ui'
import { useToastStore } from '@/store/toast'
import { Avatar } from '@/components/ui/primitives'
import { IconButton } from '@/components/ui/Button'
import { NAV_SECTIONS, SETTINGS_ITEM, type NavItem } from './nav'

function NavEntry({ item, collapsed, onNavigate }: { item: NavItem; collapsed: boolean; onNavigate: () => void }) {
  const Icon = item.icon
  return (
    <NavLink
      to={item.to}
      end={!item.prefix}
      onClick={onNavigate}
      title={collapsed ? item.label : undefined}
      className={({ isActive }) =>
        cn(
          'group flex items-center gap-2.5 rounded-md px-2 py-1.5 text-sm transition-colors',
          collapsed && 'justify-center px-0',
          isActive
            ? 'bg-accent-soft font-medium text-accent'
            : 'text-muted hover:bg-elevated hover:text-primary',
        )
      }
    >
      <Icon className="size-4 shrink-0" />
      {!collapsed ? <span className="truncate">{item.label}</span> : null}
    </NavLink>
  )
}

function SidebarBody({ collapsed, onNavigate }: { collapsed: boolean; onNavigate: () => void }) {
  const user = useAuthStore((state) => state.user)
  const logout = useAuthStore((state) => state.logout)
  const theme = useUiStore((state) => state.theme)
  const toggleTheme = useUiStore((state) => state.toggleTheme)
  const toggleSidebar = useUiStore((state) => state.toggleSidebar)
  const toast = useToastStore((state) => state.success)

  return (
    <div className="flex h-full flex-col bg-surface">
      {/* brand */}
      <div className={cn('flex h-12 shrink-0 items-center gap-2 border-b border-subtle px-3', collapsed && 'justify-center px-0')}>
        <span className="flex size-6 shrink-0 items-center justify-center rounded bg-accent text-accent-fg">
          <Sparkles className="size-3.5" />
        </span>
        {!collapsed ? (
          <span className="truncate text-sm font-semibold tracking-tight text-primary">AI Workbench</span>
        ) : null}
      </div>

      {/* nav */}
      <nav className="aiw-scroll flex-1 space-y-4 px-2 py-3">
        {NAV_SECTIONS.map((section) => (
          <div key={section.title}>
            {!collapsed ? (
              <p className="mb-1 px-2 text-2xs font-semibold uppercase tracking-wider text-faint">
                {section.title}
              </p>
            ) : (
              <div className="mx-2 mb-1.5 h-px bg-subtle" />
            )}
            <div className="space-y-0.5">
              {section.items.map((item) => (
                <NavEntry key={item.to} item={item} collapsed={collapsed} onNavigate={onNavigate} />
              ))}
            </div>
          </div>
        ))}
      </nav>

      {/* footer */}
      <div className="shrink-0 border-t border-subtle p-2">
        <NavEntry item={SETTINGS_ITEM} collapsed={collapsed} onNavigate={onNavigate} />

        <div className={cn('mt-1 flex items-center gap-1', collapsed && 'flex-col')}>
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
