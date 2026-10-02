# API contract — frontend reference

Base URL: `http://127.0.0.1:8000/api/v1`
Auth: `Authorization: Bearer <access_token>`
Interactive docs: `http://127.0.0.1:8000/docs` · OpenAPI JSON: `http://127.0.0.1:8000/openapi.json`

## Response envelope

Every endpoint (success or failure) returns:

```jsonc
// success
{ "success": true, "data": <payload>, "message": "optional human text" }

// failure
{ "success": false, "error": { "code": "not_found", "message": "...", "details": {...} },
  "request_id": "a1b2c3d4e5f6" }
```

Error codes: `validation_error`, `not_found`, `conflict`, `authentication_error`,
`permission_denied`, `unsupported_media_type`, `payload_too_large`, `rate_limited`,
`infrastructure_error`, `vector_store_error`, `ai_error`, `ai_provider_error`,
`ai_timeout`, `ai_rate_limited`, `ai_auth_error`, `ai_response_format_error`,
`document_parse_error`, `tool_execution_error`, `database_error`, `internal_error`.

Every response also carries `X-Request-ID` and `X-Process-Time-Ms` headers.

Pagination shape (`data`):

```jsonc
{ "items": [...], "meta": { "page": 1, "page_size": 20, "total": 137,
  "total_pages": 7, "has_next": true, "has_prev": false } }
```

---

## Auth

| Method | Path | Body | Notes |
|---|---|---|---|
| POST | `/auth/register` | `{email, username, password, display_name}` | password ≥8 chars, must mix letters+digits |
| POST | `/auth/login` | `{identifier, password}` | `identifier` = username **or** email |
| GET | `/auth/me` | — | current user |
| POST | `/auth/logout` | — | client just drops the token |
| GET | `/users/me` | — | same shape as `/auth/me` |
| PATCH | `/users/me` | `{display_name?, avatar_color?}` | `avatar_color` = `#rrggbb` |

`User`: `{id, email, username, display_name, avatar_color, is_active, created_at}`
Login/register return `{access_token, token_type, expires_at, user}`.

Demo account seeded on first boot: **demo / demo1234**

---

## Projects

| Method | Path | Notes |
|---|---|---|
| GET | `/projects?page&page_size&status&q` | paginated |
| POST | `/projects` | `{name, description, icon, color}` |
| GET | `/projects/{id}` | returns project **+ `stats`** |
| PATCH | `/projects/{id}` | partial |
| DELETE | `/projects/{id}` | cascades |

`stats`: `{knowledge_bases, documents, chunks, tasks_total, tasks_done, tasks_open, notes, ai_runs}`

---

## Knowledge bases

| Method | Path | Notes |
|---|---|---|
| GET | `/knowledge-bases?page&page_size&project_id&q&include_archived` | |
| POST | `/knowledge-bases` | `{name, description, icon, color, project_id}` |
| GET | `/knowledge-bases/tags` | all tags of the user |
| GET | `/knowledge-bases/{id}` | returns KB **+ `stats`** |
| PATCH | `/knowledge-bases/{id}` | partial incl. `is_archived` |
| DELETE | `/knowledge-bases/{id}` | deletes chunks + Qdrant points |

`stats`: `{document_count, chunk_count, embedded_chunk_count, total_chars, total_size_bytes, failed_documents, processing_documents, last_updated}`

---

## Documents

| Method | Path | Notes |
|---|---|---|
| POST | `/documents/upload` | **multipart**: `file`, `knowledge_base_id`, `title?`, `project_id?` → 202 |
| GET | `/documents?page&page_size&knowledge_base_id&project_id&status&q` | |
| GET | `/documents/{id}` | + `job_status`, `job_stage`, `job_error`, `embedding_progress`, `tags` |
| GET | `/documents/{id}/content?limit` | extracted plain text |
| GET | `/documents/{id}/chunks?page&page_size` | |
| GET | `/documents/{id}/embedding-status` | |
| PATCH | `/documents/{id}` | `{title?, project_id?, tag_names?}` |
| POST | `/documents/{id}/reprocess` | re-queue ingestion |
| POST | `/documents/{id}/analyze` | `{actions: string[], save_note, project_id?}` |
| DELETE | `/documents/{id}` | |

Upload response: `{document, job_id, duplicate, message}` — `duplicate: true` means the
same content hash already exists in that knowledge base and nothing was re-processed.

`status` ∈ `PENDING | PROCESSING | COMPLETED | FAILED`. Poll `GET /documents/{id}`
(1–2 s) while status is `PENDING`/`PROCESSING`; `progress` is 0–100.

`actions` ∈ `summary | concepts | todos | terms | study_plan | interview | note | cards`.

Analyze response keys: `summary`, `key_points[]`, `keywords[]`, `difficulty`, `audience`,
`concepts[]`, `terms[{term,definition}]`, `todos[{title,source}]`, `outline[]`,
`interview[{question,answer,difficulty,topic,follow_up}]`, `study_plan[{day,focus,goal,estimated_minutes,deliverable}]`,
`cards[{front,back,source}]`, `note_ids[]`, `run_id`, `provider`, `model`, `latency_ms`.

