# 基线审计：AI Workbench 现状

审计时间：2026-10-13
审计对象：`C:\Users\user\Desktop\ai-workbench`
基线提交：`72d9a255af9236c2a818f7f937b5f9b040c0e774`（分支 `fix/release-freeze-20261009`，版本 2.3.0）
审计分支：`feat/private-ai-employee`

审计方法：读源码、跑测试、跑真实接口。**不采信文档与历史报告**——本仓库根目录有 30 余份 `*REPORT*.md` / `*VERDICT*.md` / `FIX-ROUND-*.md`，它们是开发过程记录，不是事实来源。凡本文出现"文档声称"，都已标注为不可采信。

---

## 0. 一句话结论

**这个项目要做的"私人 AI 员工工作台"，其底层能力大约已经完成 80%，但其中一大半在界面上看不见。**

- 后端已有 34 张表、123 个 API 路径 / 155 个操作，覆盖记忆治理、交付物验证、权限分级、审批等待、工作区快照回滚、运行中断对账。
- 桌面端有 16 个 QML 页面，但**桥接层只暴露了 44 个方法**，其中**没有一个是交付物（deliverables）相关的**——后端有 19 个交付接口，界面一个都没用。
- 真正缺失的不是引擎，是**"员工"这一层实体**，以及把这些已有能力**组织成用户能看懂的工作流**的界面。

---

## 1. 技术栈与实际形态

