/**
 * The conversation list. This is what the sidebar is.
 *
 *     "左侧只有会话列表，不是 16 个菜单项"
 *
 * Grouped by when it was last spoken in — 今天 / 昨天 / 更早 — because the list is a
 * memory aid, and a flat list of twenty titles is not one. No dates are shown: "昨天" is
 * what a person thinks, "2026-10-07" is what a database thinks.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate, useParams } from 'react-router-dom'
import { Loader2, MessageSquare, Plus } from 'lucide-react'

import { conversationApi, type Conversation } from '@/lib/conversationApi'
import { cn } from '@/lib/utils'

function bucketOf(iso: string): string {
  const then = new Date(iso)
  const now = new Date()
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  const startOfYesterday = new Date(startOfToday.getTime() - 86_400_000)
  if (then >= startOfToday) return '今天'
  if (then >= startOfYesterday) return '昨天'
  return '更早'
}

/** Grouped, in the order the buckets should read, dropping empty ones. */
function group(items: Conversation[]): [string, Conversation[]][] {
  const order = ['今天', '昨天', '更早']
  const buckets = new Map<string, Conversation[]>()
  for (const item of items) {
    const key = bucketOf(item.updated_at)
    buckets.set(key, [...(buckets.get(key) ?? []), item])
  }
  return order.filter((key) => buckets.has(key)).map((key) => [key, buckets.get(key)!])
}

export function ConversationList({
  collapsed,
  onNavigate,
}: {
  collapsed: boolean
  onNavigate: () => void
}) {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { id: activeId } = useParams<{ id?: string }>()

  const conversations = useQuery({
    queryKey: ['conversations'],
    queryFn: () => conversationApi.list(),
    refetchInterval: 15_000,
  })

  const create = useMutation({
    mutationFn: () => conversationApi.create(),
    onSuccess: (result) => {
      void queryClient.invalidateQueries({ queryKey: ['conversations'] })
      navigate(`/c/${result.data.id}`)
      onNavigate()
    },
  })

  const items = conversations.data?.conversations ?? []

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className={cn('shrink-0 px-2 pb-1.5', collapsed && 'px-0')}>
        <button
          type="button"
          data-testid="new-conversation"
          disabled={create.isPending}
          onClick={() => create.mutate()}
          title={collapsed ? '新对话' : undefined}
          className={cn(
            'flex w-full items-center gap-2 rounded-lg border border-subtle bg-elevated px-2.5 py-2 text-[13px] text-primary transition-colors hover:border-accent/40 disabled:opacity-60',
            collapsed && 'justify-center px-0 py-2',
          )}
        >
          {create.isPending ? (
            <Loader2 className="size-3.5 animate-spin" />
          ) : (
            <Plus className="size-3.5" />
          )}
          {!collapsed ? <span>新对话</span> : null}
        </button>
      </div>

      <nav className="aiw-scroll min-h-0 flex-1 space-y-3 px-2 pb-2" data-testid="conversation-list">
        {conversations.isLoading ? (
          !collapsed ? <p className="px-2 text-[11.5px] text-faint">读取会话…</p> : null
        ) : items.length === 0 ? (
          !collapsed ? (
            <p className="px-2 text-[11.5px] leading-relaxed text-faint">
              还没有会话。点上面「新对话」开始。
            </p>
          ) : null
        ) : (
          group(items).map(([label, rows]) => (
            <div key={label}>
              {!collapsed ? (
                <p className="mb-1 px-2 text-2xs font-semibold uppercase tracking-wider text-faint">
                  {label}
                </p>
              ) : (
                <div className="mx-2 mb-1.5 h-px bg-subtle" />
              )}
              <div className="space-y-0.5">
                {rows.map((conversation) => (
                  <button
                    key={conversation.id}
                    type="button"
                    data-testid="conversation-entry"
                    onClick={() => {
                      navigate(`/c/${conversation.id}`)
                      onNavigate()
                    }}
                    title={collapsed ? conversation.title || '新对话' : undefined}
                    className={cn(
                      'flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-[13px] transition-colors',
                      collapsed && 'justify-center px-0',
                      conversation.id === activeId
                        ? 'bg-accent-soft font-medium text-accent'
                        : 'text-muted hover:bg-elevated hover:text-primary',
                    )}
                  >
                    <MessageSquare className="size-3.5 shrink-0" />
                    {!collapsed ? (
                      <span className="truncate">{conversation.title || '新对话'}</span>
                    ) : null}
                  </button>
                ))}
              </div>
            </div>
          ))
        )}
      </nav>
    </div>
  )
}
