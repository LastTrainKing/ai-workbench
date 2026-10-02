import { Loader2 } from 'lucide-react'
import {
  Badge,
  Dot,
} from '@/components/ui/primitives'
import {
  cn,
  documentStatusLabel,
  documentStatusTone,
  priorityLabel,
  priorityTone,
  runStatusTone,
  sourceLabel,
  taskStatusLabel,
  taskStatusTone,
} from '@/lib/utils'

export function TaskStatusBadge({ status }: { status: string }) {
  return <Badge tone={taskStatusTone(status)}>{taskStatusLabel(status)}</Badge>
}

export function PriorityBadge({ priority }: { priority: string }) {
  return (
    <Badge tone={priorityTone(priority)} className="uppercase">
      {priorityLabel(priority)}
    </Badge>
  )
}

export function SourceBadge({ source }: { source: string }) {
  const tone = source === 'PLANNER' ? 'accent' : source === 'AGENT' ? 'info' : 'neutral'
  return (
    <Badge tone={tone} className="uppercase">
      {sourceLabel(source)}
    </Badge>
  )
}

export function DocumentStatusBadge({
  status,
  progress,
}: {
  status: string
  progress?: number
}) {
  const busy = status === 'PENDING' || status === 'PROCESSING'
  return (
    <Badge tone={documentStatusTone(status)}>
      {busy ? <Loader2 className="size-3 animate-spin" /> : <Dot tone={documentStatusTone(status)} />}
      {documentStatusLabel(status)}
      {busy && typeof progress === 'number' ? ` ${progress}%` : ''}
    </Badge>
  )
}

export function RunStatusBadge({ status }: { status: string }) {
  return (
    <Badge tone={runStatusTone(status)}>
      <Dot tone={runStatusTone(status)} />
      {status}
    </Badge>
  )
}

export function TagChip({ name, color }: { name: string; color?: string }) {
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 rounded border border-subtle bg-elevated px-1.5 py-px text-2xs text-muted',
      )}
    >
      {color ? <span className="size-1.5 rounded-full" style={{ backgroundColor: color }} /> : null}
      {name}
    </span>
  )
}

export function BoolBadge({ value, trueLabel, falseLabel }: { value: boolean; trueLabel: string; falseLabel: string }) {
  return <Badge tone={value ? 'success' : 'neutral'}>{value ? trueLabel : falseLabel}</Badge>
}
