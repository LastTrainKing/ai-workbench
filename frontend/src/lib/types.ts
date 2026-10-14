/**
 * TypeScript mirror of the backend contract documented in `docs/api.md`.
 *
 * Nothing in this file is invented: every field name matches the Pydantic
 * schemas under `backend/app/schemas` and the routers under `backend/app/api/v1`.
 */

/* ------------------------------------------------------------------ envelope */

export interface ApiEnvelope<T> {
  success: boolean
  data: T
  message?: string
}

export interface ApiErrorBody {
  code: string
  message: string
  details?: unknown
}

export interface ApiErrorEnvelope {
  success: false
  error: ApiErrorBody
  request_id?: string
}

export interface PageMeta {
  page: number
  page_size: number
  total: number
  total_pages: number
  has_next: boolean
  has_prev: boolean
}

export interface Paginated<T> {
  items: T[]
  meta: PageMeta
}

/* ---------------------------------------------------------------------- auth */

export interface User {
  id: string
  email: string
  username: string
  display_name: string
  avatar_color: string
  is_active: boolean
  created_at: string
}

export interface TokenResponse {
  access_token: string
  token_type: string
  expires_at: string
  user: User
}

export interface LoginPayload {
  identifier: string
  password: string
}

export interface RegisterPayload {
  email: string
  username: string
  password: string
  display_name: string
}

export interface UserUpdatePayload {
  display_name?: string
  avatar_color?: string
}

/* ------------------------------------------------------------------ projects */

export type ProjectStatus = 'ACTIVE' | 'ARCHIVED' | 'COMPLETED'

export interface ProjectStats {
  knowledge_bases: number
  documents: number
  chunks: number
  tasks_total: number
  tasks_done: number
  tasks_open: number
  notes: number
  ai_runs: number
}

export interface Project {
  id: string
  name: string
  description: string
  icon: string
  color: string
  status: string
  created_at: string
  updated_at: string
}

export interface ProjectDetail extends Project {
  stats: ProjectStats
}

export interface ProjectPayload {
  name: string
  description?: string
  icon?: string
  color?: string
}

/* ----------------------------------------------------------- knowledge bases */

export interface Tag {
  id: string
  name: string
  color: string
}

export interface KnowledgeBaseStats {
  document_count: number
  chunk_count: number
  embedded_chunk_count: number
  total_chars: number
  total_size_bytes: number
  failed_documents: number
  processing_documents: number
  last_updated: string | null
}

export interface KnowledgeBase {
  id: string
  name: string
  description: string
  icon: string
  color: string
  project_id: string | null
  embedding_model: string
  vector_size: number
  is_archived: boolean
  created_at: string
  updated_at: string
}

export interface KnowledgeBaseDetail extends KnowledgeBase {
  stats: KnowledgeBaseStats
}

export interface KnowledgeBasePayload {
  name: string
  description?: string
  icon?: string
  color?: string
  project_id?: string | null
  is_archived?: boolean
}

/* ----------------------------------------------------------------- documents */

export type DocumentStatus = 'PENDING' | 'PROCESSING' | 'COMPLETED' | 'FAILED'
export type EmbeddingStatus = 'PENDING' | 'EMBEDDED' | 'FAILED' | 'SKIPPED'

export interface DocumentSummary {
  id: string
  knowledge_base_id: string
  project_id: string | null
  title: string
  original_filename: string
  mime_type: string
  file_ext: string
  size_bytes: number
  status: string
  progress: number
  error_message: string
  char_count: number
  chunk_count: number
  embedded_chunk_count: number
  page_count: number
  language: string
  created_at: string
  updated_at: string
  processed_at: string | null
}

export interface DocumentDetail extends DocumentSummary {
  summary: string
  doc_metadata: Record<string, unknown>
  tags: Tag[]
  job_status: string | null
  job_stage: string | null
  job_error: string | null
  embedding_progress: { total: number; embedded: number; failed: number; percent: number }
}

export interface Chunk {
  id: string
  document_id: string
  chunk_index: number
  content: string
  heading: string
  char_count: number
  token_estimate: number
  embedding_status: string
  embedding_model: string
  vector_id: string
}