---

## Search

`GET /search?q&mode=keyword|semantic|hybrid&knowledge_base_id&project_id&limit`

```jsonc
{ "query": "...", "mode": "hybrid", "cached": false, "took_ms": 42, "reranked": true,
  "dense_hits": 40, "sparse_hits": 22,
  "results": [ { "chunk_id", "document_id", "document_title", "knowledge_base_id",
                 "chunk_index", "heading", "content",
                 "score", "vector_score", "keyword_score" } ] }
```

`vector_score` and `keyword_score` are the two legs of the hybrid fusion — show them
so the user can see *why* a chunk matched.

---

## RAG chat

`POST /chat/rag`

```jsonc
// request
{ "question": "...", "knowledge_base_ids": ["uuid"], "document_ids": [],
  "mode": "hybrid", "top_k": 8, "history": [{"role":"user","content":"..."}],
  "project_id": null }

// response data
{ "run_id": "run_...", "answer": "…[1][2]…", "grounded": true,
  "used_citation_ids": [1,2],
  "citations": [ { "index": 1, "document_id", "document_title", "chunk_id",
                   "chunk_index", "heading", "score", "snippet" } ],
  "queries": ["original", "rewritten variant"],
  "retrieval": { "mode", "dense_hits", "sparse_hits", "unique_chunks",
                 "used_chunks", "context_tokens", "retrieval_ms" },
  "provider": "offline", "model": "heuristic-extractive-v1", "latency_ms": 120 }
```

Render `[n]` markers in the answer as clickable citation chips that scroll to the
matching entry in `citations`.

---

## Planner

| Method | Path | Body |
|---|---|---|
| POST | `/planner/plan` | `{goal, knowledge_base_ids, project_id?, horizon_days, create_tasks}` |
| POST | `/planner/plan/apply` | `{run_id, project_id?}` |
| POST | `/planner/study-plan` | `{goal, knowledge_base_ids, project_id?, horizon_days}` |

`/planner/plan` → `{plan: {goal, strategy, horizon_days, phases[], estimated_total_minutes, estimated_total_hours, run_id, provider, model, grounded_chunks}, created: null | {created, phases, task_ids}}`

`phases[]`: `{name, description, tasks: [{title, description, estimated_minutes, priority, completion_criteria, depends_on[], subtasks[{title,estimated_minutes}]}]}`

---

## Agent & tools

| Method | Path | Notes |
|---|---|---|
| GET | `/agents/tools` | `{tools: [{name, description, category, parameters, is_write}], count}` |
| POST | `/agents/run` | `{goal, project_id?, knowledge_base_ids, max_steps}` |
| GET | `/agents/runs/{run_id}` | trajectory |

`/agents/run` → `{run_id, status, goal, final_answer, stopped_reason, provider, model, latency_ms, created: {tasks:[uuid], notes:[uuid]}, steps: [{index, thought, tool, arguments, ok, summary, error, latency_ms}]}`

`stopped_reason` ∈ `final_answer | max_steps | loop_detected | empty_response`.

---

## Prompts

| Method | Path | Notes |
|---|---|---|
| GET | `/prompts` | `[{id, name, description, category, is_active, current_version, version_count, variables[], updated_at, task}]` |
| POST | `/prompts` | `{name, description, category, content}` |
| GET | `/prompts/{name}` | `{name, description, category, task, json_output, versions[{version, content, variables, change_note, is_current, created_by, created_at}], builtin_content}` |
| POST | `/prompts/{name}/versions` | `{content, change_note, activate}` |
| POST | `/prompts/{name}/versions/{version}/activate` | |
| POST | `/prompts/{name}/reset` | back to the built-in template |

---

## AI runs (execution trace)

| Method | Path | Notes |
|---|---|---|
| GET | `/ai-runs?page&page_size&run_type&status&project_id&q` | history |
| GET | `/ai-runs/metrics?days` | `{metrics, daily}` |
| GET | `/ai-runs/{run_id}` | full trace |

Run object: `{run_id, run_type, status, provider, model, prompt_name, prompt_version,
retrieval_count, tool_call_count, prompt_tokens, completion_tokens, total_tokens,
latency_ms, retry_count, cost_estimate, error_code, error_message, created_at, finished_at}`

`run_type` ∈ `RAG_ANSWER | DOCUMENT_SUMMARY | KNOWLEDGE_EXTRACTION | TASK_PLANNER |
AGENT | NOTE_GENERATION | INTERVIEW_GENERATOR | STUDY_PLAN | EMBEDDING | QUERY_REWRITE`
`status` ∈ `RUNNING | SUCCEEDED | FAILED | TIMEOUT | CANCELLED`

Trace detail adds: `request`, `result`, `retrieval`, `tools_used`, `response_text`,
`sources[{document_id, chunk_id, document_title, chunk_index, score, snippet}]`,
`tool_calls[{step, tool, arguments, result, status, error, latency_ms}]`,
`agent{goal, status, steps_used, max_steps, scratchpad, created}`.

