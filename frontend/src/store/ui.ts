import { create } from 'zustand'

export type Theme = 'dark' | 'light'

const THEME_KEY = 'aiw.theme'
const SHELL_KEY = 'aiw.shell'

interface ShellPrefs {
  sidebarCollapsed: boolean
  rightPanelOpen: boolean
}

function readTheme(): Theme {
  try {
    const stored = localStorage.getItem(THEME_KEY)
    return stored === 'light' ? 'light' : 'dark'
  } catch {
    return 'dark'
  }
}

function readShellPrefs(): ShellPrefs {
  try {
    const raw = localStorage.getItem(SHELL_KEY)
    if (raw) {
      const parsed = JSON.parse(raw) as Partial<ShellPrefs>
      return {
        sidebarCollapsed: Boolean(parsed.sidebarCollapsed),
        rightPanelOpen: parsed.rightPanelOpen !== false,
      }
    }
  } catch {
    /* ignore */
  }
  return { sidebarCollapsed: false, rightPanelOpen: true }
}

function applyTheme(theme: Theme): void {
  if (typeof document === 'undefined') return
  document.documentElement.setAttribute('data-theme', theme)
  try {
    localStorage.setItem(THEME_KEY, theme)
  } catch {
    /* ignore */
  }
}

function persistShell(prefs: ShellPrefs): void {
  try {
    localStorage.setItem(SHELL_KEY, JSON.stringify(prefs))
  } catch {
    /* ignore */
  }
}

const initialTheme = readTheme()
applyTheme(initialTheme)

const initialShell = readShellPrefs()

interface UiState extends ShellPrefs {
  theme: Theme
  mobileNavOpen: boolean
  commandPaletteOpen: boolean
  shortcutsOpen: boolean
  rightPanelTab: 'assistant' | 'context'
  setTheme: (theme: Theme) => void
  toggleTheme: () => void
  toggleSidebar: () => void
  setMobileNav: (open: boolean) => void
  toggleRightPanel: () => void
  setRightPanelTab: (tab: 'assistant' | 'context') => void
  setCommandPalette: (open: boolean) => void
  toggleCommandPalette: () => void
  setShortcuts: (open: boolean) => void
}

export const useUiStore = create<UiState>((set, get) => ({
  theme: initialTheme,
  sidebarCollapsed: initialShell.sidebarCollapsed,
  rightPanelOpen: initialShell.rightPanelOpen,
  mobileNavOpen: false,
  commandPaletteOpen: false,
  shortcutsOpen: false,
  rightPanelTab: 'assistant',

  setTheme(theme) {
    applyTheme(theme)
    set({ theme })
  },

  toggleTheme() {
    const next: Theme = get().theme === 'dark' ? 'light' : 'dark'
    applyTheme(next)
    set({ theme: next })
  },

  toggleSidebar() {
    const sidebarCollapsed = !get().sidebarCollapsed
    persistShell({ sidebarCollapsed, rightPanelOpen: get().rightPanelOpen })
    set({ sidebarCollapsed })
  },

  setMobileNav(open) {
    set({ mobileNavOpen: open })
  },

  toggleRightPanel() {
    const rightPanelOpen = !get().rightPanelOpen
    persistShell({ sidebarCollapsed: get().sidebarCollapsed, rightPanelOpen })
    set({ rightPanelOpen })
  },

  setRightPanelTab(tab) {
    set({ rightPanelTab: tab, rightPanelOpen: true })
  },

  setCommandPalette(open) {
    set({ commandPaletteOpen: open })
  },

  toggleCommandPalette() {
    set({ commandPaletteOpen: !get().commandPaletteOpen })
  },

  setShortcuts(open) {
    set({ shortcutsOpen: open })
  },
}))