export interface DocumentContent {
  document_id: string
  title: string
  content: string
  char_count: number
  truncated: boolean
}

export interface EmbeddingStatusOut {
  document_id: string
  status: string
  chunks_total: number
  chunks_embedded: number
  chunks_failed: number
  embedding_model: string
}

export interface UploadAccepted {
  document: DocumentSummary
  job_id: string | null
  duplicate: boolean
  message: string
}

export type AnalysisAction =
  | 'summary'
  | 'concepts'
  | 'todos'
  | 'terms'
  | 'study_plan'
  | 'interview'
  | 'note'
  | 'cards'

export interface AnalysisTodo {
  title: string
  source?: string
}

export interface AnalysisTerm {
  term: string
  definition: string
}

export interface AnalysisInterviewQA {
  question: string
  answer: string
  difficulty?: string
  topic?: string
  follow_up?: string
}

export interface AnalysisStudyDay {
  day: number | string
  focus: string
  goal?: string
  estimated_minutes?: number
  deliverable?: string
}

export interface AnalysisCard {
  front: string
  back: string
  source?: string
}

export interface DocumentAnalysis {
  summary?: string
  key_points?: string[]
  keywords?: string[]
  difficulty?: string
  audience?: string
  concepts?: string[]
  terms?: AnalysisTerm[]
  todos?: AnalysisTodo[]
  outline?: string[]
  interview?: AnalysisInterviewQA[]
  study_plan?: AnalysisStudyDay[]
  cards?: AnalysisCard[]
  note_ids?: string[]
  run_id?: string
  provider?: string
  model?: string
  latency_ms?: number
}

/* -------------------------------------------------------------------- search */

export type SearchMode = 'keyword' | 'semantic' | 'hybrid'

export interface SearchHit {
  chunk_id: string
  document_id: string
  document_title: string
  knowledge_base_id: string
  chunk_index: number
  heading: string
  content: string
  score: number
  vector_score: number
  keyword_score: number
}

export interface SearchResponse {
  query: string
  mode: string
  cached: boolean
  took_ms: number
  reranked: boolean
  dense_hits: number
  sparse_hits: number
  results: SearchHit[]
}

/* ----------------------------------------------------------------- RAG chat */

export interface ChatMessageIn {
  role: 'user' | 'assistant'
  content: string
}

export interface Citation {
  index: number
  document_id: string | null
  document_title: string
  chunk_id: string | null
  chunk_index: number | null
  heading: string
  score: number
  snippet: string
}

export interface RetrievalMeta {
  mode: string
  dense_hits: number
  sparse_hits: number
  unique_chunks: number
  used_chunks: number
  context_tokens: number
  retrieval_ms: number
}

export interface RagAskPayload {
  question: string
  knowledge_base_ids: string[]
  document_ids?: string[]
  mode: SearchMode
  top_k?: number
  history?: ChatMessageIn[]
  project_id?: string | null
}

export interface RagAnswer {
  run_id: string
  answer: string
  grounded: boolean
  used_citation_ids: number[]
  citations: Citation[]
  queries: string[]
  retrieval: RetrievalMeta
  provider: string
  model: string
  latency_ms: number
}

/* ------------------------------------------------------------------ planner */

export interface PlanSubtask {
  title: string
  estimated_minutes?: number
}

export interface PlanTask {
  title: string
  description?: string
  estimated_minutes?: number
  priority?: string
  completion_criteria?: string
  depends_on?: string[]
  subtasks?: PlanSubtask[]
}

export interface PlanPhase {
  name: string
  description?: string
  tasks: PlanTask[]
}

export interface Plan {
  goal: string
  strategy?: string
  horizon_days?: number
  phases: PlanPhase[]
  estimated_total_minutes?: number
  estimated_total_hours?: number
  run_id: string
  provider?: string
  model?: string
  grounded_chunks?: number
}

export interface PlanCreated {
  created: number
  phases: number
  task_ids: string[]
}

export interface PlanResponse {
  plan: Plan
  created: PlanCreated | null
}

