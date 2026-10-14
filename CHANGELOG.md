# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [3.0.0] — 2026-10-14

**私人 AI 员工工作台。** 2.5.0 让状态说得准；这一版让**结果出现在工作结束的地方**，
并把"能不能公开发布"这件事本身做成了可核验的。

发布形态是**正式版**：§17 的验收清单十三行全部达标，§16 的七条"禁止发布"闸门
一条都没有触发。安装包已重建并与源码 commit 对齐，并且在本机完整走完了
**卸载 2.5.0 → 安装 3.0.0 → 启动 → 退出 → 卸载 → 重装**，用户数据自始至终完好。

**没有做的**：GitHub Actions 尚未在 GitHub 上实际运行过（它的命令已被逐条跑过，
并因此发现了一个会让 CI 首跑即红的错误）。这不是 §16 的闸门，也不是 §17 的一行，
但在 `docs/release/TEST_REPORT.md` 里如实标着。

### 新增

- **统一结果契约（`run_outcome.py`）。** 一个运行"产出了什么"只有一种形状：状态、
  摘要、产物、验证证据、可执行的后续动作、以及一个指向专家日志的引用。
  **读侧投影，不是第二个状态权威** —— 每个字段都是现算的，不落库。
  契约文档：`docs/product/RUN_OUTCOME_CONTRACT.md`。
- **成果出现在运行结束的地方。** 运行页原先只写"产出文件在「交付物」里，可以直接
  打开"，然后给一个跳到另一页的按钮。现在文件名、类型、大小、版本、验证证据和能真
  按下去的按钮都在原地，并且带**应用内预览**。
- **按类型验证产物。** `md`/`txt` 必须严格 UTF-8 可解码且非空；`docx`/`xlsx`/`pptx`
  必须是 zip **且**含该格式的标志性部件；`csv`/`pdf` 读回行数/页数；`code` 明确报告
  "本次没有运行任何编译或测试"。
- **验证有了第三种结果。** 检查项是 `pass` / `fail` / `unverified` 三态。
  读不出文件（缺库、格式没有读取器）既不是通过也不是失败 —— 把它写成 `fail` 是给一个
  好文件定罪，写成 `pass` 是给一个没人打开过的文件放行。旧的只带 `ok` 的行仍能读。
- **员工岗位合同**（`EmployeeContract.qml`，12 节）、**记忆七类分类**、**员工声明字段**。
- **普通 / 专家模式**的导航：普通模式 13 个入口，运行记录、图谱、Planner、权限、搜索、
  问答只在专家模式出现。
- **`tools/`** —— 验收与安全工具进了版本控制（原先在 `build/rc/`，而 `build/` 被
  gitignore 覆盖，干净克隆里没有它们）。
- **`SECURITY.md`**、**GitHub Actions**（CI 自动跑静态检查与后端测试；Windows 安装包
  构建是手动 workflow）。

### 修复

- **本地服务被发给了系统代理。** `getproxies()` 在 Windows 上不读 `ProxyOverride`，
  所以 `127.0.0.1:6333` 被送进了代理 —— 代理开着是 `401`，关着是 `ConnectError`。
  横幅说「向量检索降级」，而 Qdrant 一直是健康的；`AsyncQdrantClient` 同样被拦，
  于是语义检索静默退化成关键词检索。已修（只豁免 loopback）。
- **安装包里的应用自称 2.5.0。** 打包用的 PyInstaller 树比版本号提交早 23 分钟，
  而应用读的正是树里的 `_internal/VERSION`。已重建，并把核对方法写成命令。

- **卸载不干净，整个程序目录留在磁盘上。** 静默卸载 2.5.0 之后，
  `%LOCALAPPDATA%\AI Workbench` 里还剩 158 个文件 / 3.75 MB。其中 `.env` 是**故意**
  保留的（它保存 SECRET_KEY，删了用户重装后读不出自己的数据），而 `cache/`（157 个
  文件的 QML 编译缓存）是**漏的** —— 它由应用在首次运行时创建，安装器从不写它，
  正因为如此才没人想到要列进卸载清单。更糟的是，因为它没被删掉，最后那句
  `RMDir "$INSTDIR"` 也静默地什么都没做。已修，卸载后只剩 `.env` 一个文件。
