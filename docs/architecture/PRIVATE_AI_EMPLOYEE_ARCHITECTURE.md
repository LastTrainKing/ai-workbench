# 私人 AI 员工工作台：架构

版本 2.4.0 · 2026-10-13 · 与代码同步；每个组件后面是它的真实文件路径

---

## 1. 组件职责

```
┌─────────────────────────────────────────────────────────────────────────┐
│ 桌面端  desktop-native/                                                 │
│   Main.qml            窗口、导航栏、页面装载、toast                       │
│   qml/pages/*.qml     20 个页面（19 个有导航入口 + 1 个详情页）            │
│   qml/components/*    12 个组件（Button/Card/StateBlock/StatusPill…）     │
│   qml/theme.js        配色、间距、导航模型、状态文案（唯一来源）           │
│   awbnative/bridge.py 71 个 @Slot：QML 到业务层的唯一通道                 │
│   awbnative/async_runner.py   后台线程 + 事件循环，UI 线程不阻塞          │
│   awbnative/runtime.py        拉起/回收 PostgreSQL、Redis、Qdrant 子进程  │
│   awbnative/window_frame.py   DWM 深色标题栏                            │
│   awbnative/bootstrap.py      alembic upgrade head + 就绪检查             │
└───────────────────────────────┬─────────────────────────────────────────┘
                                │ 同进程 import，不走 HTTP
┌───────────────────────────────▼─────────────────────────────────────────┐
│ 后端  backend/app/                                                       │
│   api/v1/*.py          130 路径 / 165 操作（OpenAPI 实测）               │
│   services/*.py        13 个服务：业务编排，只调仓储                      │
│   repositories/*.py    5 个仓储：唯一写 SQL 的地方                        │
│   models/*.py          35 张表                                          │
│   ai/agent/            执行循环、计划、状态机、快照、保留策略             │
│   ai/memory/           记忆装配、评估、清理、生命周期                     │
│   ai/rag/              分块、向量、检索、分层                             │
│   ai/tools/            工具注册、权限、审批、审计、对账                   │
│   ai/employees/        ★ 本轮新增：岗位模板、人格护栏、权限收敛           │
│   ai/providers/        Provider 抽象 + 离线引擎 + 预算 + 保护             │
│   ai/skills/           技能发现、扫描、沙箱、注册                         │
│   core/                配置、错误、日志、schema 守卫                      │
│   infrastructure/      数据库、缓存、向量库、LLM 客户端                   │
└───────────────────────────────┬─────────────────────────────────────────┘
                                │
        ┌───────────────────────┼───────────────────────┐
        ▼                       ▼                       ▼
   PostgreSQL 16            Redis 5                Qdrant 1.12
   （业务数据）            （缓存/队列）            （向量索引）
```

**分层规则**（`repositories/base.py` 的模块文档里写着，代码遵守）：仓储是唯一写 SQL 的地方；服务编排仓储；API 层只跟服务说话；Agent 只跟工具→服务说话。

---

## 2. 进程模型与数据目录

窗口是**一个**原生 Qt 进程。启动时 `runtime.ensure()` 以子进程方式拉起三个引擎；退出时**只回收本进程启动的那套**——别人启动的栈停掉会像数据丢失（`bridge.stopEnvironment` 的文档说明了这一点）。

数据目录由 `AI_WORKBENCH_DATA_DIR` 决定，默认 `%LOCALAPPDATA%\AI Workbench Data\.runtime\`：

```
.runtime/
  pgdata/          PostgreSQL 集群
  redis/           缓存
  qdrant/          向量库
  ai-config/       Provider 配置（含 API Key，不进 Git）
  storage/         上传的文档
  snapshots/       工作区快照
  logs/            启动日志
```

**开发检出用 `<repo>/.runtime/`，安装版用 `%LOCALAPPDATA%`——两者是不同的数据库。** 这一点在开发时很容易搞混：`scripts/infra-start.ps1` 用前者，安装版启动用后者。

---

## 3. 员工层（本轮新增）

```
ai/employees/
  templates.py      6 个岗位模板（纯数据，无子类）
  persona_guard.py  人格文本检查（纯函数）
  policy.py         员工权限 = 用户权限的收敛（纯函数）
