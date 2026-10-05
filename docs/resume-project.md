# Resume project description

<!-- scope -->
> **Scope: the platform.** The original write-up, covering the knowledge pipeline, RAG,
> the task system and the backend engineering.
>
> For the agent, the personal context layer, document generation and the desktop
> delivery, see [`resume.md`](resume.md). Neither supersedes the other.


Copy-ready text for a software-engineering internship application. Three lengths are
provided: a one-line entry, a standard block, and a detailed version for a
project-experience section.

Everything below is verifiable in this repository. Do not add claims that are not.

---

## One-line entry

> **AI Workbench** — 本地优先的 AI 知识与任务执行平台（FastAPI · PostgreSQL · Redis ·
> Qdrant · React/TypeScript）。实现带引用的 RAG、工具调用 Agent 与全链路 AI 执行追踪。

---

## Standard block (recommended)

**AI Workbench — 本地优先的 AI 知识与任务执行平台**
*独立完成 · 2026.08 – 2026.10 · 个人项目*

**项目介绍**
面向"资料多但找不到、看过但记不住、知道要做什么却没形成任务"的真实痛点，构建了一个把
**资料 → 知识 → 任务 → 执行 → 结果 → 再沉淀为知识** 完整闭环的平台。用户导入
Markdown / TXT / PDF 后，系统完成解析、分块、向量化与检索，支持带引用的知识问答、
文档智能分析、目标到任务树的规划，以及由工具驱动的 AI Agent 执行。

**技术栈**
Python 3.13 · FastAPI · SQLAlchemy 2.0 (async) · asyncpg · Alembic · Pydantic v2 ·
PostgreSQL 16 · Redis 5 · Qdrant 1.12 · React 18 · TypeScript · Vite · Tailwind CSS ·
TanStack Query · Zustand · Pytest · Vitest · Ruff

**核心工作**

- **分层架构与 API 设计**：按 `api → services → repositories → models` 分层，接口层
  只做校验与序列化，SQL 只出现在仓储层。设计并实现 49 个 REST 路径 / 65 个操作，
  统一响应信封、分页契约与 20 余种错误码，由 OpenAPI 自动生成文档。
- **RAG 检索增强生成**：实现查询改写 → 混合检索（Qdrant 稠密向量 + PostgreSQL
  候选集生成后 BM25 打分）→ 加权归一化融合 → 词面重排 → 上下文预算组装 → 生成 →
  引用抽取的完整流水线。强制模型只能依据检索片段作答并标注 `[n]` 引用编号，
  检索为空时明确返回"无依据"而非编造。
- **文档入库流水线**：设计解析 → 清洗 → 标题感知分块 → 批量向量化 → 写入向量库的
  异步流水线，用内容哈希做去重与幂等重跑，并记录分阶段进度与失败原因。
- **AI 工程化**：抽象 `LLMProvider` / `EmbeddingProvider`，实现 OpenAI 兼容、
  Ollama 本地与确定性离线引擎三种实现，切换仅需改配置；建立带版本历史的 Prompt
  注册表，每次 AI 调用都记录所用 Prompt 名称与版本；实现超时、有限重试、指数退避、
  错误分类、结构化输出解析、Token 与延迟统计。
- **工具调用 Agent**：设计统一 Tool 接口与 11 个工具，实现有界 ReAct 循环。
  Agent 无法直接访问数据库，只能通过工具 → 服务 → 仓储的链路操作数据；加入重复调用
  检测、单次运行写操作上限与最大步数限制，保证可审计、可回放。
- **异步任务与缓存**：基于 PostgreSQL 实现任务队列（`FOR UPDATE SKIP LOCKED` 抢占、
  有限重试、过期任务回收、任务级超时），避免引入 Windows 不友好的消息中间件；
  用 Redis 缓存检索结果、Prompt、统计与向量，并设计命名空间版本失效策略。
- **测试与工程化**：编写 270 个后端测试（单元 / 集成 / API）与 114 个前端测试，
  覆盖率 80.4%；编写 75 项断言的端到端验收脚本；配置 Ruff、ESLint、Prettier、
  Alembic 迁移与 Docker Compose。

**项目难点**

1. **混合检索的融合与可解释性**：稠密与稀疏两路分数量纲不同，需要先归一化再加权；
   同时把两路分数分别返回前端，让用户看到"为什么这段被召回"。
2. **异步 Worker 的事件循环饥饿**：Worker 主循环在槽位占满时不 await 直接返回，
   导致事件循环被饿死、整个进程（含 HTTP 接口）冻结。通过"无任务时必须让出控制权"
   修复，并补充了测量并发协程最大停顿间隔的回归测试。