- **干净克隆跑不通。** 在一个全新目录里 `git clone` → 建 venv → 按 README 装依赖 →
  跑测试，得到 4 个"换台机器就红"的缺陷。**四个都不是开发机上看得到的**：
  - `desktop-native/verify_projection.py` 用了 Python 3.12 的 f-string 语法，而项目
    声明 `requires-python = ">=3.11"` —— 该文件在 3.11 上**根本解析不了**。
  - CI 的 `ruff --config backend/pyproject.toml` 让 `per-file-ignores` 失效：
    CI 报 131 个错误，本地 0 个。
  - README 让新用户跑 `pytest`，但依赖里没有 PySide6（两个测试模块导入桥接层）——
    测试在**收集阶段**就死了，一个都没跑。
  - 测试套件依赖三个 README 没写的机器本地前提（Qdrant 的 key、`pg_dump`、
    项目自己的 `.env`），缺一个就是 29 条失败。
  - `test_permissions_effective.py` 断言项目自己的 `.env` 必须存在，而 `.env` 被
    gitignore（这是对的）—— 于是它在干净克隆里**失败**而不是跳过。
    改为测试期间创建、结束后删除。
- **`_persist_run` 从来没有执行过。** 提交 `591fa2e` 里读的是 `run.started_at`，而
  `AIRun` 没有这一列（它属于 `ToolCall`）。`AttributeError` 被 `try/except` 吞掉，
  整个函数跳过 —— 运行照样 COMPLETED，交付物照样登记，唯一痕迹是一行日志。
  **那个想让成功有结束时间的提交，正是拿掉结束时间的提交。** 已修，并补了断言。
- **文件被删除后，界面仍显示 `0 B`。** `0 B` 会被读成"文件是空的"，那是另一件更糟的
  事。文件不在时不再报大小。
- **`test_workspace_layout.py` 把运行时目录硬编码成 `.runtime`。** 它测的不再是
  docstring 里写的"默认位置在产品内"，而变成了"目录必须叫 `.runtime`"。
  改为从 `runtime_root()` 推导。
- **两个秘密扫描器的缺陷**，都是"工具说没问题，而它其实什么也没查"：
  一个扫了 0 个对象却报"历史干净"，一个报了 1406 处假阳性。

### 变更

- 运行终态的判定从 `_watch` 的内联代码提取为 `decide_final_status()`。行为逐字不变，
  但它现在可以**不启动真实模型**就被测试。
- 首页、交付物页、Agent 页的状态词与类型名统一到 `theme.js`，两处不再各写一份。

### 已知限制

见 `RELEASE_NOTES.md`。简述：仅 Windows；真实桌面截图未做（构建环境无头，且
`offscreen` 平台报告零个字体族，用它判断字形会得出错误结论）；`code` 类产物不运行
检查；没有文档转换器所以没有「导出」；`ai_runs.total_tokens` 对自主运行不写入。

## [2.5.0] — 2026-10-14

私人 AI 员工工作台。这一版把"能跑"变成"说得准"。

**首次真实端到端运行**（真实员工 + 真实模型 + 真实文件），并且它抓出了一个已发布的缺陷。

- **任务状态不再撒谎。** `set_state(COMPLETED)` 之后 agent 还在干活：它要生成最终报告
  （又一次模型调用），之后服务才落库、才释放员工。而构建运行只看了内存里的状态，于是
  把一次**还在写**的运行标成了「完成」，并从里面收走了交付物。实测：构建运行 COMPLETED，
  而 `ai_runs.status` 还是 RUNNING、`finished_at` 为空、员工还是「工作中」。
  现在 `RunController.settled` 才是结束信号，由 `_execute_run` 的 `finally` 写入。
- **员工释放放进 `finally`。** 之前它在正常路径上，任何一次中途异常都会把员工永久留在
  「工作中」。现在每条退出路径都会释放，且用独立会话、不抛异常。
- **重启会把卡住的员工收回名册。** 「工作中」和「等待授权」都由进程内对象驱动，重启就
  不再成立，而此前没有任何东西清除它们。
- **`ai_runs.finished_at` 从没被写过。** 一条状态为 SUCCEEDED 却没有结束时间的记录，既算
  不出耗时，也分不清它跟"还在跑"的区别。

**界面**

- 首页重做：问候 + 在岗员工 → 输入框 → **最近成果**（可打开、可继续修改）。原来的
  「最近项目 / 最近任务 / 最近运行」是按数据模型组织的，用户找不到自己刚生成的文件。
- 交付物页新增 打开 / 在文件夹中显示 / 继续修改 / 重新生成。「重新生成」重跑的是**原来
  那句请求**（从产生它的运行里查），不是拿标题猜。
