/**
 * Typed API client.
 *
 * Responsibilities:
 *  - attach the bearer token to every request,
 *  - unwrap the `{ success, data }` envelope the backend always returns,
 *  - convert the `{ success: false, error, request_id }` envelope into a typed
 *    `ApiError` that carries `code` / `message` / `details` / `requestId` / `status`,
 *  - clear the session and bounce to `/login` on a 401.
 */
import type {
  AgentRunPayload,
  AgentRunResult,
  AgentToolCatalogue,
  AIConfigPatch,
  AIConfigResponse,
  AIConnectionTestResult,
  AIProviderOption,
  AIModelOption,
  AIRun,
  AIMetricsResponse,
  AIRunTrace,
  AnalysisAction,
  Chunk,
  Citation,
  DashboardStats,
  DocumentAnalysis,
  DocumentContent,
  DocumentDetail,
  DocumentSummary,
  EmbeddingStatusOut,
  GraphResponse,
  HealthBasic,
  HealthDeps,
  KnowledgeBase,
  KnowledgeBaseDetail,
  KnowledgeBasePayload,
  LoginPayload,
  Note,
  NotePayload,
  Paginated,
  PlanCreated,
  PlanResponse,
  Project,
  ProjectDetail,
  ProjectPayload,
  PromptDetail,
  PromptSummary,
  RagAnswer,
  RagAskPayload,
  RegisterPayload,
  RuntimeMetrics,
  SearchMode,
  SearchResponse,
  StudyPlanResponse,
  Tag,
  Task,
  TaskDetail,
  TaskPayload,
  TaskTreeResponse,
  TaskUpdatePayload,
  TokenResponse,
  UploadAccepted,
  User,
  UserUpdatePayload,
} from './types'

export const API_BASE_URL: string = import.meta.env.VITE_API_BASE_URL || '/api/v1'

/* ------------------------------------------------------------------- errors */

export class ApiError extends Error {
  readonly code: string
  readonly details?: unknown
  readonly requestId?: string
  readonly status: number

  constructor(args: {
    code: string
    message: string
    status: number
    details?: unknown
    requestId?: string
  }) {
    super(args.message)
    this.name = 'ApiError'
    this.code = args.code
    this.details = args.details
    this.requestId = args.requestId
    this.status = args.status
  }

  /** True when the caller may reasonably retry (transient backend trouble). */
  get isRetryable(): boolean {
    return (
      this.status >= 500 ||
      this.code === 'rate_limited' ||
      this.code === 'ai_rate_limited' ||
      this.code === 'infrastructure_error' ||
      this.code === 'ai_timeout'
    )
  }

  get isAuthError(): boolean {
    return this.status === 401 || this.code === 'authentication_error'
  }

  /** Human-facing text including the request id when the backend supplied one. */
  get displayMessage(): string {
    return this.requestId ? `${this.message}（请求 ${this.requestId}）` : this.message
  }
}

export function isApiError(error: unknown): error is ApiError {
  return error instanceof ApiError
}

/** Normalise anything thrown by the client into an ApiError. */
export function toApiError(error: unknown): ApiError {
  if (isApiError(error)) return error
  if (error instanceof DOMException && error.name === 'AbortError') {
    return new ApiError({ code: 'aborted', message: '请求已取消', status: 0 })
  }
  if (error instanceof Error) {
    return new ApiError({ code: 'network_error', message: error.message || '网络请求失败', status: 0 })
  }
  return new ApiError({ code: 'unknown_error', message: '未知错误', status: 0 })
}

/* -------------------------------------------------------------- session glue */

let accessToken: string | null = null
let unauthorizedHandler: (() => void) | null = null

/** Called by the auth store whenever the token changes. */
export function setAccessToken(token: string | null): void {
  accessToken = token
}

export function getAccessToken(): string | null {
  return accessToken
}

/** Called once from the app bootstrap so a 401 can reset the session. */
export function setUnauthorizedHandler(handler: (() => void) | null): void {
  unauthorizedHandler = handler
}

/* ------------------------------------------------------------------ request */

export type QueryValue = string | number | boolean | null | undefined

export interface ApiResult<T> {
  data: T
  message?: string
  requestId?: string
}

