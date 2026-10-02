import { useQuery } from '@tanstack/react-query'
import { Check, Layers } from 'lucide-react'
import { api } from '@/lib/api'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/Button'
import { Skeleton } from '@/components/ui/feedback'

export interface KnowledgeBasePickerProps {
  selected: string[]
  onChange: (ids: string[]) => void
  /** Project scope for the knowledge base list. */
  projectId?: string
  emptyHint?: string
}

/**
 * Compact multi-select for knowledge bases. Used by the planner, agent and chat
 * surfaces so the selection affordance is identical everywhere.
 */
export function KnowledgeBasePicker({
  selected,
  onChange,
  projectId,
  emptyHint = '还没有知识库，先去「知识库」页面创建并上传文档。',
}: KnowledgeBasePickerProps) {
  const { data, isLoading } = useQuery({
    queryKey: ['knowledge-bases', 'options', projectId ?? 'all'],
    queryFn: ({ signal }) =>
      api.knowledgeBases.list({ page_size: 100, project_id: projectId }, signal),
    staleTime: 60_000,
  })

  const bases = data?.items ?? []

  if (isLoading) {
    return (
      <div className="flex flex-wrap gap-1.5">
        {Array.from({ length: 3 }).map((_, index) => (
          <Skeleton key={index} className="h-6 w-24" />
        ))}
      </div>
    )
  }

  if (bases.length === 0) {
    return <p className="text-2xs text-faint">{emptyHint}</p>
  }

  const toggle = (id: string) => {
    onChange(selected.includes(id) ? selected.filter((item) => item !== id) : [...selected, id])
  }

  return (
    <div className="space-y-1.5">
      <div className="flex flex-wrap gap-1.5">
        {bases.map((base) => {
          const active = selected.includes(base.id)
          return (
            <button
              key={base.id}
              type="button"
              aria-pressed={active}
              onClick={() => toggle(base.id)}
              className={cn(
                'inline-flex max-w-[220px] items-center gap-1.5 rounded border px-2 py-1 text-2xs font-medium transition-colors',
                active
                  ? 'border-accent/50 bg-accent-soft text-accent'
                  : 'border-subtle bg-elevated text-muted hover:border-strong hover:text-primary',
              )}
            >
              <span
                className="size-1.5 shrink-0 rounded-full"
                style={{ backgroundColor: base.color }}
              />
              <span className="truncate">{base.name}</span>
              {active ? <Check className="size-3 shrink-0" /> : null}
            </button>
          )
        })}
      </div>
      <div className="flex items-center gap-2">
        <Button
          size="xs"
          variant="ghost"
          icon={<Layers className="size-3" />}
          onClick={() => onChange(bases.map((base) => base.id))}
        >
          全选
        </Button>
        <Button size="xs" variant="ghost" onClick={() => onChange([])}>
          清空
        </Button>
        <span className="text-2xs text-faint">已选 {selected.length} / {bases.length}</span>
      </div>
    </div>
  )
}