export interface StudyPlanResponse {
  run_id: string
  days?: AnalysisStudyDay[]
  [key: string]: unknown
}

/* -------------------------------------------------------------------- agent */

export interface AgentTool {
  name: string
  description: string
  category: string
  parameters: Record<string, unknown>
  is_write: boolean
}

export interface AgentToolCatalogue {
  tools: AgentTool[]
  count: number
}

export interface AgentStep {
  index: number
  thought: string
  tool: string
  arguments: Record<string, unknown>
  ok: boolean
  summary: string
  error: string | null
  latency_ms: number
}

export interface AgentCreatedEntities {
  tasks?: string[]
  notes?: string[]
}

export interface AgentRunResult {
  run_id: string
  status: string
  goal: string
  final_answer: string
  stopped_reason: string
  provider: string
  model: string
  latency_ms: number
  created: AgentCreatedEntities
  steps: AgentStep[]
}

export interface AgentRunPayload {
  goal: string
  project_id?: string | null
  knowledge_base_ids: string[]
  max_steps: number
}

/* ------------------------------------------------------------------ prompts */

export interface PromptSummary {
  id: string
  name: string
  description: string
  category: string
  is_active: boolean
  current_version: number
  version_count: number
  variables: string[]
  updated_at: string | null
  task: string
}

export interface PromptVersion {
  version: number
  content: string
  variables: string[]
  change_note: string
  is_current: boolean
  created_by: string
  created_at: string
}

export interface PromptDetail {
  name: string
  description: string
  category: string
  task: string
  json_output: boolean
  versions: PromptVersion[]
  builtin_content: string
}

/* ------------------------------------------------------------------ ai runs */

export type RunType =
  | 'RAG_ANSWER'
  | 'DOCUMENT_SUMMARY'
  | 'KNOWLEDGE_EXTRACTION'
  | 'TASK_PLANNER'
  | 'AGENT'
  | 'NOTE_GENERATION'
  | 'INTERVIEW_GENERATOR'
  | 'STUDY_PLAN'
  | 'EMBEDDING'
  | 'QUERY_REWRITE'

export type RunStatus =
  | 'RUNNING'
  | 'SUCCEEDED'
  | 'INCOMPLETE'
  | 'INTERRUPTED'
  | 'FAILED'
  | 'TIMEOUT'
  | 'CANCELLED'

export interface AIRun {
  run_id: string
  run_type: string
  status: string
  provider: string
  model: string
  prompt_name: string
  prompt_version: number
  retrieval_count: number
  tool_call_count: number
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  latency_ms: number
  retry_count: number
  cost_estimate: number
  error_code: string
  error_message: string
  created_at: string | null
  finished_at: string | null
}

export interface RunSource {
  document_id: string | null
  chunk_id: string | null
  document_title: string
  chunk_index: number | null
  score: number
  snippet: string
}

export interface RunToolCall {
  step: number
  tool: string
  arguments: Record<string, unknown> | null
  result?: unknown
  status: string
  error: string | null
  latency_ms: number
}

export interface RunAgentInfo {
  goal: string
  status: string
  steps_used: number
  max_steps: number
  scratchpad: unknown
  created: AgentCreatedEntities
}

export interface AIRunTrace {
  run: AIRun
  request: Record<string, unknown> | null
  result: Record<string, unknown> | null
  retrieval: Record<string, unknown> | null
  tools_used: string[] | null
  response_text: string | null
  sources: RunSource[]
  tool_calls: RunToolCall[]
  agent: RunAgentInfo | null
}

export interface AIMetrics {
  window_days: number
  total_runs: number
  failed_runs: number
  failure_rate: number
  avg_latency_ms: number
  total_tokens: number
  estimated_cost_usd: number
  by_type: Record<string, number>
  by_model: Record<string, number>
}

export interface DailyMetric {
  date: string
  runs: number
  tokens: number
  avg_latency_ms: number
}

export interface AIMetricsResponse {
  metrics: AIMetrics
  daily: DailyMetric[]
}

/* -------------------------------------------------------------------- tasks */

