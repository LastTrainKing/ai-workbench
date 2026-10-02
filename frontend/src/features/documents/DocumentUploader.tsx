import { useCallback, useRef, useState, type DragEvent } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CheckCircle2, FileUp, Loader2, Upload, X, XCircle } from 'lucide-react'
import { api, toApiError } from '@/lib/api'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/Button'
import { Field, Select } from '@/components/ui/form'
import { InlineAlert } from '@/components/ui/feedback'
import { useToastStore } from '@/store/toast'

const ACCEPTED = ['.md', '.markdown', '.txt', '.pdf']

export interface UploadJob {
  id: string
  name: string
  size: number
  state: 'pending' | 'uploading' | 'done' | 'duplicate' | 'error'
  message?: string
}

export interface DocumentUploaderProps {
  /** When provided the knowledge base selector is hidden. */
  knowledgeBaseId?: string
  projectId?: string
  onUploaded?: () => void
  compact?: boolean
}

function isAccepted(file: File): boolean {
  const lower = file.name.toLowerCase()
  return ACCEPTED.some((extension) => lower.endsWith(extension))
}

export function DocumentUploader({
  knowledgeBaseId,
  projectId,
  onUploaded,
  compact = false,
}: DocumentUploaderProps) {
  const queryClient = useQueryClient()
  const success = useToastStore((state) => state.success)
  const [dragging, setDragging] = useState(false)
  const [jobs, setJobs] = useState<UploadJob[]>([])
  const [selectedKb, setSelectedKb] = useState(knowledgeBaseId ?? '')
  const [rejected, setRejected] = useState<string[]>([])
  const inputRef = useRef<HTMLInputElement>(null)

  const basesQuery = useQuery({
    queryKey: ['knowledge-bases', 'options'],
    queryFn: ({ signal }) => api.knowledgeBases.list({ page_size: 100 }, signal),
    enabled: !knowledgeBaseId,
    staleTime: 60_000,
  })

  const effectiveKb = knowledgeBaseId ?? selectedKb

  const uploadMutation = useMutation({
    mutationFn: (file: File) =>
      api.documents.upload({
        file,
        knowledge_base_id: effectiveKb,
        project_id: projectId,
      }),
  })

  const handleFiles = useCallback(
    async (fileList: FileList | File[]) => {
      const files = Array.from(fileList)
      if (files.length === 0) return

      if (!effectiveKb) {
        setRejected(['请先选择一个知识库'])
        return
      }

      const accepted = files.filter(isAccepted)
      const refused = files.filter((file) => !isAccepted(file)).map((file) => file.name)
      setRejected(refused)

      const queued: UploadJob[] = accepted.map((file) => ({
        id: `${file.name}-${file.size}-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
        name: file.name,
        size: file.size,
        state: 'pending',
      }))
      setJobs((prev) => [...queued, ...prev].slice(0, 12))

      for (let index = 0; index < accepted.length; index += 1) {
        const file = accepted[index]
        const job = queued[index]
        setJobs((prev) => prev.map((j) => (j.id === job.id ? { ...j, state: 'uploading' } : j)))
        try {
          const result = await uploadMutation.mutateAsync(file)
          setJobs((prev) =>
            prev.map((j) =>
              j.id === job.id
                ? {
                    ...j,
                    state: result.data.duplicate ? 'duplicate' : 'done',
                    message: result.data.message,
                  }
                : j,
            ),
          )
        } catch (error) {
          const apiError = toApiError(error)
          setJobs((prev) =>
            prev.map((j) =>
              j.id === job.id
                ? { ...j, state: 'error', message: `${apiError.message}（${apiError.code}）` }
                : j,
            ),
          )
        }
      }

      await queryClient.invalidateQueries({ queryKey: ['documents'] })
      await queryClient.invalidateQueries({ queryKey: ['insights', 'dashboard'] })
      await queryClient.invalidateQueries({ queryKey: ['knowledge-bases'] })
      if (accepted.length > 0) success(`已提交 ${accepted.length} 个文件`, '后台正在解析与向量化')
      onUploaded?.()
    },
    [effectiveKb, queryClient, success, uploadMutation, onUploaded],
  )

  const onDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault()
    setDragging(false)
    void handleFiles(event.dataTransfer.files)
  }

  return (
    <div className="space-y-2.5">
      {!knowledgeBaseId ? (
        <Field label="目标知识库" required>
          <Select value={selectedKb} onChange={(event) => setSelectedKb(event.target.value)}>
            <option value="">选择知识库…</option>
            {(basesQuery.data?.items ?? []).map((base) => (
              <option key={base.id} value={base.id}>
                {base.name}
              </option>
            ))}
          </Select>
        </Field>
      ) : null}

      <div
        onDragOver={(event) => {
          event.preventDefault()
          setDragging(true)
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        className={cn(
          'flex flex-col items-center justify-center rounded-lg border border-dashed text-center transition-colors',
          compact ? 'gap-1.5 px-4 py-6' : 'gap-2.5 px-6 py-10',
          dragging ? 'border-accent bg-accent-soft' : 'border-strong bg-inset',
        )}
      >
        <span className="flex size-9 items-center justify-center rounded-lg border border-subtle bg-elevated text-accent">
          <FileUp className="size-4" />
        </span>
        <div>
          <p className="text-sm font-medium text-primary">把文件拖到这里</p>
          <p className="mt-0.5 text-2xs text-muted">支持 .md / .markdown / .txt / .pdf</p>
        </div>
        <Button
          size="sm"
          variant="secondary"
          icon={<Upload className="size-3.5" />}
          disabled={!effectiveKb}
          onClick={() => inputRef.current?.click()}
        >
          选择文件
        </Button>
        {!effectiveKb ? <p className="text-2xs text-warn">请先选择目标知识库</p> : null}
        <input
          ref={inputRef}
          type="file"
          multiple
          accept={ACCEPTED.join(',')}
          className="hidden"
          onChange={(event) => {
            if (event.target.files) void handleFiles(event.target.files)
            event.target.value = ''
          }}
        />
      </div>

      {rejected.length > 0 ? (
        <InlineAlert tone="warn" title="以下文件类型不受支持">
          {rejected.join('、')}
        </InlineAlert>
      ) : null}

      {jobs.length > 0 ? (
        <ul className="space-y-1.5">
          {jobs.map((job) => (
            <li
              key={job.id}
              className="flex items-center gap-2.5 rounded-md border border-subtle bg-surface px-2.5 py-2"
            >
              {job.state === 'uploading' || job.state === 'pending' ? (
                <Loader2 className="size-3.5 shrink-0 animate-spin text-accent" />
              ) : job.state === 'error' ? (
                <XCircle className="size-3.5 shrink-0 text-danger" />
              ) : (
                <CheckCircle2
                  className={cn('size-3.5 shrink-0', job.state === 'duplicate' ? 'text-warn' : 'text-success')}
                />
              )}
              <div className="min-w-0 flex-1">
                <p className="truncate text-2xs text-primary">{job.name}</p>
                <p className="truncate text-2xs text-faint">
                  {job.state === 'uploading'
                    ? '上传中…'
                    : job.state === 'pending'
                      ? '排队中'
                      : job.message || (job.state === 'done' ? '已接收' : '')}
                </p>
              </div>
              <button
                type="button"
                aria-label="移除记录"
                onClick={() => setJobs((prev) => prev.filter((item) => item.id !== job.id))}
                className="shrink-0 rounded p-1 text-faint hover:bg-elevated hover:text-primary"
              >
                <X className="size-3" />
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  )
}