| 项 | 实际实现 | 证据 |
|---|---|---|
| 桌面端 | **PySide6 / Qt 6 / QML 原生**，非 WebView、非 Electron | `desktop-native/qml/Main.qml`、`desktop-native/main.py` |
| 后端 | FastAPI + SQLAlchemy 2.x (async) + Alembic + Pydantic | `backend/app/main.py`、`backend/pyproject.toml` |
| 数据库 | PostgreSQL 16（本地独立进程） | `scripts/infra-start.ps1` |
| 缓存/队列 | Redis 5（本地独立进程） | 同上 |
| 向量库 | Qdrant 1.12（本地独立进程） | `backend/app/infrastructure/vector_store.py` |
| 进程模型 | 桌面端启动时拉起 PostgreSQL/Redis/Qdrant 子进程；关闭时回收 | `desktop-native/awbnative/runtime.py`、`launcher` |
| 打包 | PyInstaller（应用）+ NSIS（安装包） | `desktop-native/ai-workbench-native.spec`、`installer/ai-workbench-native.nsi` |
| 数据目录 | `%LOCALAPPDATA%\AI Workbench Data\.runtime\`（数据库、配置、日志） | `desktop-native/awbnative/resources.py` |

**界面导航实际结构**（`desktop-native/qml/theme.js:157` 的 `navGroups`，唯一权威来源）：

```
工作区  → 工作台 dashboard / 项目 projects / 任务 tasks / 文档 documents / 笔记 notes
AI 工作 → 对话 conversation / Agent agent / 运行记录 runs
知识    → 知识库 knowledge / 问答 ask / 搜索 search / 图谱 graph
计划    → Planner planner
系统    → 权限 permissions / 设置 settings
```

共 15 个入口 + 1 个详情页（`ProjectDetailPage`）= 16 个页面文件。

---

## 2. 已有能力清单（按模块）

### 2.1 数据模型：34 张表

`backend/app/models/` 下 13 个文件定义 34 张表：

| 域 | 表 |
|---|---|
| 用户与画像 | `users`、`user_profile`、`user_preferences`、`revoked_tokens` |
| 记忆 | `memory_entries`、`memory_candidates`、`context_snapshots` |
| 知识与文档 | `knowledge_bases`、`documents`、`document_chunks`、`document_tags`、`document_concepts`、`concepts`、`tags` |
| 项目与笔记 | `projects`、`notes` |
| 任务 | `tasks`、`task_dependencies`、`task_tags`、`task_documents` |
| 运行与工具 | `agent_runs`、`ai_runs`、`ai_run_sources`、`tool_calls`、`tool_audit_log`、`activity_events` |
| 交付 | `build_runs`、`deliverables` |
| 对话与提示词 | `conversations`、`messages`、`prompts`、`prompt_versions` |
| 技能与作业 | `skills`、`installed_skills`、`jobs` |

**注意**：没有 `employees` / `personas` / `roles` 表。这是本轮要补的核心缺口。

### 2.2 记忆体系——已实现，且比预期成熟

`backend/app/ai/memory/`（2464 行）+ `/api/v1/memories` 等 20 个接口：

| 能力 | 实现位置 | 状态 |
|---|---|---|
| 记忆条目（kind / scope / content / confidence / source / status / confirmed_by_user / last_used_at / use_count） | `models/context.py:103` `MemoryEntry` | 源码已实现 |
| **候选记忆 → 用户审批**流程（proposed_kind / reason / confidence / status=pending / conflicts_with） | `models/context.py:147` `MemoryCandidate` | 源码已实现 |
| 上下文装配 + token 预算 + 截断（profile_digest / memory_ids / knowledge_chunk_ids / token_estimate / truncated） | `models/context.py:196` `ContextSnapshot`、`ai/memory/assembler.py` | 源码已实现 |
| 记忆评估 / 去重 / 相似度 / 相关性 | `ai/memory/evaluator.py`(698) / `similarity.py` / `relevance.py` | 源码已实现 |
| 记忆清理：**先出计划、再执行**（plan 写零，apply 才写） | `ai/memory/cleanup.py`、`POST /memory-cleanup/plan`、`/apply` | 源码已实现 |
| 记忆生命周期 / 过期 | `ai/memory/lifecycle.py`(500) | 源码已实现 |
| 用户画像（display_name / identity / stage / domain / current_goals / language / timezone / bio / **onboarded**） | `models/context.py:44` `UserProfile` | 源码已实现 |
| 用户偏好（key/value/scope/source） | `models/context.py:86` `UserPreference` | 源码已实现 |
| 上下文预览（"模型此刻会被告知什么"） | `GET/POST /api/v1/context/preview` | 源码已实现 |

**这已经满足任务书第 8 节的大部分要求**（分层记忆、候选→审批→持久化、字节/token 上限、来源与置信度、可查看可删除）。缺的是：记忆的**文件化与 Git 版本回滚**（任务书 8.2 的 Markdown 优先方案），以及界面入口。

### 2.3 Agent 执行与任务闭环——已实现

`backend/app/ai/agent/`：

| 文件 | 行数 | 职责 |
|---|---|---|
| `autonomous.py` | 1619 | 自主执行主循环 |
| `retention.py` | 840 | 数据保留、快照归档、审计归档与恢复 |
| `executor.py` | 647 | 单步工具执行与产物验证（`_verify_deliverables`） |
| `snapshot.py` | 574 | 工作区快照 / diff / 冲突检测 |
| `constraints.py` | 530 | 任务约束推导、偏好解析（`derive_constraints`、`resolve_preferences`） |
| `plan.py` | 298 | 执行计划与状态机 |
| `control.py` | 239 | 取消 / 暂停（`RunController`、`RunRegistry`） |

**状态机**（`ai/agent/plan.py:27` `RunState`）已有 12 个状态：
`PLANNING` `READY` `EXECUTING` `OBSERVING` `VERIFYING` `RECOVERING` `WAITING_CONFIRMATION` `PAUSED` `CANCELLED` `FAILED` `LIMIT_REACHED` `COMPLETED` `INTERRUPTED`

这已经覆盖任务书第 11 节建议的绝大部分状态。`INTERRUPTED` 尤其值得注意——它是为"进程死了，结果未知"专门加的，与 `FAILED`（确定失败）、`CANCELLED`（有人取消）区分开。

**步骤状态**：`StepStatus` = `PENDING` / `RUNNING` / `DONE` / `FAILED` / `SKIPPED`

### 2.4 权限与审批——已实现分级

`backend/app/ai/tools/permissions.py`（918 行）：

```python
class PermissionLevel(IntEnum):
    OBSERVE = 0        # 读文件、搜索、列进程、截屏
    WORKSPACE = 1      # 在工作区建/改文件、跑测试与开发服务器
    SYSTEM = 2         # shell 命令、装依赖、管进程、下载
    FULL_CONTROL = 3   # 广泛系统访问；默认关闭，仅用户显式开启

class AgentMode(StrEnum):
    ASSIST = "assist"          # 只分析建议，从不执行
    SUPERVISED = "supervised"  # 执行；任何有风险的动作先确认
    AUTONOMOUS = "autonomous"  # 在授权范围内独立完成
