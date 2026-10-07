/**
 * A produced file, and the two decisions that belong to it.
 *
 * Extracted from `WorkbenchPage` when the conversation page started showing
 * deliverables inline. Two implementations of "the card with 下载 / 打开 / 接受 /
 * 退回 on it" would drift, and the drift would be invisible until one of them lost
 * a button — which is precisely the class of bug this round was about.
 *
 * The two judgements stay separate, and the card is where that has to be visible:
 *
 *   机器校验   它读回了这个文件，确认它存在、能解析、页数对
 *   运行情况   产出它的那次运行有没有走完
 *   你验收     你说它是不是你要的
 *
 * Three questions, three places on the card. Collapsing any two of them is how a
 * good file came to be labelled 「未校验」.
 */

import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import {
  AlertTriangle,
  Check,
  CheckCircle2,
  Download,
  ExternalLink,
  Loader2,
  Package,
  X,
  XCircle,
} from 'lucide-react'

import { Button } from '@/components/ui/Button'
import { InlineAlert } from '@/components/ui/feedback'
import {
  deliveryApi,
  KIND_LABELS,
  formatSize,
  formatWhen,
  type Deliverable,
} from '@/lib/deliveryApi'
import { toApiError } from '@/lib/api'
import { saveBlob } from '@/lib/download'

/* ------------------------------------------------------- verification view -- */

export function VerificationPanel({ item }: { item: Deliverable }) {
  const result = item.verification
  if (!result || !result.checks?.length) {
    return (
      <p className="text-[12px] text-warn">
        还没有校验记录 —— 生成文件不等于内容正确。
      </p>
    )
  }
  const runState = result.run_state
  return (
    <div className="space-y-2" data-testid="verification-checks">
      {/* Column one: the file itself. This is what decides 已机器校验. */}
      <div className="space-y-1">
        <p className="text-[11px] uppercase tracking-wide text-faint">文件本身</p>
        {result.checks.map((check) => (
          <div key={check.name} className="flex items-start gap-2 text-[12px]">
            {check.ok ? (
              <CheckCircle2 className="mt-0.5 size-3.5 shrink-0 text-success" />
            ) : (
              <XCircle className="mt-0.5 size-3.5 shrink-0 text-danger" />
            )}
            <span className="font-mono text-faint">{check.name}</span>
            <span className="text-muted">{check.detail}</span>
          </div>
        ))}
      </div>

      {/* Column two: the run that produced it. A separate question, so a separate
          block — and it never turns the file's verdict red. */}
      {runState && (
        <div className="space-y-1 border-t border-border/60 pt-2" data-testid="run-state-note">
          <p className="text-[11px] uppercase tracking-wide text-faint">这次运行</p>
          <div className="flex items-start gap-2 text-[12px]">
            {runState.ok ? (
              <CheckCircle2 className="mt-0.5 size-3.5 shrink-0 text-success" />
            ) : (
              <AlertTriangle className="mt-0.5 size-3.5 shrink-0 text-warn" />
            )}
            <span className="text-muted">{runState.detail}</span>
          </div>
        </div>
      )}
    </div>
  )
}

/* ---------------------------------------------------------------- the card -- */

