/**
 * The conversation list. This is what the sidebar is.
 *
 *     "左侧只有会话列表，不是 16 个菜单项"
 *     "会话列表：现在堆满了，要能删。"
 *
 * Grouped by when it was last spoken in — 今天 / 昨天 / 更早 — because the list is a
 * memory aid, and a flat list of twenty titles is not one. No dates are shown: "昨天" is
 * what a person thinks, "2026-10-07" is what a database thinks.
 *
 * ## Deleting, three ways
 *
 * The user asked for all three and they are the same operation with a longer list, so
 * they share one code path and one confirmation:
 *
 *   * the row's own 删除 button, appearing on hover,
 *   * 勾选多条 → 删除, after 选择 turns the checkboxes on,
 *   * 全选 → 删除, which is the same thing with everything selected.
 *
 * **The confirmation states the numbers** — 「删掉 3 条对话、12 条消息、5 个产出物」 —
 * and they come from the list rows rather than a second request, so the user is
 * confirming something they can actually see. The reply afterwards reports what was
 * really removed, from the server's own count.
 */

import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate, useParams } from 'react-router-dom'
import { CheckSquare, Loader2, MessageSquare, Plus, Square, Trash2, X } from 'lucide-react'

import { confirm } from '@/components/ui/overlay'
import { conversationApi, type Conversation } from '@/lib/conversationApi'
import { toApiError } from '@/lib/api'
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

  const [selecting, setSelecting] = useState(false)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [notice, setNotice] = useState<string | null>(null)
  const [failure, setFailure] = useState<string | null>(null)

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

  const remove = useMutation({
    mutationFn: (input: { ids: string[]; all?: boolean }) =>
      conversationApi.remove(input.ids, input.all ?? false),
    onSuccess: (result, input) => {
      const ids = input.ids
      setFailure(null)
      setSelecting(false)
      setSelected(new Set())
      void queryClient.invalidateQueries({ queryKey: ['conversations'] })
      void queryClient.invalidateQueries({ queryKey: ['delivery'] })
      // If the conversation on screen is one of the deleted ones, there is nothing left
      // to show. Going to `/` is better than a 404 or a blank pane.
      if (activeId && ids.includes(activeId)) {
        navigate('/')
        onNavigate()
      }
      const { conversations: rows, deliverables, files_removed: files } = result.data
      const parts = [`${rows} 条对话`]
      if (deliverables > 0) parts.push(`${deliverables} 个产出物`)
      setNotice(`删掉了 ${parts.join('、')}${files > 0 ? `（文件也删了）` : ''}`)
    },
    onError: (exc) => {
      // The failure stays on screen. A delete that failed and said nothing would leave
      // the user believing the rows are gone.
      setNotice(null)
      setFailure(toApiError(exc).message)
    },
  })

  const items = conversations.data?.conversations ?? []
  const allSelected = items.length > 0 && selected.size === items.length

  const askAndDelete = async (ids: string[]) => {
    if (ids.length === 0) return
    const rows = items.filter((item) => ids.includes(item.id))
    const messages = rows.reduce((total, row) => total + row.message_count, 0)
    const deliverables = rows.reduce((total, row) => total + (row.deliverable_count ?? 0), 0)

    const scope = [`${rows.length} 条对话`, `${messages} 条消息`]
    if (deliverables > 0) scope.push(`${deliverables} 个产出物文件`)

    const agreed = await confirm({
      title: rows.length === 1 ? '删掉这条对话？' : `删掉这 ${rows.length} 条对话？`,
      description: `${scope.join('、')}会一起删掉，删了就找不回来了。`,
      // Naming them is what makes it a confirmation rather than a reflex. Above three
      // the list stops being readable and the count is the honest thing to show.
      detail:
        rows.length <= 3 ? rows.map((row) => row.title || '新对话').join('、') : undefined,
      confirmLabel: '删掉',
      cancelLabel: '算了',
      tone: 'danger',
    })
    if (agreed) {
      // **「全选」 means the whole set, not the loaded page.** The list endpoint returns 50
      // by default, so selecting everything the client had ever seen left the rest behind —
      // a user with 60 conversations clicked 全选 → 一键清空 and kept 10, with nothing on
      // screen to explain it. When everything loaded is selected, the server is asked to
      // delete everything it has for this user.
      const everything = ids.length === items.length && items.length > 0
      remove.mutate({ ids, all: everything })
    }
  }

  const toggle = (id: string) => {
    setSelected((current) => {
      const next = new Set(current)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

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

      {/* The toolbar only exists when there is something to select. An always-present
          「选择」 button on an empty list is a control that cannot do anything. */}
      {!collapsed && items.length > 0 && (
        <div className="shrink-0 px-2 pb-1" data-testid="conversation-toolbar">
          {selecting ? (
            <div className="flex items-center gap-1">
              <button
                type="button"
                data-testid="select-all"
                onClick={() => setSelected(allSelected ? new Set() : new Set(items.map((i) => i.id)))}
                className="flex items-center gap-1 rounded-md px-1.5 py-1 text-[11.5px] text-muted transition-colors hover:text-primary"
              >
                {allSelected ? <CheckSquare className="size-3.5" /> : <Square className="size-3.5" />}
                {allSelected ? '取消全选' : '全选'}
              </button>
              <button
                type="button"
                data-testid="delete-selected"
                disabled={selected.size === 0 || remove.isPending}
                onClick={() => void askAndDelete([...selected])}
                className="ml-auto flex items-center gap-1 rounded-md px-1.5 py-1 text-[11.5px] text-danger transition-colors hover:bg-danger-soft/40 disabled:opacity-40"
              >
                {remove.isPending ? <Loader2 className="size-3.5 animate-spin" /> : <Trash2 className="size-3.5" />}
                删除 {selected.size || ''}
              </button>
              <button
                type="button"
                data-testid="exit-select-mode"
                onClick={() => {
                  setSelecting(false)
                  setSelected(new Set())
                }}
                className="rounded-md p-1 text-faint transition-colors hover:text-primary"
                title="退出选择"
              >
                <X className="size-3.5" />
              </button>
            </div>
          ) : (
            <button
              type="button"
              data-testid="enter-select-mode"
              onClick={() => setSelecting(true)}
              className="rounded-md px-1.5 py-1 text-[11.5px] text-faint transition-colors hover:text-primary"
            >
              选择
            </button>
          )}
        </div>
      )}

      {(notice || failure) && !collapsed && (
        <p
          data-testid="conversation-notice"
          className={cn('shrink-0 px-3 pb-1 text-[11.5px]', failure ? 'text-danger' : 'text-muted')}
        >
          {failure ?? notice}
        </p>
      )}

      <nav className="aiw-scroll min-h-0 flex-1 space-y-3 px-2 pb-2" data-testid="conversation-list">
        {conversations.isLoading ? (
          !collapsed ? <p className="px-2 text-[11.5px] text-faint">读取会话…</p> : null
        ) : items.length === 0 ? (
          !collapsed ? (
            // The empty state, not a blank pane: an empty list and a failed list look the
            // same otherwise, and only one of them is fine.
            <div className="px-2 py-6 text-center" data-testid="conversation-empty">
              <MessageSquare className="mx-auto mb-2 size-5 text-faint" />
              <p className="text-[11.5px] leading-relaxed text-faint">
                还没有会话。
                <br />
                点上面「新对话」开始。
              </p>
            </div>
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
                {rows.map((conversation) => {
                  const active = conversation.id === activeId
                  return (
                    // A `div`, not a `button`: the row now contains a delete button, and a
                    // button inside a button is invalid HTML — the inner one stops being
                    // clickable, which is exactly the "悬停出现但点不到" the user warned about.
                    <div key={conversation.id} className="group/row flex items-center gap-0.5">
                      {selecting && !collapsed && (
                        <input
                          type="checkbox"
                          data-testid="conversation-check"
                          aria-label={`选择 ${conversation.title || '新对话'}`}
                          checked={selected.has(conversation.id)}
                          onChange={() => toggle(conversation.id)}
                          className="size-3.5 shrink-0 accent-current"
                        />
                      )}
                      <button
                        type="button"
                        data-testid="conversation-entry"
                        onClick={() => {
                          if (selecting) {
                            toggle(conversation.id)
                            return
                          }
                          navigate(`/c/${conversation.id}`)
                          onNavigate()
                        }}
                        title={collapsed ? conversation.title || '新对话' : undefined}
                        className={cn(
                          'flex min-w-0 flex-1 items-center gap-2 rounded-md px-2 py-1.5 text-left text-[13px] transition-colors',
                          collapsed && 'justify-center px-0',
                          active
                            ? 'bg-accent-soft font-medium text-accent'
                            : 'text-muted hover:bg-elevated hover:text-primary',
                        )}
                      >
                        <MessageSquare className="size-3.5 shrink-0" />
                        {!collapsed ? (
                          <span className="truncate">{conversation.title || '新对话'}</span>
                        ) : null}
                      </button>
                      {/* Hover to reveal, but always clickable: `opacity-0` keeps the
                          element in the layout and hit-testable, so the pointer reaches
                          it and so does a test. `focus-visible` gives it to the keyboard
                          too, which a `hidden`/`display:none` version would not. */}
                      {!collapsed && (
                        <button
                          type="button"
                          data-testid="conversation-delete"
                          aria-label={`删除 ${conversation.title || '新对话'}`}
                          title="删除这条对话"
                          onClick={() => void askAndDelete([conversation.id])}
                          className="shrink-0 rounded-md p-1 text-faint opacity-0 transition-opacity hover:bg-danger-soft/40 hover:text-danger focus-visible:opacity-100 group-hover/row:opacity-100"
                        >
                          <Trash2 className="size-3.5" />
                        </button>
                      )}
                    </div>
                  )
                })}
              </div>
            </div>
          ))
        )}
      </nav>
    </div>
  )
}