```

- `RiskLevel` = `LOW` / `MEDIUM` / `HIGH` / `CRITICAL`
- `Decision` = `ALLOW` / `CONFIRM` / `DENY`，由 `PermissionVerdict` 携带理由（理由进审计日志）
- **审批等待**：`ai/tools/confirmation.py` 的 `ConfirmationBroker` + `PendingConfirmation`；接口 `GET /agent/confirmations`、`POST /agent/confirmations/{id}`
- **执行端校验**：`POST /agent/permissions/check` 走的是真实权限服务，不是 UI 判断
- **Agent 自我限制**：`ai/tools/agent_scope.py` 的 `refuse_if_in_agent_scope` —— Agent 不能借工具修改自身权限
- **策略存储**：`ai/tools/policy_store.py`，按用户分文件，含旧格式迁移

任务书第 10 节的 L0–L3 分级，与本项目的 0–3 级在语义上**几乎一一对应**，不需要重写权限引擎。

### 2.5 工具系统——44 个工具，8 类

`backend/app/ai/tools/builtin/`，通过 `registry.py` 的 `build_computer_registry()` 构建。
`ai/tools/executor.py`（743 行）提供：`FailureKind` 失败分类、`ResourceLimits` 资源上限、`_OutputBudget` 输出截断、`_derive_target` 目标推导。
`ai/tools/audit.py` 把每次调用写进 `tool_audit_log`，并对参数做脱敏（`_safe_arguments`）。
`ai/tools/reconcile.py` 处理"进程死了、不知道工具做没做"的对账。

### 2.6 交付物——**后端完整，界面为零**

`api/v1/delivery.py` 19 个操作：

```
POST   /build-runs                              创建一个"要做的事"
GET    /build-runs                              列表
GET    /build-runs/{id}                          含计划
GET    /build-runs/{id}/context                  "它当时被告知了什么"
POST   /build-runs/{id}/execute                  开始做
POST   /build-runs/{id}/pause | /resume | /cancel
POST   /build-runs/{id}/deliverables             登记产出的文件
GET    /deliverables                             产物列表
GET    /deliverables/{id}                        含证据
GET    /deliverables/{id}/versions               版本链
POST   /deliverables/{id}/verify                 重跑验证
POST   /deliverables/{id}/accept | /reject       验收 / 带理由驳回
GET    /deliverables/{id}/download
POST   /deliverables/{id}/open
GET    /delivery/summary                         首页计数
GET    /delivery/states                          状态机（给界面用）
```

`Deliverable` 模型（`models/delivery.py:217`）已有：`path` / `kind` / `title` / `size_bytes` / `version` / `parent_id`（版本链）/ `verified` / `verification`(JSONB) / `accepted` / `feedback` / `decided_at` / `applied_preferences` / `style`。

**这几乎就是任务书第 14 节要求的产物登记表。** 缺口：`desktop-native/awbnative/bridge.py` 里 **44 个 `@Slot` 没有任何一个调用交付接口**，也没有 `DeliverablesPage.qml`。

### 2.7 知识与 RAG

`backend/app/ai/rag/`：`chunker.py` / `embedding.py` / `pipeline.py` / `retriever.py` / `tiers.py`。
支持格式 `.md` `.markdown` `.txt` `.pdf`（单文件上限 25 MB）。
检索分层由 `tiers.py` 表达，`POST /api/v1/retrieval/tiers` 回答"这次答案是从哪一级来的"。
`documents` 接口提供 ingestion 进度、chunk 分页、embedding 计数、reprocess。

### 2.8 Provider

`backend/app/ai/providers/`：`base` / `registry` / `factory` / `deepseek` / `openai_compatible` / `ollama` / `heuristic`(离线) / `budget`(预算) / `guard`(保护)。
`ai-config` 8 个接口：读有效配置、供应商目录、模型列表、切换、**真实连接测试**（`POST /ai-config/test`）、健康（"现在到底是谁在回答，为什么"）、清除覆盖。

### 2.9 工作区快照与回滚

`ai/agent/snapshot.py` 的 `WorkspaceSnapshotManager`：快照、diff（`DiffEntry`）、冲突（`Conflict`）、恢复。
接口：`GET/POST /agent/snapshots`、`GET /agent/snapshots/{id}/diff`、`POST /agent/snapshots/{id}/restore`、`POST /agent/runs/{id}/rollback`。

### 2.10 技能

`api/v1/skills.py` 8 个接口：发现（只读）、导入（必须 `confirmed=true`）、使用记录、复盘打分、退役（停用但保留作为来源证据）。
`ai/skills/` 有 `discovery` / `registry` / `sandbox` / `scanner` / `schema`——**含扫描器与沙箱**，说明导入技能时做了安全检查。

---

## 3. 缺口清单

按"对产品目标的影响"排序。

| # | 缺口 | 影响 | 现状 |
|---|---|---|---|
| G1 | **没有"员工"实体**。无 `employees` 表，无岗位模板，无 persona / mission / responsibilities / permission_profile / knowledge_scope / model_policy | 产品定位的核心。没有它，"私人 AI 员工"就只是一个说法 | 完全没有 |
| G2 | **交付物在界面上不可见**。后端 19 个接口，桥接层 0 个 | 用户看不到"我的员工做出来的东西"。这是任务书里"交付物优先"原则的直接违背 | 后端完成，界面为零 |
| G3 | **记忆在界面上不可见**。后端 20 个接口，桥接层 0 个 | 记忆治理（候选审批、清理、回滚）用户无法操作 | 后端完成，界面为零 |
| G4 | **审批等待在界面上不可见**。`ConfirmationBroker` 与 `/agent/confirmations` 已存在，但界面没有"等待你授权"的入口 | Agent 遇到高风险动作会挂起，用户不知道要去哪批准 | 后端完成，界面为零 |
| G5 | **技能在界面上不可见**。8 个接口，桥接层 0 个 | 技能发现/复盘无法使用 | 后端完成，界面为零 |
| G6 | 记忆没有文件化与 Git 版本回滚 | 任务书 8.2 建议 Markdown + Git；当前只有数据库 | 未实现 |
| G7 | 没有首次启动引导的完整链路 | `UserProfile.onboarded` 字段已存在，但没有引导页 | 部分（字段有，流程无） |
| G8 | 导航没有"员工"相关入口 | 见 §1 的导航结构 | 需改造 |
| G9 | 没有 `THIRD_PARTY_NOTICES.md` | 引入开源后必须有 | 不存在 |

---

## 4. 数据流（实际）

### 4.1 一次自主运行

```
用户给目标
  → POST /api/v1/agent/runs                    创建 run，立即返回
  → AutonomousAgent.run()                      后台执行
      ├─ 记忆装配  memory/assembler.py
      │     ├─ UserProfile + MemoryEntry(status=active) + 知识片段 + 项目摘要
      │     └─ 写 ContextSnapshot（token 预算、是否截断）
      ├─ 规划      plan.py  → ExecutionPlan / PlanStep[]
      ├─ 逐步执行  EXECUTING → 权限检查 → 工具执行 → OBSERVING
      │     ├─ 需要确认 → WAITING_CONFIRMATION → ConfirmationBroker
      │     └─ 每次调用写 tool_audit_log（参数脱敏）
      ├─ 验证      VERIFYING → executor._verify_deliverables
      ├─ 失败      RECOVERING → 有限重试；超限 → FAILED / LIMIT_REACHED
      └─ 完成      COMPLETED → 产物登记 deliverables
  → 进程死亡 → INTERRUPTED → POST /agent/runs/{id}/reconcile 逐条判定
