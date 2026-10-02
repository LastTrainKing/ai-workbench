# AI subsystem

The AI subsystem lives under `backend/app/ai/`. It is a set of libraries the
service layer calls; it never calls the API layer. This document covers the
provider abstraction, the factory's degradation logic, the offline engine in
detail, prompt versioning, structured output handling, token/latency/cost
accounting, the retry/timeout/backoff policy, the error taxonomy and execution
tracing.

## 1. Provider abstraction

Two abstract interfaces in `app/ai/providers/base.py` are the only thing business
code depends on:

```python
class LLMProvider(ABC):
    name: str
    def __init__(self, *, model: str, timeout: float, max_retries: int) -> None: ...
    @property
    def supports_tool_calling(self) -> bool: return False
    @abstractmethod
    async def chat(self, messages, *, temperature=0.2, max_tokens=2048,
                   tools=None, json_mode=False) -> ChatResult: ...
    async def aclose(self) -> None: ...

class EmbeddingProvider(ABC):
    name: str
    @property
    @abstractmethod
    def dimension(self) -> int: ...
    @abstractmethod
    async def embed(self, texts) -> EmbeddingResult: ...
    async def aclose(self) -> None: ...
```

Provider-agnostic value objects are in `app/ai/types.py`: `ChatMessage`,
`ToolCallRequest`, `Usage`, `ChatResult`, `EmbeddingResult`, `RetrievedChunk`,
`ToolSpec`, `ToolResult`. Nothing outside `app/ai/providers/` knows which vendor
is in use.

### Concrete providers

| Class | Module | Notes |
|---|---|---|
| `OpenAICompatibleProvider` | `openai_compatible.py` | Any OpenAI-compatible `/chat/completions` endpoint (DeepSeek, Qwen, OpenAI, SiliconFlow, …) |
| `OpenAICompatibleEmbeddingProvider` | `openai_compatible.py` | `/embeddings`; dimension can be discovered from the first response |
| `OllamaProvider` | `ollama.py` | Fully local; exposes `list_models()` |
| `OllamaEmbeddingProvider` | `ollama.py` | Local embeddings |
| `HeuristicLLMProvider` | `heuristic.py` | Deterministic offline engine (fallback) |
| `HeuristicEmbeddingProvider` | `heuristic.py` | Deterministic hashed n-gram embedder (fallback) |

## 2. The factory and degradation logic

`app/ai/providers/factory.py` is the single place where configuration becomes an
instance. It caches providers by a key derived from kind + model + base URL.

### `build_llm_provider()`

```
AI_PROVIDER == "openai_compatible" and AI_API_KEY non-empty
        → OpenAICompatibleProvider(kind="openai_compatible")

AI_PROVIDER == "ollama"
        → OllamaProvider(base_url or http://127.0.0.1:11434)

otherwise (openai_compatible with empty key, or AI_PROVIDER == "offline")
        → HeuristicLLMProvider
          ProviderInfo(kind="offline", model="heuristic-extractive-v1",
                       degraded=True, reason=...)
```

The recorded `reason` is one of:

* `"AI_API_KEY is empty; using the offline heuristic engine."`
* `"Ollama was selected but the provider is unavailable; using offline engine."`
* `"AI_PROVIDER=offline explicitly selected."`

### `build_embedding_provider()`

The same shape, with `EMBEDDING_PROVIDER`. Embedding base URL and API key fall
back to the chat values via `settings.resolved_embedding_base_url()` /
`resolved_embedding_api_key()`. The offline fallback is
`HeuristicEmbeddingProvider` (`model="heuristic-hash-ngram-v1"`,
`degraded=True`).

### Where degradation is surfaced

* `GET /api/v1/health/deps` → `ai.llm.degraded`, `ai.llm.reason`,
  `ai.embedding.degraded`, and a top-level `ai_degraded` boolean.
* `GET /api/v1/stats/dashboard` → `providers.llm` / `providers.embedding`
  (each `{kind, model, degraded, reason}`).
* Every `ai_runs` row records the actual `provider` and `model` that answered.

`check_provider_health()` also reports `tool_calling` (from
`llm.supports_tool_calling`) and `embedding_dimension`.