services/employee_service.py   持久化与编排
repositories/employee_repo.py  唯一写 employees SQL 的地方
api/v1/employees.py            9 个操作
models/employee.py             employees 表，21 列
```

### 3.1 为什么是表

产品里其他东西都是实体：项目、任务、记忆、交付物各有一行。「员工」是唯一一个只作为文本存在的东西——每次请求拼一段 system prompt。文本不能被授予权限、不能拥有交付物、不能被暂停，也不能在运行记录里和另一个员工区分开。

### 3.2 四个字段组

| 组 | 字段 | 性质 |
|---|---|---|
| 是谁 | `name` `role_id` `avatar` `mission` `persona` | 展示 |
| 能碰什么 | `knowledge_scope` `memory_scope` `permission_profile` | **强制**——权限服务在工具运行前读取 |
| 能用什么 | `skills` `model_policy` | 对着真实注册表解析 |
| 在哪 | `status` `version` `created_from_template` `archived` | 生命周期 |

`persona` 刻意不是自由发挥：它塑造语气，系统提示、权限策略和审计日志在它之外，从它那里够不到。

### 3.3 权限收敛（`policy.py`）

**唯一规则：员工只能收窄用户策略，永远不能放宽。**

| 字段 | 收敛方向 |
|---|---|
| `level` | `min`——数字更小，机器更少 |
| `mode` | 朝 `ASSIST`——更保守的赢 |
| `allowed_paths` | **交集**——员工不能新增可写区域 |
| `denied_paths` | 并集——只能加禁止，不能解除 |
| `allowed_domains` / `blocked_domains` | 交集 / 并集 |
| `max_*` 上限 | `min`——员工只能要得更少 |

上限那一行是最容易写错的：员工配置里的 `max_tool_calls = 10_000` 看起来无害，实际是让一行存储数据去抬高用户自己的预算。取最小值让这个字段只能读作「这个员工最多这么多」。

**没有一条分支产生比用户原有更宽松的结果**，所以一个由模板、导入或手改 JSON 创建的员工都越不过它的所有者。这条性质由 `test_employee_layer.py` 对枚举字段做穷举验证。

### 3.4 人格护栏（`persona_guard.py`）

它**不拦截**，只报告。理由：人格本来就够不到权限系统——权限在 `ai/tools/permissions.py`，工具执行前读数据库，不读提示词。这段文本改变不了任何授权。

那它做什么？两件仍然真实的事：

1. 一段告诉员工「你有完整权限」的人格，会产生一个**和用户争论**的员工——它拒绝请求授权、解释说自己已被授权，把用户的时间浪费在一个它从来没有的权限上。写的时候发现，好过运行时困惑。
2. 人格**每次请求都会被注入**。「打印你的系统提示词」这类文本是一个有永久住所的提示注入载荷。

所以 API 返回发现项，用户决定。这和这个产品里其他有风险的写操作是同一个形状。

### 3.5 岗位模板是数据

六个模板是六个 dict，不是六个子类。新增第七个岗位 = 新增第七个 dict。每个模板声明十一件事（任务书 §6.3），其中最容易省掉的几件恰恰最重要：`out_of_scope`（「不在列表里」和「明确不归我」是两回事）、`needs_approval`、`definition_of_done`。

**工具白名单在测试里对着真实注册表校验**（`build_default_registry()` ∪ 计算机工具），一个被重命名或删除的工具会让测试失败，而不是悄悄授予空集。

---

## 4. 数据流

### 4.1 一次自主运行

```
用户给目标
 → POST /build-runs                      创建，立即返回（"已创建，尚未开始"）
 → POST /build-runs/{id}/execute         或桥接的 startBuildRun（创建+开始一次调用）
 → DeliveryService.start_build_run
     ├─ attach_context()                 装配并记录"这次运行被告知了什么"
     └─ execute_build_run()              后台
 → AutonomousAgent.run()
     ├─ 记忆装配  memory/assembler.py
     │    └─ 写 ContextSnapshot（profile_digest / memory_ids / token_estimate / truncated）
     ├─ 规划      plan.py → ExecutionPlan / PlanStep[]
     ├─ 逐步执行  EXECUTING → 权限检查 → 工具执行 → OBSERVING
     │    ├─ 需要确认 → WAITING_CONFIRMATION → ConfirmationBroker（用户回答后继续）
     │    └─ 每次调用写 tool_audit_log（参数脱敏）
     ├─ 验证      VERIFYING → executor._verify_deliverables
     ├─ 失败      RECOVERING → 有限重试；超限 → FAILED / LIMIT_REACHED
     └─ 完成      COMPLETED → 登记 deliverables（路径/大小/版本/验证）
 → 进程死亡 → INTERRUPTED → POST /agent/runs/{id}/reconcile 逐条判定工具副作用
