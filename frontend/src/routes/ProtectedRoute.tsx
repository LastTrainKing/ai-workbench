import { Navigate, Outlet, useLocation } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { Sparkles, TriangleAlert } from 'lucide-react'
import { useAuthStore } from '@/store/auth'
import { contextApi } from '@/lib/contextApi'
import { Button } from '@/components/ui/Button'
import { InlineAlert } from '@/components/ui/feedback'

function Splash() {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-3 bg-base">
      <span className="flex size-9 items-center justify-center rounded-lg bg-accent text-accent-fg">
        <Sparkles className="size-4" />
      </span>
      <p className="text-xs text-muted">正在恢复会话…</p>
    </div>
  )
}

/** Redirects unauthenticated visitors to `/login`, preserving the intended path. */
export function ProtectedRoute() {
  const token = useAuthStore((state) => state.token)
  const status = useAuthStore((state) => state.status)
  const location = useLocation()

  if (!token) {
    return <Navigate to="/login" replace state={{ from: location.pathname + location.search }} />
  }

  if (status === 'idle' || status === 'loading') return <Splash />

  return <Outlet />
}

/** Keeps signed-in users away from the auth screens. */
export function PublicOnlyRoute() {
  const token = useAuthStore((state) => state.token)
  const status = useAuthStore((state) => state.status)

  if (token && status === 'authenticated') return <Navigate to="/" replace />
  return <Outlet />
}

/**
 * Sends anyone who has not been through the first-run wizard to it.
 *
 * One implementation, because there is one fact: `onboarded` on the profile. Register,
 * log in, an account that predates the wizard, a direct navigation to `/` — all of them
 * are asking the same question, and they all get their answer here.
 *
 * The login page used to decide this for itself, which is exactly why a new account was
 * dropped into the main UI and never saw the wizard: the register and login branches
 * shared one `navigate(redirectTo)` call, and `redirectTo` is `/` for both.
 *
 * `/welcome` sits *outside* this guard in `App.tsx`, or the redirect would loop.
 */
export function RequireOnboarding() {
  const location = useLocation()
  const userId = useAuthStore((state) => state.user?.id)

  const query = useQuery({
    // Keyed by user id. A cached `onboarded: true` from the previous session must never
    // be served to a different account — sign out, register, and the key changes.
    queryKey: ['onboarding', userId],
    queryFn: () => contextApi.contextStats(),
    enabled: Boolean(userId),
  })

  if (!userId) return <Splash />
  if (query.isPending) return <Splash />

  if (query.isError) {
    // Not swallowed, and not defaulted. Guessing `onboarded` either way is wrong: guess
    // true and a new account never meets the product; guess false and every returning
    // user is shown the wizard again. So the failure is reported, with the real message
    // and a way out of it.
    return (
      <div className="flex h-full items-center justify-center p-6">
        <div className="w-full max-w-md space-y-3">
          <InlineAlert tone="danger" title="读不到你的档案状态">
            {query.error instanceof Error ? query.error.message : String(query.error)}
          </InlineAlert>
          <div className="flex items-center gap-2">
            <Button variant="primary" size="sm" onClick={() => query.refetch()}>
              <TriangleAlert className="size-3.5" />
              重试
            </Button>
          </div>
        </div>
      </div>
    )
  }

  if (!query.data.onboarded) {
    return <Navigate to="/welcome" replace state={{ from: location.pathname }} />
  }

  return <Outlet />
}