The product never pretends the offline engine is a hosted model. When no cloud
key is configured, `provider` is `"offline"` and `model` is
`"heuristic-extractive-v1"` everywhere it is displayed.

## 3. The offline engine in detail

`app/ai/providers/heuristic.py` is a **real implementation**, not a stub. It is
what keeps every workflow functional with zero external dependencies and zero API
keys.

### 3.1 Tokenisation

`tokenize(text)` is language-agnostic:

* Latin words and numbers via `[A-Za-z][A-Za-z0-9_+#.-]*|\d+`, lower-cased.
* CJK runs (`[\u4e00-\u9fff]+`) are expanded to character bigrams **and** the
  full run is kept for exact-phrase matching.
* A stop-word set (English + Chinese) is removed; single-character non-CJK tokens
  are dropped.

### 3.2 Embeddings

`HeuristicEmbeddingProvider` (`DEFAULT_DIMENSION = 384`):

1. Tokenise the text.
2. Count token frequencies.
3. For each token, hash it with BLAKE2b (8-byte digest) into a bucket
   `value % dim`, with a sign bit taken from the top bit of the hash.
4. Accumulate `sign * (1 + ln(tf))` — sublinear TF, which damps repeated
   boilerplate.
5. L2-normalise the vector.

Properties (asserted in `backend/tests/unit/test_heuristic_provider.py`):

* deterministic (identical input → identical vector);
* L2-normalised (norm ≈ 1.0);
* fixed 384 dimensions;
* semantically related text scores higher cosine similarity than unrelated text;
* empty input yields an empty vector list.

This is a hashed bag-of-ngrams vectoriser: good enough for lexical-semantic
recall, dependency-free, and it never downloads a model.

### 3.3 Task routing

`HeuristicLLMProvider.chat()` inspects the system prompt for a
`<!-- task: <name> -->` marker (present in every built-in template) and routes to
a deterministic algorithm. Without a marker it infers from the user text
(`plan`/`计划` → planner, `summar`/`总结` → summary, else RAG answer), and if
tools are supplied it uses the agent policy.

| Task marker | Method | Output |
|---|---|---|
| `rag_answer` | `_answer` | Extractive sentence-scored answer |
| `document_summary` | `_summary` | Extractive summary + keywords |
| `knowledge_extraction` | `_extract` | JSON: summary, concepts, key_points, terms, todos, outline |
| `task_planner` | `_plan` | JSON: phased task tree |
| `study_plan` | `_study_plan` | JSON: day-by-day plan |
| `interview_generator` | `_interview` | JSON: questions with answers |
| `note_generation` | `_note` | Structured Markdown note |
| `query_rewrite` | `_rewrite` | JSON: 2–3 query variants |
| `agent_system` | `_agent_step` | One deterministic tool call per step |

### 3.4 Extractive QA (`_answer`)

Sentences are split, filtered to length > 8, and scored by:

```
score = 2.0 * query_term_overlap
      + 0.4 * lexical_density          (unique/total tokens)
      + 0.2 * position_prior           (1 / (1 + 0.08 * index))
```

The top five sentences are returned as a bulleted answer prefixed with
`根据检索到的资料，可以归纳出以下要点（离线启发式抽取）` and suffixed with a
statement that the content comes only from retrieved context. With no context it
returns an honest "no material found" message that tells the user to upload
documents or configure `AI_API_KEY`.

### 3.5 Structured extraction

* **Summary** (`_summary`): top-frequency terms, sentence salience, a position
  prior and a length penalty; returns numbered points plus a keyword list.
* **Knowledge extraction** (`_extract`): concepts from frequent multi-character
  terms, terms with their defining sentence, TODOs detected by a
  `TODO|FIXME|待办|下一步|需要|要完成` regex (falling back to headings), and an
  outline from Markdown headings.
* **Planning** (`_plan`): headings (or short sentences, or goal-derived topics)
  become 3–5 phases, each with three tasks carrying descriptions, integer
  estimates, priorities and verifiable completion criteria.
* **Interview** (`_interview`): a question per salient topic, with the defining
  sentence as the answer and a follow-up.
* **Study plan** (`_study_plan`): flattens the plan into per-day entries.

### 3.6 Agent policy

