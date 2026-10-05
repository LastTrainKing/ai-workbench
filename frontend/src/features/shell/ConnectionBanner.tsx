/**
 * The connection banner.
 *
 * One line, at the top of the shell, that answers "is it me or is it it". It is
 * deliberately quiet — a thin strip, not a modal, and it never blocks the UI.
 * A desktop application that interrupts you because a background service
 * restarted is worse than one that tells you and gets on with it.
 *
 * It also reports the time of the last successful contact, which is the detail
 * that actually helps: "3 seconds ago" means keep waiting, "14:32" means the
 * backend has been gone for an hour.
 */

import { AlertTriangle, Loader2, RefreshCw } from 'lucide-react'

import { Button } from '@/components/ui/Button'
import { formatLastSeen } from '@/features/shell/useConnection'
import type { ConnectionState } from '@/features/shell/useConnection'
import { cn } from '@/lib/utils'

export function ConnectionBanner({ connection }: { connection: ConnectionState }) {
  if (connection.online) return null

  const { failures, lastSeen, retryIn, checkNow } = connection
  const stillTrying = failures < 6

  return (
    <div
      role="status"
      aria-live="polite"
      data-testid="connection-banner"
      className={cn(
        'flex items-center gap-2 border-b px-4 py-1.5 text-[12.5px]',
        'border-warn/30 bg-warn/10 text-warn',
      )}
    >
      <span className="relative flex size-2 shrink-0">
        <span className="absolute inline-flex size-full animate-ping rounded-full bg-warn/60" />
        <span className="relative inline-flex size-2 rounded-full bg-warn" />
      </span>

      <span className="font-medium">与后端失去连接</span>
      <span className="text-warn/70">·</span>

      {stillTrying ? (
        <span className="flex items-center gap-1.5 text-warn/90">
          <Loader2 className="size-3 animate-spin" />
          正在重试
          {retryIn > 0 && <span className="text-warn/70">{retryIn}s 后</span>}
        </span>
      ) : (
        <span className="flex items-center gap-1.5 text-warn/90">
          <AlertTriangle className="size-3" />
          已重试 {failures} 次仍未恢复
        </span>
      )}

      <span className="text-warn/70">·</span>
      <span className="text-warn/90">
        最后成功连接 <span className="font-mono">{formatLastSeen(lastSeen)}</span>
      </span>

      <span className="ml-auto text-warn/70">
        已加载的内容仍可查看，恢复后会自动刷新
      </span>

      <Button size="xs" variant="ghost" onClick={checkNow} className="text-warn hover:text-warn">
        <RefreshCw className="size-3" /> 立即重试
      </Button>
    </div>
  )
}