export type TaskStatus = 'TODO' | 'IN_PROGRESS' | 'BLOCKED' | 'DONE' | 'CANCELLED'
export type TaskPriority = 'LOW' | 'MEDIUM' | 'HIGH' | 'URGENT'
export type TaskSource = 'MANUAL' | 'PLANNER' | 'AGENT'

export interface Task {
  id: string
  project_id: string | null
  parent_id: string | null
  title: string
  description: string
  completion_criteria: string
  status: string
  priority: string
  source: string
  ai_suggestion: string
  due_date: string | null
  estimated_minutes: number
  actual_minutes: number
  depth: number
  is_pinned: boolean
  completed_at: string | null
  created_at: string | null
  updated_at: string | null
}

export interface TaskTreeNode extends Task {
  children: TaskTreeNode[]
}

export interface TaskTreeResponse {
  tree: TaskTreeNode[]
  count: number
}

export interface TaskLinkedDocument {
  id: string
  title: string
  status: string
}

export interface TaskDetail {
  task: Task
  children: Task[]
  dependencies: Task[]
  dependents: Task[]
  documents: TaskLinkedDocument[]
  is_blocked: boolean
}

export interface TaskPayload {
  title: string
  description?: string
  project_id?: string | null
  parent_id?: string | null
  priority?: TaskPriority
  status?: TaskStatus
  estimated_minutes?: number
  actual_minutes?: number
  due_date?: string | null
  completion_criteria?: string
  document_ids?: string[]
  depends_on_ids?: string[]
}

export type TaskUpdatePayload = Partial<Omit<TaskPayload, 'title'>> & { title?: string }

/* -------------------------------------------------------------------- notes */

export interface Note {
  id: string
  project_id: string | null
  knowledge_base_id: string | null
  source_document_id: string | null
  source_run_id: string
  title: string
  content_md: string
  is_pinned: boolean
  is_ai_generated: boolean
  created_at: string | null
  updated_at: string | null
}

export interface NotePayload {
  title: string
  content_md?: string
  project_id?: string | null
  knowledge_base_id?: string | null
  is_pinned?: boolean
}

/* ---------------------------------------------------------------- dashboard */

export interface TaskBrief {
  id: string
  title: string
  status: string
  priority: string
  due_date: string | null
  project_id: string | null
  estimated_minutes: number
  source: string
}

export interface DocumentBrief {
  id: string
  title: string
  status: string
  chunk_count: number
  knowledge_base_id: string
  created_at: string
}

export interface NoteBrief {
  id: string
  title: string
  is_ai_generated: boolean
  updated_at: string
  excerpt: string
}

export interface RunBrief {
  run_id: string
  run_type: string
  status: string
  model: string
  provider: string
  latency_ms: number
  total_tokens: number
  tool_call_count: number
  retrieval_count: number
  created_at: string
  prompt_name: string
  prompt_version: number
}

export interface ActivityBrief {
  id: string
  event_type: string
  title: string
  entity_type: string
  entity_id: string | null
  project_id: string | null
  created_at: string
}

export interface ProviderInfo {
  kind: string
  model: string
  degraded: boolean
  reason: string
}

export interface DashboardStats {
  cached: boolean
  generated_at: string
  tasks: {
    total: number
    open: number
    done: number
    overdue: number
    completed_this_week: number
    due_today: TaskBrief[]
    high_priority: TaskBrief[]
    by_status: Record<string, number>
  }
  knowledge: {
    knowledge_bases: number
    documents: number
    documents_by_status: Record<string, number>
    chunks: number
    chunk_tokens: number
    chunk_chars: number
    concepts: number
    storage_files: number
    storage_bytes: number
  }
  ai: {
    window_days: number
    total_runs: number
    failed_runs: number
    failure_rate: number
    avg_latency_ms: number
    total_tokens: number
    estimated_cost_usd: number
    by_type: Record<string, number>
    by_model: Record<string, number>
    runs_today: number
    cache: Record<string, unknown>
  }
  workspace: { projects: number; notes: number }
  recent: {
    documents: DocumentBrief[]
    notes: NoteBrief[]
    tasks: TaskBrief[]
    ai_runs: RunBrief[]
    activity: ActivityBrief[]
  }
  providers: {
    llm: ProviderInfo
    embedding: ProviderInfo
    llm_model: string
  }
  jobs: Record<string, number>
}