3. **失败状态的一致性**：入库流水线抛异常会回滚整个事务，导致文档永久停留在
   PROCESSING。解决方式是在失败路径上先回滚已废弃的事务释放行锁，再用独立事务写入
   失败状态——直接开第二个事务会与外层行锁形成自死锁。
4. **跨实体查询的隐式错误**：通用仓储方法始终按 `self.model` 生成 SQL，用它查询子实体
   会返回错误类型。改为显式 `list_of / get_one_of / count_of` 并在全仓审计。
5. **Prompt 与上下文的边界**：离线引擎需要在不依赖模型的情况下定位"资料"，早期实现
   会把 Prompt 自身的指令当成资料。最终在模板中用 `<document>` / `<context>` 显式标记，
   使上下文来源不再依赖猜测。

**项目亮点**

- 完整闭环而非单点功能：知识可关联到任务，任务可追溯到 AI 执行，AI 回答可追溯到具体文档片段
- 真实的 AI 工程能力：Provider 抽象、Prompt 版本化、执行追踪、降级可观测，而非简单调用 API
- 数据可信：仪表盘所有数字均来自数据库实时计算，无任何内置演示数据
- 本地优先：模型可切换为本地 Ollama，知识库与向量可完全留在本机
- 工程质量：分层清晰、异常不吞、超时与重试有界、270 个测试 + 80.4% 覆盖率

---

## Detailed version (for a project-experience section with bullet points)

```
AI Workbench · 本地优先 AI 知识与任务执行平台                    个人项目 · 独立完成
Python 3.13 / FastAPI / SQLAlchemy 2.0 async / PostgreSQL 16 / Redis 5 / Qdrant 1.12
React 18 / TypeScript / Vite / Tailwind / TanStack Query / Zustand

· 独立完成需求分析、架构设计、数据库设计、API 设计、AI 流水线设计、前后端开发、
  测试、容器化与文档编写，产出可运行、可验证、可开源的完整项目
· 设计 22 张表的数据库模型（含任务树自引用、任务↔知识关联、Prompt 版本、AI 执行追踪、
  后台任务队列），使用 Alembic 管理迁移并通过 up/down 双向验证
· 实现带引用强制的 RAG：查询改写 + 稠密/稀疏混合检索 + 归一化加权融合 + 词面重排 +
  上下文预算组装，答案必须标注来源编号，无依据时明确返回"检索不到"
· 实现标题感知分块器（按 Markdown 标题切分小节、按句边界打包、保留重叠、前缀标题路径），
  显著提升中文技术文档的召回质量
· 抽象 LLM / Embedding Provider 三种实现（OpenAI 兼容 / Ollama 本地 / 确定性离线引擎），
  配置切换、无需改代码；缺少密钥时自动降级并在健康检查与每次执行中如实标注
· 实现带版本历史的 Prompt 注册表，业务代码按名称取用，AI 执行记录中保存实际使用的版本号
· 实现工具调用 Agent：11 个带 JSON Schema 的工具、有界 ReAct 循环、重复调用检测、
  写操作配额；Agent 只能经 Tool → Service → Repository 访问数据
· 实现基于 PostgreSQL 的异步任务队列（SKIP LOCKED 抢占、指数退避重试、过期回收、任务超时），
  规避了 Windows 上 Celery 的兼容问题，并保证任务状态与业务数据同事务
· 实现 AI 执行追踪：请求、Provider、模型、Prompt 版本、检索片段、工具调用、Token、
  延迟、重试与错误分类全部落库，支持按 run_id 完整回放
· 编写 270 个后端测试（单元 194 / 集成 51 / API 25，覆盖率 80.4%）与 114 个前端测试，
  以及 75 项断言的端到端验收脚本；期间定位并修复 10 个真实缺陷
```

---

## Suggested interview talking points (pick two or three)

1. **The closed loop.** Most AI projects stop at "ask a question". Explain how a document
   becomes knowledge, how knowledge links to a task, and how an AI run records which
   documents produced its answer — and why that makes the system auditable.
2. **Citation enforcement.** The prompt forbids answering from prior knowledge, and an
   empty knowledge base produces an explicit "no material found" instead of a
   hallucination. Show the trace that maps `[n]` back to a chunk id.
3. **Provider abstraction and honest degradation.** One interface, three implementations,
   and a health endpoint that tells the truth about which one is answering.
4. **The worker starvation bug.** A one-line-looking bug that froze the whole process.
   It demonstrates debugging an async event loop, not just writing endpoints.
5. **The ten real defects.** Having found and fixed ten genuine bugs — each with a
   regression test — is more persuasive than claiming nothing went wrong.

## What NOT to claim

- A hosted-model quality evaluation (only the offline engine was exercised)
- Production traffic, users or scale numbers
- Docker verification (Docker was unavailable on the development machine)
- Benchmarks or latency comparisons you did not measure
- "国内领先" / "行业首创" — describe the engineering, not the marketing