- **Agent 页终于走员工路径。** 在此之前，员工存在、能显示、能收窄权限，但从这个页面发起
  的任务不经过任何员工 —— 任务书 §12 的验收链路在这里是断的。
- 新增 **普通 / 专家模式**（按账号持久化）：普通模式只给任务、状态、结果；专家模式加上
  步骤、工具调用、token 和延迟。

修复：

- 首页读错字段名（`summary.llm` 而非 `summary.providers.llm`），导致它对一个配好 Key 的
  用户说"未配置 API Key"。写错字段名不抛异常，只产生一句假话。
- 页面加载检查器只过滤 `shell` 相关报错，漏掉 `bridge`，把 `AgentWorkbenchPage` 里早就
  存在的代码报成坏页面。

## [2.4.0] — 2026-10-13

私人 AI 员工工作台。

**员工是一个实体，不是一段提示词。** 新增 `employees` 表与六个岗位模板。每个员工有使命、
职责、不做什么、工具白名单、权限档案和记忆范围，并且这些字段**真的约束执行**——一次运行
会按员工的合同收窄权限和工具集，而不是只影响它显示成什么样。

- 新增「我的员工」「交付物」「记忆」「待审批」四个页面。
- 打通桥接层：交付物的 19 个接口、记忆的 20 个接口此前在界面上完全不可见。
- 权限只收敛不放宽：员工的 `level` 取更小者、`mode` 朝更保守者、可写目录取交集、
  禁止目录取并集、所有上限取更小者。一条存储数据无法抬高用户自己的预算。
- 人格护栏：人格可以定制语气，但不能改变权限。它**报告**而不是拦截，因为权限在执行端
  读取，不读提示词。

修复：

- 版本号不一致（`VERSION` 说 2.3.0，Tauri 外壳、前端和旧安装包仍说 2.2.0）——上一轮
  发版时只同步了新产物，且没有跑全量测试。
- `backend/alembic/env.py` 的 4 处 lint 错误（该文件不在任何人跑过的 lint 范围内）。
- 员工名册与 Planner 的按钮列错位；布局审计新增「只判定纵向列表」的规则，避免把并排的
  卡片布局误报为缺陷。

## [2.3.0] — 2026-10-11

窗口与图标。

- 系统标题栏改用 DWM 着色，与应用的深蓝黑一致，不再是一条白带。仍是原生边框：拖动、
  贴靠、双击最大化、系统菜单、逐显示器 DPI 和缩放边框都照旧。
- 窗口图标此前从未设置（`QGuiApplication::setWindowIcon` 没有被调用过），标题栏和任务栏
  显示的是 Windows 的占位图；EXE、安装包和快捷方式的图标一直是正确的。图标文件现在随包发布。
- `--self-check --windowed` 用真实窗口跑那些必须有窗口才能验证的检查。

## [2.2.0] — 2026-10-09

Release-freeze round. Every entry below is a defect that was confirmed against the source
and is covered by a regression test that fails on the previous code. The full table,
including what is still open, is `RELEASE-FREEZE-ISSUES.md`.

### Security

- **Logout no longer fails open.** `is_token_revoked` read Redis, and a Redis outage made
  every logged-out token valid again. Revocations are recorded in a new `revoked_tokens`
  table (with a migration) and Redis is a fast path in front of it; the check answers
  revoked / not_revoked / storage_unavailable, and a request is refused in the third case.
- **Passwords are no longer truncated at 72 bytes.** The old code applied bcrypt's limit
  itself, so two passwords agreeing on their first 72 bytes were one password and a
  multibyte password could be cut mid-character. The scheme is versioned and pre-hashes the
  whole password; existing hashes still verify and are upgraded on the next login.
- **Production refuses placeholder keys.** The validator compared against one exact string
  and the length, so the 47-character placeholder that actually shipped in `.env.example`
  passed. It now rejects placeholder text, short values and degenerate patterns.
- **A fresh install is a production install.** The installer copied `.env.example` to
  `.env`, which selects `APP_ENV=dev` and seeds `demo / demo1234`. The launcher now writes
  a production file with a per-install random key, and never overwrites an existing one.
- **Skill discovery had no domain check.** `_authorise` treated a missing policy as
  "allowed", and `POST /skills/discover` never passed one. A missing policy is now a
  denial, and redirects are re-authorised at every hop instead of followed blindly.