`_agent_step` mirrors what a competent assistant would do and always terminates:
it inspects the transcript for tools already used and emits the next tool in the
sequence `knowledge_search → task_planner → task_create_batch → note_create`,
selecting only the tools relevant to the goal keywords. When all relevant tools
are used it returns a prose final answer with no tool calls.

## 4. Prompt versioning

Prompts are **data, not code**. They live in the database (`prompts`,
`prompt_versions`) and are managed by `app/ai/prompts/registry.py`.

* `builtin.py` defines nine seed templates, each with `name`, `description`,
  `category`, `task`, `variables`, `json_output` and a `content` string.
  `PROMPT_INVENTORY` is the seed list; `TEMPLATES_BY_NAME` is the lookup map.
* `render_template()` substitutes `{name}` placeholders with a restricted regex
  (`\{([a-z][a-z0-9_]*)\}`) so literal JSON braces inside a template are safe.
  Unknown placeholders are left intact.
* `ensure_seeded()` inserts any built-in template that has no row yet (idempotent;
  called at startup and in tests).
* `get_prompt(session, name)` returns the currently active version, or the
  built-in template if no row exists. Business code asks **by name** and receives
  a `ResolvedPrompt(name, version, content, variables, task, json_output,
  category, source)`.
* `create_version()` / `activate_version()` / `reset_to_builtin()` manage the
  history. Creating a version identical to the current one raises `ConflictError`.
  Content is capped at 40,000 characters.
* Caching: `prompt:<name>:<namespace_version>` with TTL
  `CACHE_TTL_PROMPT_SECONDS` (600 s). Every write bumps the namespace version so
  edits are visible immediately.

Because the resolved prompt's `name` and `version` are persisted on the `ai_runs`
row, any AI answer is reproducible: given a run you can see exactly which prompt
version produced it.

## 5. Structured output handling

`load_json_object(text)` in `app/ai/rag/pipeline.py` is a tolerant JSON extractor:

1. Strip Markdown code fences.
2. Try `json.loads`.
3. On failure, find the first `{...}` block with a DOTALL regex and parse it.
4. If that fails too, raise `AIResponseFormatError` with a 400-character preview.

Callers that expect JSON (analysis, planning, rewrite) all go through this
helper, so a model that wraps JSON in prose or fences still works.

For tool calling, `parse_tool_arguments(raw)` in `agent/executor.py` applies the
same best-effort extraction to a tool-call argument string.

## 6. Token, latency and cost accounting

`estimate_tokens(text)` in `app/ai/types.py` is a dependency-free heuristic
(`tiktoken` is deliberately avoided because it downloads BPE files at runtime):

```
tokens = max(1, cjk_chars + round(other_chars / 4))
```

`Usage` carries `prompt_tokens`, `completion_tokens`, `total_tokens` and supports
`merged()` so multi-call runs (agent steps, multi-action analysis) accumulate.

`estimate_cost(model, usage)` in `app/ai/tracing.py` applies a small public price
table (USD per 1M tokens) for known model families and returns `0.0` for unknown
models rather than guessing. Models in the table include `gpt-4o-mini`,
`gpt-4o`, `gpt-4.1-mini`, `deepseek-chat`, `deepseek-reasoner`, `qwen-plus`,
`qwen-turbo`, `text-embedding-3-small`, `text-embedding-3-large`.

Latency is measured per provider call (inside each provider) and per run
(`RunRecorder.finish` computes wall-clock latency from `time.perf_counter()`).
Both are written to `ai_runs.latency_ms`; aggregate averages are exposed by
`GET /ai-runs/metrics` and the dashboard.

## 7. Retry, timeout and backoff

`app/core/retry.py` is a hand-rolled bounded retry helper (no `tenacity`), so the
policy is explicit, testable and identical across HTTP/AI/worker call sites.

`RetryPolicy` defaults: `max_attempts=3`, `base_delay=1.0`, `max_delay=20.0`,
`multiplier=2.0`, `jitter=0.25`, `max_elapsed=180.0`.

Rules enforced by `retry_async()`:

* only errors explicitly marked `retryable` are retried (`should_retry`);
* `asyncio.CancelledError` is never retried;
* the attempt count is hard-capped;
* total wall-clock time is bounded by `max_elapsed`;
* every retry is logged (`retry_scheduled`) with attempt number, delay and reason;
* giving up is logged (`retry_giving_up`) before re-raising.

`delay_for(attempt)` = `min(base_delay * multiplier**(attempt-1), max_delay)` with
symmetric jitter `raw * (1-jitter) + raw*jitter*2*random()`.
`backend/tests/unit/test_retry.py` asserts the exponential schedule
(1, 2, 4, 8, capped at 10), fail-fast on non-retryable errors, the elapsed-time
bound, the `on_retry` callback and cancellation behaviour.

Timeouts: `AI_TIMEOUT_SECONDS` (90 s) for chat, `EMBEDDING_TIMEOUT_SECONDS`
(60 s) for embeddings. The worker adds a job-level
`asyncio.timeout(WORKER_JOB_TIMEOUT_SECONDS)` (900 s).

## 8. Error taxonomy

From `app/core/errors.py`. `AIError.retryable` drives the retry policy.

| Exception | Code | HTTP | Retryable |
|---|---|---|---|
| `AIError` | `ai_error` | 502 | no |
| `AIProviderError` | `ai_provider_error` | 502 | yes |
| `AITimeoutError` | `ai_timeout` | 502 | yes |
| `AIRateLimitError` | `ai_rate_limited` | 429 | yes |
| `AIAuthError` | `ai_auth_error` | 502 | no |
| `AIConfigurationError` | `ai_configuration_error` | 502 | no |
| `AIResponseFormatError` | `ai_response_format_error` | 502 | yes |
| `DocumentParseError` | `document_parse_error` | 422 | — |
| `ToolExecutionError` | `tool_execution_error` | 502 | — |
| `VectorStoreError` | `vector_store_error` | 503 | — |

`AIRunService.health()` and `GET /ai-runs/metrics` classify `FAILED` + `TIMEOUT`
as failed runs and report a failure rate.

## 9. Execution tracing

`app/ai/tracing.py` defines `RunRecorder`, an explicit
`start → mutate → finish` handle (not a decorator) so a run survives partial
failure: if an agent times out at step 4, the first three tool calls are still
recorded.

`RunRecorder.start()` inserts an `ai_runs` row (`RUNNING`) and sets the `run_id`
contextvar. Mutators: `record_usage`, `record_retry`, `set_retrieval`,
`set_result_payload`, `add_sources` (writes `ai_run_sources`),
`record_tool_call` (writes `tool_calls` and appends to `tools_used`),
`attach_agent_run` (creates the 1:1 `agent_runs` row).

`finish()` sets status, response text (capped 60,000 chars), latency, retry count,
error code/message, token totals, `cost_estimate` and `finished_at`, then flushes
and clears the contextvar. It is idempotent (`_finished` guard).

Large payloads are trimmed by `_sanitize()` (max depth 6, max 200 list items, max
8000 chars per string) so a run row can never blow up the database.

### Trace shape

`GET /api/v1/ai-runs/{run_id}` returns `run`, `tool_calls`, `sources` and
`agent_run`. The `run` object carries `run_id`, `run_type`, `status`, `provider`,
`model`, `prompt_name`, `prompt_version`, `retrieval_count`, `tool_call_count`,
`prompt_tokens`, `completion_tokens`, `total_tokens`, `latency_ms`, `retry_count`,
`cost_estimate`, `error_code`, `error_message`, `created_at`, `finished_at`, plus
`request`, `result`, `retrieval`, `tools_used` and `response_text`.

`run_type` ∈ `RAG_ANSWER | DOCUMENT_SUMMARY | KNOWLEDGE_EXTRACTION |
TASK_PLANNER | AGENT | NOTE_GENERATION | INTERVIEW_GENERATOR | STUDY_PLAN |
EMBEDDING | QUERY_REWRITE`.
`status` ∈ `RUNNING | SUCCEEDED | FAILED | TIMEOUT | CANCELLED`.

## 10. Related documents

* [rag.md](rag.md) — the retrieval pipeline that feeds generation
* [agent.md](agent.md) — the tool loop
* [../docs/thesis/07-AI模块设计.md](thesis/07-AI模块设计.md) — the Chinese design chapter