```

### 4.2 一次知识问答

```
文档上传 → POST /documents/upload → 异步 ingestion
  → 解析 → 分块(chunker) → Embedding → 向量(Qdrant) + 稀疏索引
  → 检索(retriever) → 分层(tiers) → 组装上下文 → 生成 → 引用
```

---

## 5. 风险分级

| 级别 | 项 | 说明 |
|---|---|---|
| **高** | 界面与后端能力严重不对齐 | 用户看到的界面**低估**了系统的实际能力。本轮改造的最大价值就在这里 |
| **中** | 34 张表只有 1 个用户（`demo`）在用 | 多用户隔离有代码但**缺少真实的多用户验证**。任务书要求"用独立用户构造测试，证明未经授权无法检索对方知识"，当前测试是否覆盖需核实 |
| **中** | `desktop-native/qml/pages/` 16 个页面里 `AgentWorkbenchPage` 与 `PlannerPage` 职责边界模糊 | 两者都涉及 Agent 工作，需要确认是否有重复 |
| **低** | 根目录 30 余份过程报告与源码并存 | 不影响运行，但会让审查者误把过程记录当事实 |
| **低** | 记忆无 Git 版本 | 有数据库版本（`memory_candidates` 的审批链），但无法回滚 |

---

## 6. 推荐动作

| 项 | 动作 | 理由 |
|---|---|---|
| 记忆体系（`ai/memory/`） | **保留** | 已满足任务书 8.1/8.3 大部分要求，重写只会更差 |
| 权限体系（`ai/tools/permissions.py`） | **保留**，补"员工级"绑定 | L0–L3 与任务书一致，只需把权限从"用户级"扩展到"员工级" |
| 交付物后端（`api/v1/delivery.py`） | **保留**，补界面 | 后端完整 |
| Agent 执行（`ai/agent/`） | **保留** | 状态机、快照、对账都已在 |
| 工具系统（44 个） | **保留** | 有审计、限流、脱敏、对账 |
| RAG | **保留** | 不改 |
| **员工实体** | **新增** | G1 |
| **交付物 / 记忆 / 审批 / 技能 界面** | **新增** | G2–G5 |
| 导航信息架构 | **改造** | G8，按"员工优先"重排 |
| 记忆文件化 + Git | **暂缓** | 任务书 8.2 说"不要机械地把每种数据都改为 Markdown"，当前数据库实现已满足事务/查询/关联需求 |

---

## 7. 无法验证的事项（诚实声明）

1. **全量测试结果**：见 §8，本轮已实际执行。
2. **真实模型调用**：本机没有可用的 API Key，云端 Provider 无法端到端验证；离线引擎（`heuristic.py`）可运行，但它不是真实大模型。
3. **多显示器 / 混合 DPI**：未验证。
4. **代码签名**：缺失。
5. **多用户隔离的端到端验证**：需确认现有测试是否真的构造了两个用户并互相尝试越权。

---

## 8. 基线测试与构建

### 8.1 首次执行结果：**基线不绿**

```
cd backend && python -m pytest tests -q -p no:randomly
→ 4 failed, 1363 passed, 10 skipped in 598.61s (9分58秒)
```

4 个失败全部集中在 `tests/unit/test_release_freeze_versioning.py`，原因是**版本号不一致**：

```
VERSION says 2.3.0, but:
  desktop\src-tauri\tauri.conf.json: '2.2.0'
  desktop\package.json: '2.2.0'
  frontend\package.json: '2.2.0'
  desktop\src-tauri\Cargo.toml: '2.2.0'
  installer\ai-workbench.nsi: '2.2.0'