- **One account could change another's AI provider.** Provider, model, base URL and API key
  lived in one shared file. They are now layered per account, and a request can only write
  its own layer. Base URLs are validated (https for remote, no embedded credentials).
- **Qdrant had no authentication and CORS was on.** The launcher now generates and enforces
  an API key that the backend reads from the same file, and CORS is off.
- **docker-compose published everything on every interface** with a default PostgreSQL
  password and an unauthenticated Redis. The data services are no longer published; the API
  and frontend bind to loopback; the credentials are required.

### Fixed

- **A stopped agent is no longer recorded as a success.** `max_steps`, `loop_detected` and
  `empty_response` produced explanatory text, and the truthiness of that text was the whole
  success test. New terminal states `INCOMPLETE` and `INTERRUPTED` say what happened, and a
  delivery task's success is verified against the file it claims to have produced.
- **A crashed job is no longer retried forever.** `requeue_stale` ignored
  `attempts`/`max_attempts`. An exhausted job is now terminal `FAILED`, and its document is
  marked failed with it.
- **RAG grounding is honest.** One valid citation marked the whole answer grounded. Every
  claim-bearing sentence must now cite a real source, and an ungrounded answer says so.

### Changed

- Single version source: `VERSION` at the repository root, propagated by
  `scripts/sync_version.py`. The shell, installer, backend and frontend had reported four
  different versions.
- `pytest` no longer carries a duplicate `-q`, which was suppressing the summary.

## [1.1.0] — 2026-10-03

Real DeepSeek integration plus a runtime provider control surface. Verified against the
live DeepSeek API: **60/60 integration checks**, **75/75 end-to-end assertions**, and a
full RAG → planner → agent chain executed by `deepseek-flash`.

### Added

- **DeepSeek provider** (`app/ai/providers/deepseek.py`) — reuses the OpenAI-compatible
  transport and adds DeepSeek's defaults, live model discovery via `GET /models`,
  precise error translation, and handling of the reasoning-model contract.
  Default model is **`deepseek-flash` (DeepSeek V4.1 Flash)**.
- **Provider registry** (`app/ai/providers/registry.py`) — a declarative catalogue of
  DeepSeek / OpenAI-Compatible / Ollama / Offline with labels, kinds, default models,
  docs links and per-provider model lists. Adding a vendor is a two-step change.
- **Runtime AI configuration** (`app/ai/runtime_config.py`) — provider, model, base URL,
  API key, temperature, max tokens and timeout are resolvable at runtime with the
  precedence **runtime override → `.env` → default**. Overrides persist to
  `.runtime/ai-config.json` (git-ignored, local only) and take effect on the next AI
  call without a restart.
- **AI provider API** (`/api/v1/ai-config`) — 8 endpoints: read the effective
  configuration, list providers, list models (`live=true` asks the vendor), read health,
  patch the configuration, clear overrides, toggle a provider, and run a real
  connection test that returns latency and token usage.
- **Settings → AI Provider UI** — a working control panel: current effective provider
  banner with an explicit degradation warning, provider cards with enable switches and
  configured/active badges, dynamic model picker per provider with a custom-model
  escape hatch, base URL, write-only API key field, temperature / max-tokens / timeout,
  "apply", "reset to .env", and a "test connection" button. The tab is now the default
  view of Settings.
- **DeepSeek-aware error taxonomy** — `ai_insufficient_balance` (HTTP 402, not
  retryable), `ai_model_not_found`, and `ai_reasoning_budget_exhausted`.
- **Agent run provenance** — `knowledge_search` now records the chunks it retrieved as
  `ai_run_sources` on the agent run, so an agent run can answer "which documents did
  this use?" exactly like a RAG run, and the knowledge graph gains those edges.
- **Live integration tests** (`tests/integration/test_deepseek_live.py`) — real API
  calls that **skip automatically** with a stated reason when no key is configured.
  The key is read from the environment and never referenced in test source.
- **Verification harness** (`scripts/verify_deepseek.py`) — 11 groups / 60 checks
  covering connectivity, RAG, document analysis, planner, agent, the AI run trace,
  provider switching, bad key, unreachable host, retry, fallback and honest labelling.

### Fixed

Five real defects, all found by running against the live API rather than a mock:

- **JSON mode was rejected with HTTP 400.** DeepSeek refuses
  `response_format={"type":"json_object"}` unless the prompt literally contains the word
  "json". The provider now satisfies the requirement in one place instead of making
  every prompt author remember a vendor quirk.
