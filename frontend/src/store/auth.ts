import { create } from 'zustand'
import { api, setAccessToken, toApiError } from '@/lib/api'
import type { LoginPayload, RegisterPayload, User, UserUpdatePayload } from '@/lib/types'

const TOKEN_KEY = 'aiw.token'
const USER_KEY = 'aiw.user'

type AuthStatus = 'idle' | 'loading' | 'authenticated' | 'unauthenticated'

interface AuthState {
  user: User | null
  token: string | null
  status: AuthStatus
  error: string | null
  login: (payload: LoginPayload) => Promise<void>
  register: (payload: RegisterPayload) => Promise<void>
  logout: () => Promise<void>
  hydrate: () => Promise<void>
  setUser: (user: User) => void
  updateProfile: (payload: UserUpdatePayload) => Promise<void>
  clear: () => void
}

function readStoredToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY)
  } catch {
    return null
  }
}

function readStoredUser(): User | null {
  try {
    const raw = localStorage.getItem(USER_KEY)
    return raw ? (JSON.parse(raw) as User) : null
  } catch {
    return null
  }
}

function persist(token: string | null, user: User | null): void {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token)
    else localStorage.removeItem(TOKEN_KEY)
    if (user) localStorage.setItem(USER_KEY, JSON.stringify(user))
    else localStorage.removeItem(USER_KEY)
  } catch {
    /* storage can be unavailable (private mode) — the session simply won't persist */
  }
}

const initialToken = readStoredToken()
setAccessToken(initialToken)

export const useAuthStore = create<AuthState>((set, get) => ({
  user: readStoredUser(),
  token: initialToken,
  status: initialToken ? 'idle' : 'unauthenticated',
  error: null,

  async login(payload) {
    set({ status: 'loading', error: null })
    try {
      const { data } = await api.auth.login(payload)
      setAccessToken(data.access_token)
      persist(data.access_token, data.user)
      set({ token: data.access_token, user: data.user, status: 'authenticated', error: null })
    } catch (error) {
      const apiError = toApiError(error)
      set({ status: 'unauthenticated', error: apiError.message })
      throw apiError
    }
  },

  async register(payload) {
    set({ status: 'loading', error: null })
    try {
      const { data } = await api.auth.register(payload)
      setAccessToken(data.access_token)
      persist(data.access_token, data.user)
      set({ token: data.access_token, user: data.user, status: 'authenticated', error: null })
    } catch (error) {
      const apiError = toApiError(error)
      set({ status: 'unauthenticated', error: apiError.message })
      throw apiError
    }
  },

  async logout() {
    try {
      if (get().token) await api.auth.logout()
    } catch {
      /* the endpoint is only an acknowledgement — never block sign-out on it */
    }
    get().clear()
  },

  async hydrate() {
    const token = get().token
    if (!token) {
      set({ status: 'unauthenticated' })
      return
    }
    set({ status: 'loading' })
    try {
      const user = await api.auth.me()
      persist(token, user)
      set({ user, status: 'authenticated', error: null })
    } catch (error) {
      const apiError = toApiError(error)
      // A rejected token means the session is gone; a network blip does not.
      if (apiError.status === 401 || apiError.status === 0) {
        setAccessToken(null)
        persist(null, null)
        set({ token: null, user: null, status: 'unauthenticated', error: apiError.message })
      } else {
        set({ status: 'authenticated', error: apiError.message })
      }
    }
  },

  setUser(user) {
    persist(get().token, user)
    set({ user })
  },

  async updateProfile(payload) {
    const user = await api.auth.updateProfile(payload)
    persist(get().token, user.data)
    set({ user: user.data })
  },

  clear() {
    setAccessToken(null)
    persist(null, null)
    set({ token: null, user: null, status: 'unauthenticated', error: null })
  },
}))

/**
 * Wired into the API client from `main.tsx` so any 401 (expired/revoked token)
 * immediately drops the session and the router guard redirects to `/login`.
 */
export function handleUnauthorized(): void {
  useAuthStore.getState().clear()
}