```

### 4.2 一次带引用的问答

```
上传 → POST /documents/upload → 异步 ingestion
  → 解析 → 分块 → Embedding → 向量(Qdrant) + 稀疏索引
  → 检索(retriever) → 分层(tiers) → 组装上下文 → 生成 → 引用
  → POST /retrieval/tiers 回答"这次是从哪一级拿到的答案"
```

### 4.3 一条记忆的生命

```
对话/资料
 → 提取候选（memory/evaluator.py）
 → MemoryCandidate(status=pending)     ← 不生效
 → 用户批准 / 拒绝（"记忆"页 或 POST /memory-candidates/{id}/approve）
 → MemoryEntry(status=active)          ← 生效，进上下文装配
 → 清理：plan_cleanup() 先出计划（写零）→ 用户确认 → apply
```

---

## 5. 状态机

`RunState`（`ai/agent/plan.py`）12 个值，`terminal` 与 `active` 是计算属性：

```
PLANNING → READY → EXECUTING ⇄ OBSERVING → VERIFYING → COMPLETED
                        ↓           ↓
                   RECOVERING   WAITING_CONFIRMATION
                        ↓           ↓
                     FAILED      （用户回答后回到 EXECUTING）
    任意时刻：PAUSED / CANCELLED / LIMIT_REACHED
    进程死亡：INTERRUPTED（不是 FAILED——没有东西判定它失败；
                          也不是 CANCELLED——没有人取消它）
```

`INTERRUPTED` 是为「结果真的未知」专门加的：`RunStatus` 与 `RunState` 曾经对同一次被中断的运行给出不同答案（行说 FAILED，状态说 EXECUTING）。

`StepStatus` = `PENDING` / `RUNNING` / `DONE` / `FAILED` / `SKIPPED`。

---

## 6. 权威来源（任务书要求明确，不得留给运行时猜测）

| 问题 | 权威来源 | 谁不参与 |
|---|---|---|
| 任务运行时 | `agent_runs` / `ai_runs` / `tool_calls` + `AutonomousAgent` | DSH 的 session |
| 记忆 | `memory_entries` / `memory_candidates` | 无 Git 记忆仓库 |
| 员工权限 | `ai/tools/permissions.py` + `policy_store.py` + `employees.permission_profile`（收敛层） | 界面禁用态 |
| 模型配置 | `ai-config` 8 个接口 + Provider 注册表 | 无第二个配置中心 |
| 交付物 | `deliverables` 表 | 无第二个产物登记 |
| 审计 | `tool_audit_log` | Agent 不能改或删 |

---

## 7. 故障语义

| 故障 | 行为 |
|---|---|
| 引擎没起来 | 窗口先显示，`bridge._after_environment` 等最多 240 秒；页面不会在数据库就绪前放弃 |
| 进程被杀 | 下次启动走崩溃恢复；`INTERRUPTED` 的运行可由用户对账 |
| 工具副作用未知 | `tool_calls` 记 `working_directory`（在意图时刻记录——那是答案唯一存在的时刻），`POST /agent/runs/{id}/reconcile` 逐条判定 |
| 权限不足 | `Decision.DENY` + 理由进审计；界面禁用态**不是**安全控制 |
| 用户不在 | 高风险调用停在 `WAITING_CONFIRMATION`，「待审批」页显示原因、影响与目标 |
| 运行改坏了工作区 | 每次运行前有快照；`POST /agent/runs/{id}/rollback` 恢复 |
| 上下文超预算 | `ContextSnapshot.truncated = true`，装配器按块裁剪 |

---

## 8. 已知架构债

1. **两代产物并存**：旧 Tauri/WebView2 外壳（`desktop/`、`frontend/`、`installer/ai-workbench.nsi`）仍在仓库里，被版本一致性测试覆盖，但不参与构建。删掉它们需要一个独立的清理决定。
2. **19 个导航入口**在 800px 高度下需要滚动。
3. **首次启动引导**没有串成流程（见 PRD §5）。
4. **桥接层 71 个槽**，`_submit` 的超时默认 120 秒，长操作靠逐处传 `timeout=`——容易漏。