export interface RequestOptions {
  method?: 'GET' | 'POST' | 'PATCH' | 'PUT' | 'DELETE'
  query?: Record<string, QueryValue>
  body?: unknown
  formData?: FormData
  signal?: AbortSignal
  /** Internal: prevents a 401 on the login call from triggering a redirect loop. */
  skipAuthRedirect?: boolean
}

function buildUrl(path: string, query?: Record<string, QueryValue>): string {
  const base = API_BASE_URL.replace(/\/$/, '')
  const url = `${base}${path.startsWith('/') ? path : `/${path}`}`
  if (!query) return url
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === null || value === '') continue
    params.append(key, String(value))
  }
  const qs = params.toString()
  return qs ? `${url}?${qs}` : url
}

async function parseBody(response: Response): Promise<unknown> {
  const text = await response.text()
  if (!text) return null
  try {
    return JSON.parse(text)
  } catch {
    return { __raw: text }
  }
}

/**
 * Perform a request and return the unwrapped payload together with the optional
 * envelope `message` (used for toasts) and the `X-Request-ID` header.
 */
export async function requestResult<T>(path: string, options: RequestOptions = {}): Promise<ApiResult<T>> {
  const { method = 'GET', query, body, formData, signal, skipAuthRedirect } = options

  const headers: Record<string, string> = { Accept: 'application/json' }
  if (accessToken) headers.Authorization = `Bearer ${accessToken}`

  let payload: BodyInit | undefined
  if (formData) {
    // Let the browser set the multipart boundary.
    payload = formData
  } else if (body !== undefined) {
    headers['Content-Type'] = 'application/json'
    payload = JSON.stringify(body)
  }

  let response: Response
  try {
    response = await fetch(buildUrl(path, query), { method, headers, body: payload, signal })
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw toApiError(error)
    throw new ApiError({
      code: 'network_error',
      message: '无法连接到后端服务，请确认服务是否已启动（http://127.0.0.1:8000）',
      status: 0,
    })
  }

  const requestId = response.headers.get('X-Request-ID') ?? undefined
  const parsed = await parseBody(response)

  if (response.status === 401 && !skipAuthRedirect) {
    accessToken = null
    unauthorizedHandler?.()
  }

  const envelope = parsed as { success?: boolean; data?: T; message?: string; error?: unknown } | null

  if (response.ok && envelope && envelope.success !== false) {
    const data = (envelope && 'data' in envelope ? envelope.data : parsed) as T
    return { data, message: envelope?.message, requestId }
  }

  const errorBody = (envelope?.error ?? null) as
    | { code?: string; message?: string; details?: unknown }
    | null

  const fallbackCode =
    response.status === 401
      ? 'authentication_error'
      : response.status === 403
        ? 'permission_denied'
        : response.status === 404
          ? 'not_found'
          : response.status === 409
            ? 'conflict'
            : response.status === 422
              ? 'validation_error'
              : response.status >= 500
                ? 'internal_error'
                : 'http_error'

  throw new ApiError({
    code: errorBody?.code || fallbackCode,
    message: errorBody?.message || `请求失败（HTTP ${response.status}）`,
    details: errorBody?.details,
    requestId,
    status: response.status,
  })
}

/** Convenience wrapper returning only the unwrapped payload. */
export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  return (await requestResult<T>(path, options)).data
}

/* ---------------------------------------------------------------- endpoints */

