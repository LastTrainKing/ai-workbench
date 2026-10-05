/**
 * The home screen's launch grid.
 *
 * Six large cards, navigable with the arrow keys and clickable with the mouse.
 * The first card is the point of the whole product — "tell the Agent what you
 * want" — so it is wider and louder than the rest, and it is the one the keyboard
 * lands on first.
 *
 * Keyboard behaviour comes from `useFocusGrid`; this component only decides what
 * the cards are and what they look like. The focus ring is deliberately strong: a
 * subtle ring is invisible on a dark background at a glance, and the whole point
 * of keyboard navigation is that you can always see where you are.
 */

import { useCallback, useMemo, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Activity,
  ArrowRight,
  Bot,
  FolderKanban,
  Library,
  ListChecks,
  Sparkles,
  UserCircle,
} from 'lucide-react'

import { cn } from '@/lib/utils'
import { useColumnCount, useFocusGrid } from '@/features/shell/useFocusGrid'

interface LaunchCard {
  to: string
  label: string
  description: string
  icon: typeof Bot
  /** Spans two columns and gets a louder treatment. */
  primary?: boolean
  /** Short line shown on the primary card. */
  hint?: string
}

const CARDS: LaunchCard[] = [
  {
    to: '/agent',
    label: '告诉 Agent 你想做什么',
    description: '它会制定计划、调用工具、验证结果，失败时自己修复。',
    icon: Bot,
    primary: true,
    hint: '例如：帮我修复这个项目的测试',
  },
  {
    to: '/me',
    label: '我的 AI',
    description: '它对你的了解，全部在这里',
    icon: UserCircle,
  },
  {
    to: '/projects',
    label: '项目',
    description: '正在做的事',
    icon: FolderKanban,
  },
  {
    to: '/knowledge',
    label: '资料',
    description: '导入的文档与检索',
    icon: Library,
  },
  {
    to: '/tasks',
    label: '任务',
    description: '待办与进度',
    icon: ListChecks,
  },
  {
    to: '/ai-runs',
    label: '执行记录',
    description: '它做过什么，一步步可查',
    icon: Activity,
  },
]

export function LaunchGrid() {
  const navigate = useNavigate()
  const gridRef = useRef<HTMLDivElement>(null)
  const columns = useColumnCount(gridRef, 3)

  const commit = useCallback((index: number) => {
    const card = CARDS[index]
    if (card) navigate(card.to)
  }, [navigate])

  const grid = useFocusGrid({
    count: CARDS.length,
    columns,
    onCommit: commit,
  })

  // The primary card spans two columns, so the visual grid is not a plain
  // rectangle. Movement still uses the column count, which is the behaviour a
  // person expects from a d-pad regardless of the layout underneath.
  const cardClass = useMemo(
    () => (card: LaunchCard) =>
      cn(
        'group relative flex flex-col justify-between rounded-2xl border p-5 text-left transition-all duration-150',
        'border-border bg-surface hover:border-border-strong',
        card.primary ? 'sm:col-span-2 min-h-[132px]' : 'min-h-[116px]',
        // The focus ring. Two rings — an outline and a glow — because on a dark
        // background either one alone is easy to miss.
        'data-[focused=true]:border-accent data-[focused=true]:ring-2 data-[focused=true]:ring-accent/60',
        'data-[focused=true]:shadow-[0_0_28px_-6px_rgb(var(--c-accent)/0.55)]',
      ),
    [],
  )

  return (
    <div
      ref={gridRef}
      {...grid.containerProps}
      aria-label="快速入口"
      className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3"
    >
      {CARDS.map((card, index) => {
        const Icon = card.icon
        const focused = grid.isFocused(index)
        return (
          <button
            key={card.to}
            type="button"
            {...grid.itemProps(index)}
            onClick={() => navigate(card.to)}
            className={cardClass(card)}
          >
            <div className="flex items-start gap-3">
              <span
                className={cn(
                  'grid size-9 shrink-0 place-items-center rounded-xl border transition-colors',
                  focused
                    ? 'border-accent/60 bg-accent/15 text-accent'
                    : 'border-border bg-inset text-muted',
                )}
              >
                <Icon className="size-4" />
              </span>
              <div className="min-w-0">
                <p
                  className={cn(
                    'text-[15px] font-medium leading-snug',
                    card.primary ? 'text-text' : 'text-text',
                  )}
                >
                  {card.label}
                </p>
                <p className="mt-1 text-[12.5px] leading-relaxed text-muted">{card.description}</p>
                {card.hint && (
                  <p className="mt-2 inline-flex items-center gap-1.5 rounded-full border border-border px-2.5 py-0.5 text-[11.5px] text-faint">
                    <Sparkles className="size-3" />
                    {card.hint}
                  </p>
                )}
              </div>
            </div>
            <ArrowRight
              className={cn(
                'mt-3 size-4 self-end transition-all',
                focused ? 'translate-x-0 text-accent opacity-100' : '-translate-x-1 text-faint opacity-0',
              )}
            />
          </button>
        )
      })}
    </div>
  )
}