```

**根因**：仓库里并存两代产物——旧的 Tauri/WebView2 外壳（`desktop/`、`frontend/`）与新的原生端（`desktop-native/`）。上一轮发版把 `VERSION` 从 2.2.0 升到 2.3.0 时，只同步了 `installer/ai-workbench-native.nsi`，**五个旧世代文件被漏掉**，而当时的验收只跑了界面自检与布局审计，没有跑全量测试。

**修复**：项目自带 `scripts/sync_version.py`（`VERSION` 是唯一来源，JSON 与 NSIS 无法读它，所以由脚本写入）。执行后 6 个文件全部一致，该测试文件 16 passed。

**修复后基线**：`4 failed → 0 failed`。这条记为 **P0-1**。

### 8.2 未运行的测试

`10 skipped` 的原因需要逐条确认（见第 21 节的验收报告）；本轮会在最终报告里列出每一条跳过理由。

### 8.3 构建

基线构建（应用 + 安装包）在本轮改造完成后执行，结果见 `docs/release/ACCEPTANCE_REPORT.md`。

---

## 附录：审计用到的命令

```bash
# 表清单
grep -rh "__tablename__" backend/app/models/*.py

# API 清单（走真实 OpenAPI，不是数装饰器）
python -c "from app.main import app; s=app.openapi(); print(len(s['paths']))"

# 桥接层暴露面 vs 后端能力
grep -oP 'bridge\.\K\w+' desktop-native/qml/ -r | sort -u   # 界面调用的
grep -n "    def " desktop-native/awbnative/bridge.py        # 桥接实现的

# 工具分类
python -c "from app.ai.tools.registry import build_computer_registry; ..."
```