/* -------------------------------------------------------------------- graph */

export type GraphNodeType =
  | 'project'
  | 'knowledge_base'
  | 'document'
  | 'concept'
  | 'task'
  | 'note'
  | 'ai_run'

export type GraphEdgeType =
  | 'contains'
  | 'mentions'
  | 'subtask_of'
  | 'uses_knowledge'
  | 'produced'
  | 'retrieved'

export interface GraphNode {
  id: string
  type: GraphNodeType
  label: string
  meta: Record<string, unknown>
}

export interface GraphEdge {
  source: string
  target: string
  type: string
  weight: number
}

export interface GraphResponse {
  nodes: GraphNode[]
  edges: GraphEdge[]
  stats: {
    nodes: number
    edges: number
    by_type: Record<string, number>
    truncated: boolean
  }
}

/* ------------------------------------------------------------------- health */

export interface HealthDeps {
  status: string
  postgres: { ok: boolean; detail?: string }
  redis: { ok: boolean; detail?: string }
  qdrant: { ok: boolean; detail?: string }
  ai: {
    llm: ProviderInfo
    embedding: ProviderInfo
    tool_calling: boolean
    embedding_dimension: number
  }
  ai_degraded: boolean
  config: Record<string, unknown>
}

export interface RuntimeMetrics {
  enabled: boolean
  ai_24h: {
    window_hours?: number
    total?: number
    failed?: number
    failure_rate?: number
    avg_latency_ms?: number
    by_status?: Record<string, number>
  }
  cache: {
    hit?: number
    miss?: number
    set?: number
    error?: number
    bypass?: number
    hit_rate?: number
  }
  uptime_source?: string
}

export interface HealthBasic {
  status: string
  app: string
  version: string
  env: string
}

/* ------------------------------------------------------------ AI provider */

export type AIProviderId = 'deepseek' | 'openai_compatible' | 'ollama' | 'offline'

export interface AIModelOption {
  id: string
  label: string
  description?: string
}

export interface AIProviderOption {
  id: AIProviderId
  label: string
  /** cloud | local | offline */
  kind: string
  requires_api_key: boolean
  requires_daemon?: boolean
  default_base_url: string
  default_model: string
  models: AIModelOption[]
  description: string
  docs_url?: string
  /** Whether the operator has enabled it in the UI. */
  enabled: boolean
  /** Whether it is usable right now (a key is present / a daemon is reachable). */
  configured: boolean
}

export interface AIEffectiveConfig {
  provider: AIProviderId
  model: string
  base_url: string
  api_key_set: boolean
  /** Masked hint only — the raw key is never returned by the API. */
  api_key_hint: string
  temperature: number
  max_tokens: number
  timeout_seconds: number
  embedding_provider: string
  embedding_model: string
  embedding_base_url: string
  embedding_api_key_set: boolean
  enabled_providers: Record<string, boolean>
  /** Where each value came from: runtime override, env, or default. */
  sources: Record<string, string>
}

export interface AIConfigResponse {
  effective: AIEffectiveConfig
  overrides: Record<string, unknown>
  catalog: AIProviderOption[]
  runtime_config_path: string
  editable_fields: string[]
}

export interface AIConfigPatch {
  provider?: AIProviderId
  model?: string
  base_url?: string
  api_key?: string
  temperature?: number
  max_tokens?: number
  timeout_seconds?: number
  embedding_provider?: string
  embedding_model?: string
  embedding_base_url?: string
  embedding_api_key?: string
  enabled_providers?: Record<string, boolean>
}

export interface AIConnectionTestResult {
  ok: boolean
  provider: string
  model: string
  degraded?: boolean
  latency_ms?: number
  error_code?: string
  error_message?: string
  usage?: {
    prompt_tokens: number
    completion_tokens: number
    total_tokens: number
  }
}
