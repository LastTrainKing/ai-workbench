# 运行结果契约（§8.1）

> 任务书 §8.1 要求：统一结果对象必须有**字段来源、可空规则、序列化契约和前后端类型**，
> 且不得为了统一而重复保存同一事实。

**实现**：`backend/app/services/run_outcome.py::build_run_outcome`
**消费方**：`desktop-native/qml/pages/AgentWorkbenchPage.qml`（成果区）、
`desktop-native/qml/components/DeliverableCard.qml`
**入口**：`ServiceBridge.loadRunOutcome(request_id, build_run_id)`

## 一、它是什么，不是什么

**是**：读侧投影。每次调用时从 `BuildRun`、`Deliverable` 和文件系统现算。

**不是**：第二个状态权威。没有任何字段被持久化，也没有任何字段来自"上一次算出来的
结果"。凡是数据库里已经有的，这里都只是换个形状呈现。

这条限制是有理由的。这个项目已经付过一次代价：`ai_runs.status` 与 `build_runs.status`
各说各话，界面上"已完成"和"执行中"同时成立。再引入一份可写的运行结果表，就是再给
"同一件事有两个答案"一次机会。

## 二、字段

| 字段 | 类型 | 可空 | 来源 | 说明 |
|---|---|---|---|---|
| `build_run_id` | `string` (uuid) | 否 | `BuildRun.id` | |
| `status` | `string` | 否 | `BuildRun.status` | `BuildStatus` 的枚举值。**中文文案不在这里** —— 它在 `qml/theme.js::buildWord`，因为那是界面层的事，且已有断言钉住 |
| `goal` | `string` | 否（可为空串） | `BuildRun.goal` | 用户的原始那句话。「重新生成」重跑的是它，不是标题 |
| `summary` | `string` | 否 | 见下 | |
| `artifacts[]` | `Artifact[]` | 否（可为 `[]`） | `Deliverable` 行 | |
| `verification[]` | `Verification[]` | 否（可为 `[]`） | `Deliverable.verification` | 与 `artifacts[]` **按 id 对齐**，不按顺序 |
| `next_actions[]` | `Action[]` | 否（可为 `[]`） | 计算得出 | **运行级**动作 |
| `diagnostic_ref.build_run_id` | `string` | 否 | `BuildRun.id` | |
| `diagnostic_ref.ai_run_id` | `string \| null` | **是** | `BuildRun.ai_run_id` | 没有关联运行（手工登记的交付物）时为 `null`。指向专家日志，而不是把日志塞进结果 |

### `summary` 的取法

优先级从高到低，**第一条命中即止**：

1. 没有产物 → `BuildRun.result_summary`，为空则 `"这次运行没有产出文件。"`
2. 有产物不在磁盘上 → `"产出 N 个文件，其中 M 个已经不在了：<标题列表>。"`
3. 其余 → `BuildRun.result_summary`，为空则 `"产出 N 个文件，都已落盘。"`

第 2 条**覆盖** `result_summary`。这是有意的：运行当时写下的摘要不可能知道文件后来
被删了，而"文件不在了"是用户此刻最需要知道的事。

### `Artifact`

| 字段 | 类型 | 可空 | 来源 |
|---|---|---|---|
| `id` | `string` | 否 | `Deliverable.id` |
| `title` | `string` | 否 | `Deliverable.title`，空则回退 `path` |
| `kind` | `string` | 否 | `Deliverable.kind` |
| `path` | `string` | 否 | `Deliverable.path` |
| `size_bytes` | `int` | 否 | `Deliverable.size_bytes`（**登记时**的大小） |
| `version` | `int` | 否 | `Deliverable.version` |
| `verified` | `bool` | 否 | `Deliverable.verified`（机器判定） |
| `accepted` | `bool \| null` | **是** | `Deliverable.accepted`。`null` = 用户还没表态，**不等于"否"** |
| `feedback` | `string` | 否 | `Deliverable.feedback` |
| `file_state` | `FileState` | 否 | 现算，见下 |
| `actions[]` | `Action[]` | 否 | 计算得出，**产物级**动作 |

### `FileState`

| 字段 | 类型 | 可空 | 来源 |
|---|---|---|---|
| `exists` | `bool` | 否 | 每次调用时 `stat()` |
| `size_bytes` | `int` | 否 | 同上；不存在时为 `0` |
| `reason` | `string` | 否 | 不存在时是一句人话（`"文件不在了：FileNotFoundError"`），存在时是空串 |

**`size_bytes` 在 `exists=false` 时是 `0`，而界面不得把它画出来。**
`DeliverableCard.qml` 在文件缺失时省略大小 —— "0 B" 会被读成"文件是空的"，那是另一件
更糟的事。这条由投影层断言 `文件不在时不报大小` 守住。

### `Action`

| 字段 | 类型 | 可空 | 说明 |
|---|---|---|---|
| `id` | `string` | 否 | 见下表。**界面按 id 分派，不按 label 匹配** |
| `label` | `string` | 否 | 按钮文案 |
| `enabled` | `bool` | 否 | 此刻能不能按 |
| `reason` | `string` | 否 | 不可用时的原因；可用时是空串 |

产物级动作：

| id | 可用条件 |
|---|---|
| `preview` | 文件存在 **且** `kind ∈ TEXT_PREVIEWABLE_KINDS` |
| `open` | 文件存在 |
| `reveal` | 文件存在 |
| `continue` | 总是 |
| `regenerate` | `goal` 非空 |

运行级动作（`next_actions`）：

| id | 可用条件 |
|---|---|
| `verify` | 至少一个产物 |
| `open_deliverables` | 总是 |

**两组动作的 label 不得重合**，由断言 `运行级与产物级动作不重名` 守住 ——
同一个按钮出现在两处迟早会漂移。

**「导出」故意不存在。** 这个构建没有文档转换器，做一个转不了格式的按钮就是做一个
假功能。它出现在任务书 §8.2 的候选列表里，但列表里还有一句"只显示当前产物类型和
平台实际支持的操作"。

## 三、序列化

* 传输：桥接层 `_submit` → `QJsonValue`（QVariant）。所有值都是 JSON 原生类型。
* `uuid` → `str`；`datetime` 不出现（本契约不含时间字段 —— 需要时间的界面读
  `BuildRun.as_dict()`，那里有 `started_at` / `finished_at`）。
* 缺省值一律显式给出，不使用 `undefined`：QML 里 `undefined` 与 `null` 在 `??`
  下的行为不同，而契约的价值之一就是让界面不必猜。
* 未知字段：消费方忽略。新增字段是向后兼容的，删除字段不是。

## 四、两侧各由什么测试守住

| 侧 | 测试 | 断言什么 |
|---|---|---|
| 后端形状 | `backend/tests/integration/test_run_outcome.py` | 真实数据库、真实文件、真实删除；动作随磁盘变化；owner 隔离 |
| 界面投影 | `desktop-native/verify_projection.py`（`delivery` 组，20 条） | 注入同形数据，断言**画出来的内容**：标题、中文类型名、验证摘要、按钮可用性、不泄漏内部字段 |

分开是必要的。只做后端测试会漏掉"数据对、画错了"（这个项目出过两次）；
只做界面测试会漏掉"画得对、数据是假的"。
