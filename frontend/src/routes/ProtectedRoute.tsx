import { Navigate, Outlet, useLocation } from 'react-router-dom'
import { Sparkles } from 'lucide-react'
import { useAuthStore } from '@/store/auth'

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