export const api = {
  /* ---------------------------------------------------------------- auth */
  auth: {
    login: (payload: LoginPayload) =>
      requestResult<TokenResponse>('/auth/login', {
        method: 'POST',
        body: payload,
        skipAuthRedirect: true,
      }),
    register: (payload: RegisterPayload) =>
      requestResult<TokenResponse>('/auth/register', {
        method: 'POST',
        body: payload,
        skipAuthRedirect: true,
      }),
    me: (signal?: AbortSignal) => request<User>('/auth/me', { signal }),
    logout: () => request<{ logged_out: boolean; username: string }>('/auth/logout', { method: 'POST' }),
    profile: (signal?: AbortSignal) => request<User>('/users/me', { signal }),
    updateProfile: (payload: UserUpdatePayload) =>
      requestResult<User>('/users/me', { method: 'PATCH', body: payload }),
  },

  /* ------------------------------------------------------------ projects */
  projects: {
    list: (
      params: { page?: number; page_size?: number; status?: string; q?: string } = {},
      signal?: AbortSignal,
    ) => request<Paginated<Project>>('/projects', { query: params, signal }),
    create: (payload: ProjectPayload) =>
      requestResult<Project>('/projects', { method: 'POST', body: payload }),
    get: (id: string, signal?: AbortSignal) => request<ProjectDetail>(`/projects/${id}`, { signal }),
    update: (id: string, payload: Partial<ProjectPayload> & { status?: string }) =>
      requestResult<Project>(`/projects/${id}`, { method: 'PATCH', body: payload }),
    remove: (id: string) => requestResult<{ deleted: boolean }>(`/projects/${id}`, { method: 'DELETE' }),
  },

  /* ----------------------------------------------------- knowledge bases */
  knowledgeBases: {
    list: (
      params: {
        page?: number
        page_size?: number
        project_id?: string
        q?: string
        include_archived?: boolean
      } = {},
      signal?: AbortSignal,
    ) => request<Paginated<KnowledgeBase>>('/knowledge-bases', { query: params, signal }),
    create: (payload: KnowledgeBasePayload) =>
      requestResult<KnowledgeBase>('/knowledge-bases', { method: 'POST', body: payload }),
    get: (id: string, signal?: AbortSignal) =>
      request<KnowledgeBaseDetail>(`/knowledge-bases/${id}`, { signal }),
    update: (id: string, payload: Partial<KnowledgeBasePayload>) =>
      requestResult<KnowledgeBase>(`/knowledge-bases/${id}`, { method: 'PATCH', body: payload }),
    remove: (id: string) =>
      requestResult<{ deleted: boolean }>(`/knowledge-bases/${id}`, { method: 'DELETE' }),
    tags: (signal?: AbortSignal) => request<Tag[]>('/knowledge-bases/tags', { signal }),
  },

  /* ------------------------------------------------------------ documents */
  documents: {
    list: (
      params: {
        page?: number
        page_size?: number
        knowledge_base_id?: string
        project_id?: string
        status?: string
        q?: string
      } = {},
      signal?: AbortSignal,
    ) => request<Paginated<DocumentSummary>>('/documents', { query: params, signal }),
    upload: (args: {
      file: File
      knowledge_base_id: string
      title?: string
      project_id?: string
    }) => {
      const fd = new FormData()
      fd.append('file', args.file)
      fd.append('knowledge_base_id', args.knowledge_base_id)
      if (args.title) fd.append('title', args.title)
      if (args.project_id) fd.append('project_id', args.project_id)
      return requestResult<UploadAccepted>('/documents/upload', { method: 'POST', formData: fd })
    },
    get: (id: string, signal?: AbortSignal) =>
      request<DocumentDetail>(`/documents/${id}`, { signal }),
    content: (id: string, limit = 40000, signal?: AbortSignal) =>
      request<DocumentContent>(`/documents/${id}/content`, { query: { limit }, signal }),
    chunks: (id: string, params: { page?: number; page_size?: number } = {}, signal?: AbortSignal) =>
      request<Paginated<Chunk>>(`/documents/${id}/chunks`, { query: params, signal }),
    embeddingStatus: (id: string, signal?: AbortSignal) =>
      request<EmbeddingStatusOut>(`/documents/${id}/embedding-status`, { signal }),
    update: (id: string, payload: { title?: string; project_id?: string | null; tag_names?: string[] }) =>
      requestResult<DocumentSummary>(`/documents/${id}`, { method: 'PATCH', body: payload }),
    reprocess: (id: string) =>
      requestResult<{ job_id: string }>(`/documents/${id}/reprocess`, { method: 'POST' }),
    analyze: (
      id: string,
      payload: { actions: AnalysisAction[]; save_note?: boolean; project_id?: string | null },
    ) =>
      requestResult<DocumentAnalysis>(`/documents/${id}/analyze`, { method: 'POST', body: payload }),
    remove: (id: string) =>
      requestResult<{ deleted: boolean }>(`/documents/${id}`, { method: 'DELETE' }),
  },

  /* --------------------------------------------------------------- search */
  search: {
    run: (
      params: {
        q: string
        mode?: SearchMode
        knowledge_base_id?: string
        project_id?: string
        limit?: number
      },
      signal?: AbortSignal,
    ) => request<SearchResponse>('/search', { query: params, signal }),
  },

  /* ------------------------------------------------------------- rag chat */
  chat: {
    ask: (payload: RagAskPayload, signal?: AbortSignal) =>
      requestResult<RagAnswer>('/chat/rag', { method: 'POST', body: payload, signal }),
  },

  /* -------------------------------------------------------------- planner */
  planner: {
    plan: (payload: {
      goal: string
      knowledge_base_ids: string[]
      project_id?: string | null
      horizon_days: number
      create_tasks: boolean
    }) => requestResult<PlanResponse>('/planner/plan', { method: 'POST', body: payload }),
    apply: (payload: { run_id: string; project_id?: string | null }) =>
      requestResult<PlanCreated>('/planner/plan/apply', { method: 'POST', body: payload }),
    studyPlan: (payload: {
      goal: string
      knowledge_base_ids: string[]
      project_id?: string | null
      horizon_days: number
    }) => requestResult<StudyPlanResponse>('/planner/study-plan', { method: 'POST', body: payload }),
  },

  /* ---------------------------------------------------------------- agent */
  agents: {
    tools: (signal?: AbortSignal) => request<AgentToolCatalogue>('/agents/tools', { signal }),
    run: (payload: AgentRunPayload, signal?: AbortSignal) =>
      requestResult<AgentRunResult>('/agents/run', { method: 'POST', body: payload, signal }),
    trace: (runId: string, signal?: AbortSignal) =>
      request<AgentRunResult>(`/agents/runs/${runId}`, { signal }),
  },

  /* -------------------------------------------------------------- prompts */
  prompts: {
    list: (signal?: AbortSignal) => request<PromptSummary[]>('/prompts', { signal }),
    create: (payload: { name: string; description?: string; category?: string; content: string }) =>
      requestResult<PromptDetail>('/prompts', { method: 'POST', body: payload }),
    get: (name: string, signal?: AbortSignal) => request<PromptDetail>(`/prompts/${name}`, { signal }),
    createVersion: (name: string, payload: { content: string; change_note?: string; activate?: boolean }) =>
      requestResult<{ version: number }>(`/prompts/${name}/versions`, { method: 'POST', body: payload }),
    activateVersion: (name: string, version: number) =>
      requestResult<unknown>(`/prompts/${name}/versions/${version}/activate`, { method: 'POST' }),
    reset: (name: string) => requestResult<unknown>(`/prompts/${name}/reset`, { method: 'POST' }),
  },

  /* -------------------------------------------------------------- ai runs */
  aiRuns: {
    list: (
      params: {
        page?: number
        page_size?: number
        run_type?: string
        status?: string
        project_id?: string
        q?: string
      } = {},
      signal?: AbortSignal,
    ) => request<Paginated<AIRun>>('/ai-runs', { query: params, signal }),
    metrics: (days = 30, signal?: AbortSignal) =>
      request<AIMetricsResponse>('/ai-runs/metrics', { query: { days }, signal }),
    get: (runId: string, signal?: AbortSignal) =>
      request<AIRunTrace>(`/ai-runs/${runId}`, { signal }),
  },

  /* ---------------------------------------------------------------- tasks */
  tasks: {
    list: (
      params: {
        page?: number
        page_size?: number
        project_id?: string
        status?: string
        priority?: string
        parent_id?: string
        roots_only?: boolean
        q?: string
      } = {},
      signal?: AbortSignal,
    ) => request<Paginated<Task>>('/tasks', { query: params, signal }),
    tree: (projectId?: string, signal?: AbortSignal) =>
      request<TaskTreeResponse>('/tasks/tree', { query: { project_id: projectId }, signal }),
    create: (payload: TaskPayload) => requestResult<Task>('/tasks', { method: 'POST', body: payload }),
    get: (id: string, signal?: AbortSignal) => request<TaskDetail>(`/tasks/${id}`, { signal }),
    update: (id: string, payload: TaskUpdatePayload) =>
      requestResult<Task>(`/tasks/${id}`, { method: 'PATCH', body: payload }),
    complete: (id: string, actualMinutes?: number) =>
      requestResult<Task>(`/tasks/${id}/complete`, {
        method: 'POST',
        body: { actual_minutes: actualMinutes ?? null },
      }),
    remove: (id: string) => requestResult<{ deleted: boolean }>(`/tasks/${id}`, { method: 'DELETE' }),
    addDependency: (id: string, dependsOnTaskId: string) =>
      requestResult<{ added: boolean }>(`/tasks/${id}/dependencies`, {
        method: 'POST',
        body: { depends_on_task_id: dependsOnTaskId },
      }),
    removeDependency: (id: string, dependencyId: string) =>
      requestResult<{ removed: boolean }>(`/tasks/${id}/dependencies/${dependencyId}`, {
        method: 'DELETE',
      }),
    linkKnowledge: (id: string, documentId: string, relevance = 1) =>
      requestResult<{ linked: boolean }>(`/tasks/${id}/knowledge`, {
        method: 'POST',
        body: { document_id: documentId, relevance },
      }),
    unlinkKnowledge: (id: string, documentId: string) =>
      requestResult<{ removed: boolean }>(`/tasks/${id}/knowledge/${documentId}`, { method: 'DELETE' }),
  },

  /* ---------------------------------------------------------------- notes */
  notes: {
    list: (
      params: {
        page?: number
        page_size?: number
        project_id?: string
        knowledge_base_id?: string
        q?: string
      } = {},
      signal?: AbortSignal,
    ) => request<Paginated<Note>>('/notes', { query: params, signal }),
    create: (payload: NotePayload) => requestResult<Note>('/notes', { method: 'POST', body: payload }),
    get: (id: string, signal?: AbortSignal) => request<Note>(`/notes/${id}`, { signal }),
    update: (id: string, payload: Partial<NotePayload>) =>
      requestResult<Note>(`/notes/${id}`, { method: 'PATCH', body: payload }),
    remove: (id: string) => requestResult<{ deleted: boolean }>(`/notes/${id}`, { method: 'DELETE' }),
  },

  /* ------------------------------------------------------------- insights */
  insights: {
    dashboard: (signal?: AbortSignal) => request<DashboardStats>('/stats/dashboard', { signal }),
    graph: (
      params: { project_id?: string; limit?: number; include_concepts?: boolean; include_runs?: boolean } = {},
      signal?: AbortSignal,
    ) => request<GraphResponse>('/graph', { query: params, signal }),
  },

  /* ----------------------------------------------------------- ai config */
  aiConfig: {
    get: (signal?: AbortSignal) => request<AIConfigResponse>('/ai-config', { signal }),
    update: (payload: AIConfigPatch) =>
      request<AIConfigResponse>('/ai-config', { method: 'PATCH', body: payload }),
    reset: () => request<AIConfigResponse>('/ai-config', { method: 'DELETE' }),
    providers: (signal?: AbortSignal) =>
      request<{ providers: AIProviderOption[]; count: number }>('/ai-config/providers', { signal }),
    models: (provider: string, live = false, signal?: AbortSignal) =>
      request<{ provider: string; models: AIModelOption[]; live: boolean }>('/ai-config/models', {
        query: { provider, live },
        signal,
      }),
    toggle: (provider: string, enabled: boolean) =>
      request<{ enabled_providers: Record<string, boolean> }>('/ai-config/providers/toggle', {
        method: 'POST',
        body: { provider, enabled },
      }),
    test: (provider?: string) =>
      request<AIConnectionTestResult>('/ai-config/test', {
        method: 'POST',
        body: { provider: provider ?? null },
      }),
    health: (signal?: AbortSignal) =>
      request<Record<string, unknown>>('/ai-config/health', { signal }),
  },

  /* --------------------------------------------------------------- health */
  health: {
    basic: (signal?: AbortSignal) => request<HealthBasic>('/health', { signal }),
    deps: (signal?: AbortSignal) => request<HealthDeps>('/health/deps', { signal }),
    metrics: (signal?: AbortSignal) => request<RuntimeMetrics>('/metrics', { signal }),
  },
}

export type Api = typeof api

/** Convenience re-export so callers can type citation arrays without a second import. */
export type { Citation }