- **`reasoning_content` was not echoed back.** `deepseek-flash` is a reasoning model and
  rejects a tool-loop follow-up that omits the previous assistant turn's chain of
  thought ("The `reasoning_content` in the thinking mode must be passed back"). The
  value now travels on `ChatMessage` / `ChatResult` and is replayed by the agent.
- **Truncated JSON lost the whole plan.** A long structured answer cut off by the token
  ceiling used to fail the entire request. The parser now recovers the complete part by
  closing open structures and trimming back to the last valid boundary, bounded so a
  pathological input cannot spin.
- **A 1-token connection probe reported a false failure** for a healthy reasoning
  endpoint, because the whole allowance went on the chain of thought. The probe budget
  is now 256 tokens.
- **`str(None)` made an empty API key look configured.** The resolver returned the
  string `"None"` when every candidate was empty, which silently selected a cloud
  provider with no credentials. The fallback now returns the default value itself.
- **Task plans could ship without completion criteria.** Models occasionally omit the
  field even though the prompt requires it. The plan normaliser now enforces the
  contract and logs the under-delivery rather than shipping an empty criterion.
- **Pure unit tests required a database.** The test suite's schema fixture was
  `autouse`, so `pytest tests/unit` failed on a machine with no PostgreSQL — contradicting
  the README. The fixtures are now opt-in, and all 246 unit tests run with no services.

### Changed

- `AI_MAX_TOKENS` default raised from 2048 to **8192** — reasoning models need room for
  the chain of thought before the answer, and the old value silently produced empty
  responses.
- The agent loop's per-step budget is now `max(1024, min(ai_max_tokens, 3000))`.
- Provider error messages now include the vendor's own explanation verbatim. A 400 used
  to surface as "unexpected HTTP 400" and nothing else, which made the two defects above
  invisible.
- `AI_PROVIDER` defaults to `deepseek`; `DEEPSEEK_API_KEY` / `DEEPSEEK_BASE_URL` /
  `DEEPSEEK_MODEL` have their own environment namespace.
- API keys are masked as `sk-****(len=35)` — the prefix and length only, never the tail,
  because these values end up in screenshots and bug reports.

### Security

- The API key lives only in `.env` (git-ignored) or `.runtime/ai-config.json`
  (git-ignored, excluded from the delivery archive). Verified: it appears in **no Git
  commit**, no tracked file, no log line, no screenshot and no archive entry.
- Reads never return the key — only `api_key_set` and a mask.

## [1.0.0] — 2026-10-03

First complete, verified release. Every item below was executed and checked on the
development machine (Windows 11, native PostgreSQL 16.4 / Redis 5.0.14 / Qdrant 1.12.4).

### Added

**Knowledge**
- Markdown / TXT / PDF ingestion with extension and MIME validation, streaming size
  limits and content-hash duplicate detection
- Heading-aware recursive chunker with sentence-boundary packing, overlap and a
  heading breadcrumb prefixed to each chunk's embedded text
- Asynchronous ingestion pipeline (parse → normalise → chunk → embed → Qdrant) with
  per-stage progress, retries and a persisted failure reason
- Qdrant adapter with lazy collection creation at the embedding provider's real
  dimension, payload indexes and tenant-scoped filtering

**Retrieval & RAG**
- Keyword, semantic and hybrid search; hybrid fusion is a min-max normalised weighted
  sum with the dense and sparse scores returned separately for explainability
- Lexical reranker (query-term coverage, bigram phrase hit, heading match, length prior)
- RAG pipeline: query rewrite → retrieval → context assembly → generation → citation
  extraction, with the retrieval metadata and sources persisted on the AI run
- Citation-enforced prompt; an empty knowledge base produces an honest "nothing found"
  answer instead of a hallucination

**Tasks & planning**
- Goal → phase → task → subtask planner with estimates, priorities, dependencies and
  verifiable completion criteria
- Task tree with cycle-safe dependencies, status transition rules, automatic
  unblocking of children, and a `task ↔ document` knowledge link
- 7-day study-plan generation

**AI engineering**
- `LLMProvider` / `EmbeddingProvider` abstraction with three real implementations:
  OpenAI-compatible, Ollama (fully local) and a deterministic offline engine
- Automatic, *reported* degradation to the offline engine when no cloud key is present
- Versioned prompt registry with a full version history, activation and rollback
- Tool-calling agent: 11 typed tools, bounded ReAct loop, duplicate-call detection,
  write-operation cap, and a database-reachable only through tools