`/ai-runs/metrics` → `{metrics: {window_days, total_runs, failed_runs, failure_rate,
avg_latency_ms, total_tokens, estimated_cost_usd, by_type{}, by_model{}},
daily: [{date, runs, tokens, avg_latency_ms}]}`

---

## Tasks

| Method | Path | Notes |
|---|---|---|
| GET | `/tasks?page&page_size&project_id&status&priority&parent_id&roots_only&q` | |
| GET | `/tasks/tree?project_id` | `{tree: [node], count}`; node has `children[]` |
| POST | `/tasks` | `{title, description, project_id?, parent_id?, priority, status, estimated_minutes, actual_minutes, due_date, completion_criteria, document_ids[], depends_on_ids[]}` |
| GET | `/tasks/{id}` | `{task, children[], dependencies[], dependents[], documents[], is_blocked}` |
| PATCH | `/tasks/{id}` | partial |
| POST | `/tasks/{id}/complete` | `{actual_minutes?}` |
| DELETE | `/tasks/{id}` | |
| POST | `/tasks/{id}/dependencies` | `{depends_on_task_id}` — 409 on cycles |
| DELETE | `/tasks/{id}/dependencies/{dependency_id}` | |
| POST | `/tasks/{id}/knowledge` | `{document_id, relevance}` |
| DELETE | `/tasks/{id}/knowledge/{document_id}` | |

`status` ∈ `TODO | IN_PROGRESS | BLOCKED | DONE | CANCELLED`
`priority` ∈ `LOW | MEDIUM | HIGH | URGENT`
`source` ∈ `MANUAL | PLANNER | AGENT`
`due_date` accepts ISO (`2026-11-01`) or relative (`+7d`, `2w`, `3个月`).

Business rules surfaced as errors:
* completing a task with unfinished dependencies → **409**
* a dependency that would create a cycle → **409**
* illegal status transition → **409**

---

## Notes

`GET /notes?page&page_size&project_id&knowledge_base_id&q` ·
`POST /notes` `{title, content_md, project_id?, knowledge_base_id?, is_pinned}` ·
`GET|PATCH|DELETE /notes/{id}` — `PATCH` accepts `{title?, content_md?, is_pinned?, project_id?, knowledge_base_id?}`

---

## Insights

`GET /stats/dashboard` — every number is computed from the database:

```jsonc
{ "cached": false, "generated_at": "…",
  "tasks": { "total", "open", "done", "overdue", "completed_this_week",
             "due_today": [taskBrief], "high_priority": [taskBrief], "by_status": {} },
  "knowledge": { "knowledge_bases", "documents", "documents_by_status",
                 "chunks", "chunk_tokens", "chunk_chars", "concepts",
                 "storage_files", "storage_bytes" },
  "ai": { "window_days", "total_runs", "failed_runs", "failure_rate",
          "avg_latency_ms", "total_tokens", "estimated_cost_usd",
          "by_type": {}, "by_model": {}, "runs_today": 0, "cache": {} },
  "workspace": { "projects", "notes" },
  "recent": { "documents": [], "notes": [], "tasks": [], "ai_runs": [], "activity": [] },
  "providers": { "llm": {kind, model, degraded, reason}, "embedding": {...}, "llm_model": "" },
  "jobs": { "QUEUED": 0, "RUNNING": 0, "SUCCEEDED": 0, "FAILED": 0 } }
```

`taskBrief`: `{id, title, status, priority, due_date, project_id, estimated_minutes, source}`
`documentBrief`: `{id, title, status, chunk_count, knowledge_base_id, created_at}`
`noteBrief`: `{id, title, is_ai_generated, updated_at, excerpt}`
`runBrief`: `{run_id, run_type, status, model, provider, latency_ms, total_tokens, tool_call_count, retrieval_count, created_at, prompt_name, prompt_version}`
`activityBrief`: `{id, event_type, title, entity_type, entity_id, project_id, created_at}`

`GET /graph?project_id&limit&include_concepts&include_runs` →
`{nodes: [{id, type, label, meta}], edges: [{source, target, type, weight}], stats: {nodes, edges, by_type, truncated}}`

Node `type` ∈ `project | knowledge_base | document | concept | task | note | ai_run`.
Edge `type` ∈ `contains | mentions | subtask_of | uses_knowledge | produced | retrieved`.

---

## Health

* `GET /health` → `{status, app, version, env}`
* `GET /health/deps` → `{status, postgres{ok,detail}, redis{ok}, qdrant{ok,detail}, ai{llm{},embedding{},tool_calling,embedding_dimension}, ai_degraded, config{...}}`
* `GET /metrics` → `{enabled, ai_24h{}, cache{}}`

`ai.llm.degraded: true` means no cloud key is configured and the deterministic
offline engine is answering. The UI must surface this honestly (a warning badge),
never hide it.