export function DeliverableCard({
  item,
  compact = false,
}: {
  item: Deliverable
  /** Conversation mode: the evidence is folded away until asked for. */
  compact?: boolean
}) {
  const queryClient = useQueryClient()
  const [rejecting, setRejecting] = useState(false)
  const [reason, setReason] = useState('')
  const [showEvidence, setShowEvidence] = useState(!compact)
  // Carries a tone as well as text: "已验收" and "下载失败：文件已不在磁盘上" must not
  // look the same, and a download that silently does nothing is the defect this
  // whole path was rewritten to remove.
  const [notice, setNotice] = useState<{ text: string; tone: 'info' | 'danger' } | null>(null)

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ['delivery'] })
    void queryClient.invalidateQueries({ queryKey: ['context'] })
  }

  const accept = useMutation({
    mutationFn: () => deliveryApi.accept(item.id),
    onSuccess: () => {
      setNotice({ text: '已验收。它会记住这个标准。', tone: 'info' })
      invalidate()
    },
    onError: (exc) => setNotice({ text: toApiError(exc).message, tone: 'danger' }),
  })
  const reject = useMutation({
    mutationFn: () => deliveryApi.reject(item.id, reason.trim()),
    onSuccess: () => {
      setNotice({ text: '已退回。这个原因会变成一条待确认的偏好。', tone: 'info' })
      setRejecting(false)
      setReason('')
      invalidate()
    },
    onError: (exc) => setNotice({ text: toApiError(exc).message, tone: 'danger' }),
  })

  // Fetch the bytes with the token, then hand them to the browser. A plain
  // `<a href>` cannot do this — a navigation sends no Authorization header, which
  // is why the button used to do nothing at all.
  const download = useMutation({
    mutationFn: () => deliveryApi.downloadDeliverable(item.id),
    onSuccess: ({ blob, filename }) => {
      setNotice(null)
      saveBlob(blob, filename)
    },
    onError: (exc) => setNotice({ text: `下载失败：${toApiError(exc).message}`, tone: 'danger' }),
  })

  // No `file://` — a WebView refuses it. The backend resolves the path from this
  // deliverable's own record, so no path crosses the wire from the page.
  const open = useMutation({
    mutationFn: () => deliveryApi.openDeliverable(item.id),
    onSuccess: () => setNotice(null),
    onError: (exc) => setNotice({ text: `打开失败：${toApiError(exc).message}`, tone: 'danger' }),
  })

  const runState = item.verification?.run_state

  return (
    <div
      className="rounded-xl border border-border bg-surface p-3.5"
      data-testid="deliverable-card"
    >
      <div className="flex flex-wrap items-center gap-2">
        <Package className="size-4 shrink-0 text-muted" />
        <span className="text-[13.5px] text-text">
          {item.title}
          {item.version > 1 && <span className="text-faint"> v{item.version}</span>}
        </span>
        <span className="text-[11.5px] text-faint">
          {KIND_LABELS[item.kind] ?? item.kind} · {formatSize(item.size_bytes)}
        </span>

        {/* The verdict, in words rather than a pill. Only the file-level checks
            decide it — see `run_state` below. */}
        {item.verified ? (
          <span className="inline-flex items-center gap-1 text-[11.5px] text-success">
            <CheckCircle2 className="size-3" /> 可以打开
          </span>
        ) : (
          <span className="inline-flex items-center gap-1 text-[11.5px] text-warn">
            <AlertTriangle className="size-3" /> 没能读回
          </span>
        )}
        {item.accepted === true && <span className="text-[11.5px] text-success">· 你已验收</span>}
        {item.accepted === false && <span className="text-[11.5px] text-danger">· 你已退回</span>}
        <span className="ml-auto text-[11.5px] text-faint">{formatWhen(item.created_at)}</span>
      </div>

      {/* The run's state, said out loud and kept apart from the file's. */}
      {runState && !runState.ok && (
        <p className="mt-1.5 text-[11.5px] text-warn" data-testid="run-state-line">
          {runState.detail}
        </p>
      )}

      {notice && (
        <div className="mt-2" data-testid="deliverable-notice">
          <InlineAlert tone={notice.tone} title={notice.tone === 'danger' ? '没能完成' : '已更新'}>
            {notice.text}
          </InlineAlert>
        </div>
      )}

      {item.accepted === false && item.feedback && (
        <p className="mt-2 rounded-lg border border-danger/25 bg-danger/5 px-3 py-1.5 text-[12px] text-danger/90">
          退回原因：「{item.feedback}」
        </p>
      )}

      {compact && !showEvidence ? (
        <button
          type="button"
          onClick={() => setShowEvidence(true)}
          className="mt-2 text-[11.5px] text-muted underline-offset-2 hover:text-text hover:underline"
          data-testid="show-evidence"
        >
          它凭什么说这个文件是好的？
        </button>
      ) : (
        <div className="mt-2.5 rounded-lg border border-border/60 bg-inset/40 p-2.5">
          <p className="mb-1.5 text-[11px] uppercase tracking-wide text-faint">验证证据</p>
          <VerificationPanel item={item} />
        </div>
      )}

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <Button
          size="xs"
          variant="secondary"
          data-testid="download-deliverable"
          disabled={download.isPending}
          onClick={() => download.mutate()}
        >
          {download.isPending ? (
            <Loader2 className="size-3 animate-spin" />
          ) : (
            <Download className="size-3" />
          )}
          {download.isPending ? '正在取回…' : '下载'}
        </Button>
        <Button
          size="xs"
          variant="ghost"
          data-testid="open-deliverable"
          disabled={open.isPending}
          onClick={() => open.mutate()}
        >
          {open.isPending ? (
            <Loader2 className="size-3 animate-spin" />
          ) : (
            <ExternalLink className="size-3" />
          )}
          打开
        </Button>

        {item.accepted === null && !rejecting && (
          <>
            <Button
              size="xs"
              variant="primary"
              className="ml-auto"
              data-testid="accept-deliverable"
              disabled={accept.isPending}
              onClick={() => accept.mutate()}
            >
              <Check className="size-3" /> 接受
            </Button>
            <Button
              size="xs"
              variant="secondary"
              data-testid="start-reject"
              onClick={() => setRejecting(true)}
            >
              <X className="size-3" /> 退回
            </Button>
          </>
        )}
      </div>

      {rejecting && (
        <div className="mt-3 rounded-lg border border-warn/30 bg-warn/5 p-3">
          <p className="mb-2 text-[12px] text-warn">
            哪里不满意？一句话就够 —— 这是它学会你标准的唯一途径。
          </p>
          <textarea
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            rows={2}
            autoFocus
            data-testid="reject-reason"
            placeholder="例如：图表太少，页数也偏多"
            className="w-full resize-none rounded-lg border border-border bg-base px-3 py-2 text-[13px] text-text outline-none focus:border-warn/60"
          />
          <div className="mt-2 flex justify-end gap-2">
            <Button size="xs" variant="ghost" onClick={() => setRejecting(false)}>
              取消
            </Button>
            <Button
              size="xs"
              variant="primary"
              data-testid="confirm-reject"
              disabled={reason.trim().length < 2 || reject.isPending}
              onClick={() => reject.mutate()}
            >
              {reject.isPending ? <Loader2 className="size-3 animate-spin" /> : null}
              确认退回
            </Button>
          </div>
        </div>
      )}
    </div>
  )
}