- AI execution tracing: prompt name **and version**, retrieved sources, tool calls,
  tokens, latency, retries, cost estimate and classified errors
- Bounded retry with exponential backoff, jitter and a wall-clock budget
- Structured-output parsing tolerant of fenced or prose-wrapped JSON

**Platform**
- 22-table relational schema with Alembic migrations (verified up and down)
- 49 REST paths / 65 operations under `/api/v1` with a uniform envelope, pagination
  and a typed error taxonomy
- JWT authentication with bcrypt hashing and per-user data isolation
- Redis caching for search, prompts, stats and embeddings, with namespace-version
  invalidation and graceful degradation
- Database-backed worker using `SELECT … FOR UPDATE SKIP LOCKED`, bounded retries,
  stale-job recovery and a job-level timeout
- Dashboard statistics computed entirely from the database — no seeded demo data
- Knowledge graph projection over projects, knowledge bases, documents, concepts,
  tasks, notes and AI runs
- React 18 + TypeScript + Vite + Tailwind frontend: 18 pages, three-column dark-first
  developer-tool shell, `Ctrl+K` command palette, Markdown editor with live preview,
  force-directed graph, and loading / empty / error / skeleton states throughout
- Native project-scoped runtime management (`fetch-runtime`, `infra-start/stop/status`,
  `dev`) plus `start.bat` / `stop.bat` / `start.sh` / `stop.sh`
- `docker-compose.yml` and Dockerfiles for all services (**provided but not executed**
  on the development machine — see the warning in that file)
- Test suite: 270 backend tests (194 unit / 51 integration / 25 API) at 80.4% coverage,
  114 frontend tests, and a 75-assertion end-to-end smoke test
- Documentation set: architecture, database, API, AI, RAG, agent, deployment, testing,
  security, development, plus a 10-chapter thesis support pack

### Fixed

Ten real defects found during verification; each now has a regression test.

- **Worker event-loop starvation** — the main loop returned without awaiting whenever
  every slot was busy, freezing the entire process including the HTTP API
- **Reserved logging key** — `extra={"message": …}` raised `KeyError` *inside the
  exception handler*, turning a clean 409 into a 500; fixed at the call site and with
  a central guard
- **`MissingGreenlet` on serialisation** — server-generated `updated_at` was expired
  after flush and lazily reloaded; fixed with `eager_defaults`
- **Wrong entity from generic repository helpers** — cross-entity queries selected
  `self.model`; replaced with explicit `list_of` / `get_one_of` / `count_of`
- **Stale service imports in agent tools** — write tools silently returned `ok: false`
  after a module merge; fixed and guarded by an import-integrity test
- **Self-deadlock when recording a failure** — a second session waited on a row lock
  held by the doomed transaction; the transaction is now rolled back first
- **Tokenizer string expansion** — `extend(run)` emitted single CJK characters and
  flooded the embedding with noise
- **Duplicate index name** — column `index=True` plus an explicit `Index` broke
  `create_all` with `DuplicateTableError`
- **Over-aggressive payload sanitiser** — real task plans were replaced with
  `"<max-depth>"`; the nesting cap was raised above any structure the product produces
- **Fragile context discovery** — the offline engine could answer from the prompt's
  own instructions; templates now wrap material in explicit `<document>` /
  `<context>` tags

### Security

- Extension allow-list with MIME consistency checking and a streaming size limit
- UUID-generated stored filenames (no user-controlled paths) and storage-root
  containment checks
- JWT (HS256) with bcrypt password hashing and constant-time credential comparison
- Parameterised queries only; LIKE wildcards escaped in user-supplied search input
- CORS allow-list, secrets via git-ignored `.env`, secret redaction in logs

### Known limitations

- No OCR: a scanned PDF without a text layer fails with a clear message
- The reranker is lexical; no cross-encoder model is shipped
- AI responses are not streamed token by token
- `docker-compose.yml` is provided but was **not** executed on the development machine
  (no Docker, unreachable Docker Hub)
- No rate limiting, refresh-token rotation, audit log or upload virus scanning
- Single-node deployment; no horizontal scaling story beyond running more workers

[1.1.0]: https://github.com/your-username/ai-workbench/releases/tag/v1.1.0
[1.0.0]: https://github.com/your-username/ai-workbench/releases/tag/v1.0.0
