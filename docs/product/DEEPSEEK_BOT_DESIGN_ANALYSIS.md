# DeepSeekBot（DSB）逆向设计报告

面向 AI Workbench 的可借鉴性分析

- 分析对象：`BotHarness/DeepSeekBot`（`deepseekbot.app`）
- 源码：`C:\Users\user\Desktop\_oss_research\DeepSeekBot`（从 `codeload` 取的 tarball，`bot.tar.gz` 718 MB）
- 许可：MIT，Copyright (c) 2026 BotHarness
- 分析日期：2026-10-14
- **证据基础：读的是实际源码**，不是 README。规模如下：

| 项 | 数值 |
|---|---|
| TypeScript 文件 | 910 |
| TypeScript 行数 | 277,567 |
| 测试文件（`packages/*/test/`） | 513 |
| ADR | 187 |
| 文档 markdown | 655（其中中英双语对 48 组） |
| 最大单文件 | `packages/core/src/runtime/bot-runtime.ts` 6,502 行 |

> 关于"是不是 DeepSeek 官方"：不是。它由 BotHarness 开发，**运行在** DeepSeek Harness（DSH）**之上**，是 DSH 的一个插件层。根 `package.json` 的 `name` 是 `botharness`，`private: true`；对外发布物是 npm 包 `deepseekbot` 与一个 DSH profile bundle。

---

## 1. 项目定位与目标用户

### 1.1 一句话定位

**一组有各自身份、人格和记忆的 bots，一起做事。** 官方自述是"开源的 GrokBot 平替"，以插件形式装进 DSH，让用户能在自己已经在用的 IM 里（飞书 / Lark、Slack、Discord、QQ、微信）跟自己的 bot 私聊、拉群、派活。

### 1.2 目标用户

从代码里的痕迹看，用户画像是**已经在用 IM 办公、并且愿意装一个本地 DSH 的个人或小团队**：

- `apps/docs` 有安装引导、`packages/core/src/onboarding/` 有 onboarding 服务 —— 面向非开发者。
- 但安装前提是"你有一台跑着 DSH 的电脑，并且它要保持在线"（README 明说 bot 需要后台持续工作时电脑要开着）。
- 模型用用户自己在 DSH 里配置的服务商与额度 —— **它不提供模型，只提供身份层**。

### 1.3 与 AI Workbench 的关系

**同一个产品命题的另一种形态。** 两边都在做"让 AI 从一次问答变成一段长期关系"，但切入点不同：

| | DeepSeekBot | AI Workbench |
|---|---|---|
| 载体 | 已有的 IM（飞书/Slack/微信…） | 自带桌面窗口（Qt/QML） |
| 交互 | 像同事一样私聊、拉群、派活 | 提需求 → 看执行 → 取成果 |
| 身份单位 | PersonaBot（多 bot 并存，可协作） | Employee（单人多名员工，暂不能协作） |
| 引擎 | **复用 DSH**，自己不写 | 自己的（Agent 引擎 + 工具 + RAG + 记忆） |
| 数据 | 本机（DSH_HOME）+ IM 平台 | 本机（PostgreSQL + 文件） |

---

## 2. 产品结构

### 2.1 三层结构

```
DSH Host（引擎：agent / session / schedule / credentials / storage / sandbox）
   ↑ cordis.patch.yml（插件补丁，声明 dsh.manifestVersion）
BotHarness core（本仓库，210 个 TS 文件 / 7.6 万行）
   ↑ bridge/rpc.ts + methods.ts（4,901 行的 RPC 契约）
Client（React，257 个文件，基于 dsh-client-ui-* 框架）
```

关键点：**它不重写引擎。** `packages/core/package.json` 的 devDependencies 里列着它依赖的 DSH 能力，一项一项都是别人做好的：

```
@deepseek-ai/dsh-agent        @deepseek-ai/dsh-session
@deepseek-ai/dsh-schedule     @deepseek-ai/dsh-scope
@deepseek-ai/dsh-storage-domain   @deepseek-ai/dsh-credentials
@deepseek-ai/dsh-sandbox-policy   @deepseek-ai/dsh-mcp-client
@deepseek-ai/dsh-subagent     @deepseek-ai/dsh-subprocess
@deepseek-ai/dsh-token-meter  @deepseek-ai/dsh-system-prompt
@deepseek-ai/dsh-jobs         @deepseek-ai/dsh-attachment
@deepseek-ai/dsh-shell        @deepseek-ai/dsh-settings
```

**这是这份报告最重要的一条观察。** 上一轮 AI Workbench 的开源审计得出的结论是"缺的不是引擎"；DSB 是同一个结论的另一个证明 —— 它把全部工程量压在**身份层**上，引擎一行不写。

### 2.2 概念层级

`CONTEXT.md`（69 KB）是产品术语的**唯一权威**，`docs/agents/domain.md` 明确要求"产品运行关系放 `docs/architecture/`，术语只在 CONTEXT.md 定义"。核心层级：

```
PersonaBot                     ← 一个有身份、人格、记忆、模型、工作区的 bot
  └── Orchestrator             ← 它面对人的那一层 agent
        └── Assignment Session ← 一条"人可理解的、持续的工作线"
              └── Assignment   ← 真正干活的 DSH agent（不能发消息）
                    └── Subagent  ← 有界子任务
Channel → Source Event → Bot Inbox → Admission → Session
```

定义原文（`CONTEXT.md`，第 112–152 行）值得逐条读，其中三条对 AI Workbench 直接有用：

> **Assignment Session**: A Human-meaningful continuing line of activity that a PersonaBot's Orchestrator chooses to advance independently. It has no separate durable identity or lifecycle; its canonical runtime identity is the DSH Session id of its Assignment Session.

> **Assignment**: The DSH Agent executing inside one Assignment Session. It reports only to its PersonaBot's Orchestrator through that Session, **has no Channel messaging capability**, and is neither a durable identity nor a PersonaBot.
> `_Avoid_`: worker, PersonaBot, Orchestrator, Assignment Session

> **Archiving**: A PersonaBot whose new admissions, wakes, Session execution, and external actions are disabled while its identity, ownership, history, and audit attribution remain intact. Archiving first closes those gates, then stops its Orchestrator, Assignment Sessions, and owned Subagents; **it completes only when that execution tree is quiescent**. Reactivation never resumes old execution automatically.

注意第三条：**归档是一个要等到执行树静止才算完成的过程**，不是把状态字段改一下。这一点 AI Workbench 上一轮刚好踩过（见 §7.5）。

### 2.3 用户旅程

1. 装 DSH → 装 DSB profile bundle（`deepseekbot` CLI）
2. `packages/core/src/onboarding/` 走引导
3. 建 bot：`CreatePersonaBotInput` = `slug / displayName / persona / roles / description / avatar / model / preset / memoryDir / workspaces`（`bots/persona-bot.ts:42`）
4. 把 bot 配对到某个 IM 账号（`messaging/pairing.ts`）
5. 私聊 / 拉群 / 派活；bot 可以定时自己干活（`schedules/bot-schedules.ts`）
6. 长任务由 Orchestrator 拆成 Assignment，各自汇报（`Assignment Report`）

---

## 3. 架构

### 3.1 插件机制

`packages/deepseekbot/package.json`：

```json
"dsh": { "manifestVersion": 1, "bundle": { "patch": "./cordis.patch.yml" } },
"engines": { "dsh": ">=0.2.0-0", "node": ">=22" }
```

基于 **Cordis**（`@deepseek-ai/cordis` 4.0.4）—— 一个插件/依赖注入框架。DSB 通过 `cordis.patch.yml` 往 Host 上打补丁，而不是 fork。**升级 Host 不需要改 DSB**（只要 manifest 版本兼容）。

### 3.2 持久化：声明式 domain，不是手写迁移

`packages/core/src/roster/spec.ts` 全文只有 37 行，是这种风格的样板：

```ts
export const rosterDomainSpec = defineDomain({
  name: 'botharness_roster',
  version: 1,
  layout: 'single',
  global: {
    schema: rosterDomainState,          // zod
    initial: { pins: [], hidden: [], sectionOrder: [], topOrder: [] },
  },
  tables: { sections: domainTable<string, RosterSectionRecord>(rosterSectionRecord) },
});
```

用 **zod** 定义 schema，交给 `@deepseek-ai/dsh-storage-domain` 落库，**domain 自带 version**。对比 AI Workbench：11 个 Alembic 迁移 + SQLAlchemy 模型，是传统路线。两种都可辩护，但 DSB 的做法让"插件声明自己的存储"成为可能（Host 不知道插件存什么）。

另有一处例外：`channels/sqlite-store.ts`（2,557 行）和 `messaging/*` 直接用 Node 的 `DatabaseSync`（同步 SQLite），`permission-groups.ts` 里能看到 `db.prepare('SELECT body FROM messaging_permission_groups ...')` —— **body 整块存 JSON**。也就是说：**结构化查询走 domain，整块读写的对象存 SQLite 的 JSON 列**。

### 3.3 Bot 运行时

| 文件 | 行数 | 作用 |
|---|---|---|
| `runtime/bot-runtime.ts` | 6,502 | bot 的运行时（唤醒、会话、调度、生命周期） |
| `runtime/dsh-bot-agent-adapter.ts` | 3,216 | 把 DSH agent 适配成 Assignment |
| `bridge/methods.ts` | 4,901 | Host↔Client 的 RPC 方法表 |
| `messaging/inbound.ts` / `outbound.ts` | 2,995 / 2,640 | 消息进出的策略层 |
| `database/schema-plan.ts` | 2,152 | 存储规划 |

`bridge/` 只有三个文件（`rpc.ts` / `methods.ts` / `composer-recovery.ts`），却撑起整个前端。`composer-recovery.ts` 这个名字说明了一件 AI Workbench 没做的事：**输入框里没发出去的内容也要能恢复**。

### 3.4 记忆：git 托管的 Markdown 仓库

`packages/core/src/memory/` 21 个文件，这套设计是整个仓库里最值得单独讲的：

- `SOUL.md`（原 `PERSONA.md`，迁移提交信息里带 `ADR-0134`）+ `CORE_MEMORY.md`
- **字符预算**：`DEFAULT_STANDING_LIMITS = { soul: 5000, coreMemory: 3000 }`，`MIN 500 / MAX 50000`，有 `isStandingLimit` 做校验（`memory/soul.ts:12-34`）
- **用量渲染进提示词**（`renderSection`）：

```
## Soul (SOUL.md) [43% — 2,150/5,000 chars]

<正文>
```

- **超限时不静默截断，而是插入一条给模型看的指令**：

```
[Truncated: SOUL.md has 6,200 characters, over its 5,000-character limit.
 Consolidate it below the limit; the full file is still on disk.]
```

- 存储层：`repository.ts` / `git.ts`（`initializeMemoryGit` / `commitMemoryRepositorySeed` / `MemoryCommit`）/ `front-matter.ts` / `jail.ts`（路径牢笼）/ `backup.ts` / `clone.ts` / `recovery.ts` / `search.ts`

**记忆是一个用户能打开、能 diff、能 clone 到别处、能从备份恢复的 git 仓库。** 对比 AI Workbench：记忆在 PostgreSQL 的 `memory_entries` / `memory_candidates` 表里，有候选审批流程，但用户看不到、改不了、导不出。

### 3.5 调度

`packages/core/src/schedules/bot-schedules.ts`（736 行）：

- `BOT_SCHEDULE_ENABLED_LIMIT = 20`、`BOT_SCHEDULE_HISTORY_LIMIT = 20`
- `BotScheduleCreator = 'human' | 'personabot'` —— **bot 可以给自己排计划**
- `BotScheduleFiringState = 'pending' | 'observed' | 'handled' | 'coalesced' | 'skipped'`
- `previewBotScheduleTrigger` / `relativeBotScheduleTrigger` —— 给人看的下次触发预览
- 有类型化的 `BotScheduleError` / `BotScheduleErrorCode`

`coalesced` 是为桌面场景准备的：**电脑关机期间错过的 5 次触发，合并成一次**，而不是开机后补跑 5 遍。

### 3.6 消息与权限

`messaging/` 37 个文件，是一整层策略而不是一堆适配器：

群策略 `group-policy.ts`、线程策略 `thread-policy.ts`、成员准入 `member-admission.ts`、发送者权限 `sender-access.ts`、配对 `pairing.ts` + `pairing-cards.ts`、长度限制 `bounded-delivery.ts`、失败告警 `failure-alerts.ts`、审批 `approval-messaging.ts`、@ 文本 `mention-text.ts`。

权限模型（`permission-groups.ts`）：

```ts
export interface PermissionChoices {
  allow: PairingCapability[];
  deny: PairingCapability[];
  delegateAllow?: PairingCapability[];   // ← 委派是一等公民
  delegateDeny?: PairingCapability[];
}

export interface EffectivePermissions {
  capabilities: PairingCapability[];
  deniedCapabilities: PairingCapability[];
  permissionSources: PermissionSource[];  // ← 告诉你"为什么有"
  ...
}
```

`PermissionSource.kind = 'role' | 'management-pairing' | 'group' | 'person'` —— 四层来源叠加出**有效权限**，而且**有效权限自带来源清单**。每次编辑都带 `expectedRevision`（乐观并发）。

### 3.7 前端

React（257 个文件），但同样是**插件层**：依赖 `dsh-client-ui-conversation` / `dsh-client-ui-dockkit` / `dsh-client-ui-input-trigger` / `dsh-api-gateway` / `dsh-client-store` / `dsh-client-locale`。自己加的视觉件是 `blobatar`（程序化头像）、`@botharness/pixel-avatar` / `pixel-banner` / `pixel-morph`、`morphicons`、`driver.js`（引导）、`html-to-image`（导出图片）。

**bot 的身份是"画出来"的**：`bots/` 目录下有 12 个文件专门做头像与横幅（`avatar-appearance` / `avatar-random` / `avatar-line` / `avatar-pixel-symbols` / `avatar-snapshot` / `bot-banner` / `primary-color` / `bot-icons.mjs`），用 `@resvg/resvg-js` 把 SVG 渲成 PNG。

---

## 4. 工作流设计

### 4.1 单人对话

人在 IM 里私聊 bot → `Source Event` → `Bot Inbox` → `Admission` → Orchestrator Session。bot 以**自己的 IM 身份**回复（`messaging/identity.ts`、`im/identity.ts`）。

### 4.2 多人群组

`group-policy.ts` + `mention-text.ts` + `all-bot-mention.ts`：群里多个 bot 时，谁被 @ 谁答、没被 @ 时是否插话、多个 bot 同时被 @ 怎么分。`thread-policy.ts` 管线程。`member-admission.ts` 决定谁能把 bot 拉进群。

### 4.3 定时任务

`human` 或 `personabot` 创建 → 到点触发 → 产生一次 firing → 状态机推进。错过则 `coalesced`。

### 4.4 长任务（这是设计上最讲究的部分）

**Orchestrator 把长任务拆成 Assignment Session**，用 `Continuity Key` 维持"这是同一条工作线"：

> **Continuity Key**: A stable PersonaBot-local key explicitly assigned to one continuing line of work. It names at most one resumable Assignment Session and may select it only when ownership, Workspace mapping, model, and dependency requirements still match.

双向通信各有明确语义：

- **Assignment Request**（Orchestrator → Assignment）：`context-update` / `next-step` / `next-turn` 三种语义模式，映射到 DSH 的 inject / steer / follow-up，**"without interrupting the current step"**。
- **Assignment Report**（Assignment → Orchestrator）：汇报进展、阻塞、结果、产物引用。**"Full execution history remains in DSH SessionPersistence; each report stays immutable"**。
- **Assignment Ledger**（读模型）：

> 它保持 **execution facts 与 reported outcomes 分离**，从 Session Ownership 和 DSH 事实重建，**queried when needed rather than injected wholesale into every turn**。

### 4.5 审批

聊天里审批（`approval-messaging.ts`）+ **反向请求**（`channels/grant-request.ts`：bot 在聊天里请求授权）+ 容量控制（`core/test/approval-capacity-bridge.test.ts`、`assignment-approval-capacity.test.ts`）。有 `channels/user-questions.ts`（bot 向人提问）和 `native-timed-questions.ts`（带超时的提问）。

### 4.6 展示层的克制

`CONTEXT.md:50` 有一条罕见的规则：

> Active Orchestrator work is shown unless it is waiting on Assignments; **concurrent Assignment tool kinds collapse to one matching effect or generic `working`**, while waiting and blocked attention remain independent indicators rather than configurable priorities.

即：多个 Assignment 在跑时，界面上不并列展示各自的工具名，而是**收敛成一个"正在工作"**；但"在等你"和"被阻塞"必须独立显示。这正是 AI Workbench 任务书 §4/§7 想要的那种取舍，而且它给出了判断标准：**能收敛的是过程，不能收敛的是需要人介入的状态**。

---

## 5. 技术栈

| 层 | 选型 | 证据 |
|---|---|---|
| 语言 | TypeScript 7，ESM | `AGENTS.md` Commands |
| 运行时 | Node ≥22（`.node-version` = v24.21.0） | 同上 |
| 包管理 | pnpm 12.4.2（workspace monorepo） | 同上 |
| 插件框架 | Cordis 4.0.4（`@deepseek-ai/cordis`） | `packages/core/package.json` |
| 宿主 | DeepSeek Harness ≥0.2.0-0 | `packages/deepseekbot/package.json` engines |
| 校验 | zod | `roster/spec.ts`、`permission-groups.ts` |
| 存储 | DSH storage-domain（声明式）+ SQLite（`DatabaseSync`，对象整块存 JSON） | `roster/spec.ts`、`channels/sqlite-store.ts` |
| 测试 | vitest（513 个测试文件） | `AGENTS.md`、`packages/*/test/` |
| Lint / Format | oxlint + oxfmt | `package.json` scripts |
| 构建 | tsdown | 同上 |
| 前端 | React + DSH client UI 包 + blobatar/pixel-* + driver.js | `packages/client/package.json` |
| 图像 | `@resvg/resvg-js`（SVG→PNG）、CanvasKit（OG 卡） | `packages/core/package.json` |
| 音频 | `silk-wasm`（微信 SILK 语音格式） | 同上 |
| 文档站 | Astro（Nimbus UI）+ 生成式同步 | `AGENTS.md` Conventions |
| 许可 | MIT | `LICENSE` |

---

## 6. 工程实践

这个仓库的工程纪律比它的产品设计更值得学。

### 6.1 交付方式：tracer bullets

`AGENTS.md` 的 `Delivery workflow — tracer bullets` 一节规定：

> 对跨层的功能，交付一串 **tracer bullets**：能到达宿主模块、持久权威、适配器/RPC、Client 界面和一条人能测的运行路径的**最小生产形态端到端切片**。
> - 从一个可观察的用户行为出发，不是一个完整的横向层。
> - **第一刀就要用真的权威边界** —— 不引入临时的第二套存储或生命周期。
> - 功能有前后端就要一起给；"只有后端的地基"或"接不上的 UI 假件"都算准备工作，不算完成。
> - 第一刀之后**先要人的反馈**，再铺兄弟视图/变体/边界情况。

AI Workbench 上一轮的经历正好是这个方法论的反面教材：先把整层员工实体建完，最后才发现"Agent 页从不走员工路径"（见 §7.1）。

### 6.2 ADR 是活的，不是墓碑

187 个 ADR，且有 `scripts/check-adr-numbers.mjs` 校验编号。更关键的是这条约定：

> Record durable architecture choices as ADRs under `docs/adr/`, then **integrate their current result into the living architecture instead of leaving decisions in chat or a mutable PRD**.

"living architecture" 是 `docs/architecture/botharness-architecture.md`（内嵌 mermaid），并且**从仓库同步生成文档站**。文档站上区分两件事：

> **Design is normative target state; Reference is current implementation fact.**

### 6.3 变更片段，而不是直接改 CHANGELOG

`changes/` 目录放"变更片段"（当前只剩一个示例），lint 里跑 `pnpm changelog:check` 校验，`fold-change-fragments.mjs` 负责合并。CHANGELOG 是中英双语两份（244 KB / 231 KB）。

### 6.4 AX（Agent Experience）是一等公民

`AGENTS.md` 有专门一节：

> Agent-facing debugging is a first-class concern. 拥有长生命周期或外部资源的插件（容器、适配器、调度器、外部命令）必须为其生命周期转换记录**结构化、有界**的开发者日志 —— 发起者、阶段、耗时、外部命令摘要、拒绝原因 —— 放在人和 agent 都能读的日志面上，**而不是随手的 `console` 输出**。消息保持稳定、机器可解析、绝不含密钥，并且**作为过程证据而不是第二个持久权威**。

配套：`docs/agents/`（issue tracker / triage labels / changelog / domain / pr-merge-risk / pr-ui-visual-evidence / pr-html-explainer / agent-work-trace），`.agents/skills/` 里的第三方 skill 用 `skills-lock.json` 按 hash 锁定。

### 6.5 密钥纪律

> Never commit secrets: the Feishu App Secret goes to the DSH credentials service, never to config or the repo (ADR-0006).

凭据解析顺序写成明文规则：`env > ~/.config/botharness/dev.env > Keychain botharness-deepseek`，并且 `dev-secret.mjs check` **只报告来源、不回显值**。这与 AI Workbench 的 `runtime_config.py` 是同一类思考（密钥不进数据库、不进 dump）。

### 6.6 PR 纪律

- **merge-risk 分类**：每个 PR 都要在一个 `Special things to note` 里说清可逆性、影响半径、评审重点。
- **UI 变更强制前后截图**，且必须验证截图能渲染出来。
- 非平凡的改动要附 HTML explainer，**绑定到确切的 head SHA**，且"本地 HTML 不算评审可见的证据"。
- 跨模块架构改动用 PR Lens 画图，但**不替代** GitHub 渲染的 UI 证据和人的 QA 关卡。

### 6.7 术语纪律

`CONTEXT.md` 是唯一权威，且**给反例**：`Assignment` 词条下写着 `_Avoid_: worker, PersonaBot, Orchestrator, Assignment Session`。187 个 ADR 里有专门的记忆术语 ADR（0002/0003/0004/0012/0014/0021）。

### 6.8 测试与文档规模

- 513 个测试文件 / 910 个源文件 ≈ **0.56**
- 测试按域组织，不是按层：`assignment-collaboration` / `assignment-wait-runtime` / `bot-attention-query` / `avatar-pixel-symbols` …
- 文档 655 篇，48 组中英双语对（英文为主，中文在 `/zh/**`）

---

## 7. 可以直接借鉴到 AI Workbench 的具体点

按"能带来什么"排序。每条给出 DSB 的出处、AI Workbench 的对应位置、以及为什么值得做。

### 7.1 两层 agent 模型：Orchestrator + Assignment（对应任务书 §11）

**DSB 怎么做**：`CONTEXT.md:112-133`。一个 PersonaBot 有一个 Orchestrator（面对人、持有身份），它把工作拆成若干 Assignment Session，每个 Assignment 是 DSH agent，**没有 IM 发消息能力**，只向 Orchestrator 汇报。

**AI Workbench 现状**：`backend/app/ai/employees/templates.py` 的 6 个岗位是**单层**的；一次运行就是一个 `AutonomousAgent`。任务书 §11 的"研究员工分析 → 写作员工整理 → 审查员工检查"没有实现。

**为什么值得做**：单层模型下，"多员工协作"只能靠人在中间传话（用户看完研究员工的产出，再手动派给写作员工）。DSB 证明了两层模型是可行的，而且它划出了关键约束：**干活的那层不能有对外能力**（`has no Channel messaging capability`）。这条约束翻译到 AI Workbench 就是：**Assignment 层的员工不能有 `send_email`、不能写工作区之外**，否则协作会变成权限的横向扩散。

**落地建议**：先只做"一名员工把子任务派给另一名员工"这一条垂直切片（tracer bullet），派出去的子任务复用现有的 `build_runs` + `scope.employee_id` 机制 —— 上一轮已经打通过这条路（`_build_agent` 按员工收窄策略）。

### 7.2 Continuity Key：让"同一条工作线"可恢复

**DSB 怎么做**：`CONTEXT.md:140`。一个 PersonaBot 本地的稳定键，指向**至多一个**可恢复的 Assignment Session，且只有在归属、工作区映射、模型、依赖都还匹配时才允许选中。键的转移必须先让持有者结束或被显式停止。

**AI Workbench 现状**：`build_runs` 每次派活都是一次性的。用户说"继续修改"时，前端只能把标题拼进新目标里（`DeliverablesPage.continueFrom`），**没有"这是同一条工作线"的概念**。

**为什么值得做**：这是"持续合作"与"一次性问答"的分界线。任务书第一部分就要求"不是一次回答，而是长期员工关系"。

**落地建议**：`build_runs` 加一个 `continuity_key`（可空），"继续修改"带上原键。**注意 DSB 的约束条件**：键只有在工作区与模型仍匹配时才复用，否则应当新建 —— 这条比"总是复用"更安全，值得照抄。

### 7.3 执行事实与汇报结论分离，且按需查询

**DSB 怎么做**：`CONTEXT.md:136`。Assignment Ledger "keeps execution facts separate from reported outcomes"，并且 "queried when needed rather than injected wholesale into every turn"。

**AI Workbench 现状**：`bridge.loadRunDetail` 一次性返回 plan + tool_calls + unresolved，页面全量渲染。

**为什么值得做**：这正是任务书 §7「普通模式」要解决的问题的另一面 —— 不是"藏起来"，而是**本来就不该无差别注入**。DSB 把它做成了架构约束而不是 UI 开关。

**落地建议**：`loadRunDetail` 拆成 `loadRunSummary`（状态 + 结果 + 产物）与 `loadRunTrace`（plan + 工具调用）。上一轮已经把技术区块挂到专家模式上了，这一步是让后端也只在该给的时候给。

### 7.4 记忆的字符预算 + 把用量渲染进提示词 + 超限时给模型下指令

**DSB 怎么做**：`memory/soul.ts`。`soul: 5000 / coreMemory: 3000` 字符，界面上可调（500–50000），提示词里直接写：

```
## Soul (SOUL.md) [43% — 2,150/5,000 chars]
```

超限时不是截断了事，而是插入：

```
[Truncated: SOUL.md has 6,200 characters, over its 5,000-character limit.
 Consolidate it below the limit; the full file is still on disk.]
```

**AI Workbench 现状**：`backend/app/ai/memory/` 有候选审批（`MemoryEvaluator`）和上下文装配的 token 预算（`ContextAssembler`，1800 tokens），但**没有"这个员工长期记忆的容量"这个概念**，用户看不到记忆有多满。

**为什么值得做**：任务书 §9.3 要求"不要无限保存，保持精准、有价值"。DSB 给出了一个具体做法，而且它的妙处在于**把压缩的主动权交给模型**：不是系统猜哪些该丢，而是告诉它"你超了，自己压"，并保证原文还在磁盘上（可回退）。这比自动摘要更安全。

**落地建议**：`employees` 表加 `standing_limits`（沿用已有的 `permission_profile` JSONB 风格），`ContextAssembler` 在装配时把百分比写进段落标题；超限时插入同样的"请压缩"指令。注意 AI Workbench 的记忆在数据库里，**没有"原文件还在磁盘上"这个退路**，所以要额外保证超限内容不被丢弃、只是不注入。

### 7.5 归档必须等到执行树静止

**DSB 怎么做**：`CONTEXT.md:14`。归档先关门（禁止新的准入、唤醒、会话执行、外部动作），再停 Orchestrator / Assignment / Subagent，**"it completes only when that execution tree is quiescent"**，且**重新激活不会自动恢复旧执行**。

**AI Workbench 现状**：上一轮刚踩过这个坑（提交 `591fa2e`）。`build_executor._watch` 只看内存里的 `controller.state`，而 agent 宣布成功后**还要再调一次模型生成最终报告**才真正结束。结果：构建运行被标成 COMPLETED 时，`ai_runs.status` 还是 RUNNING、员工还是「工作中」。我加的 `RunController.settled` 是这条原则的**原始版**（只覆盖一次运行，不覆盖"树"）。

**为什么值得做**：一旦按 §7.1 引入子任务，`settled` 就不够了 —— 父任务必须等所有子任务静止。DSB 的说法可以直接作为实现规格：**静止是递归的，且重新激活不恢复**。

### 7.6 有效权限带来源清单

**DSB 怎么做**：`permission-groups.ts` 的 `EffectivePermissions.permissionSources`，每项是 `{ kind: 'role'|'management-pairing'|'group'|'person', id, name, revision }`。四层叠加（role / management-pairing / group / person），每层各有 `allow` / `deny`。

**AI Workbench 现状**：`backend/app/ai/employees/policy.py` 的 `narrow_policy` 只**收窄**（level 取小、mode 朝保守、路径取交/并、上限取小），并且 `describe_narrowing` 只报告**变化**，不报告**来源**。

**为什么值得做**：用户问"这个员工为什么不能写这个目录"时，`describe_narrowing` 只能回答"比你的设置更窄"，答不出是哪一层造成的。来源清单把"审计"从"读代码"变成"读返回值"。这个改动很小（`describe_narrowing` 多返回一个 `sources` 数组），收益是排障时间。

### 7.7 所有编辑带 expectedRevision

**DSB 怎么做**：`permissionGroupInputs` 里每个 `edit-group` / `delete-group` / `person-permissions` 都要求 `expectedRevision: z.number().int().positive()`。

**AI Workbench 现状**：`employees` 表有 `version` 列，但编辑接口没有要求客户端回传它。

**为什么值得做**：员工是长期对象，用户可能在两个地方同时改（比如窗口和未来可能的第二入口）。乐观并发是廉价的正确性。

### 7.8 按员工的模型路由（orchestrator 与 assignment 用不同模型）

**DSB 怎么做**：`models/presets.ts:33` 的 `PersonaBotModelPlan`：`orchestrator: ModelRoute` + `assignmentDefault: ModelRoute` + `assignmentModels?: AssignmentModelOption[]`，带 `revision` 与 `sourcePresetId`（从预设套用，可追溯）。

**AI Workbench 现状**：模型是**全局**的（`.runtime/ai-config.json`），员工没有模型配置。任务书 §11 要求"每个员工不同 Prompt / 工具 / 权限 / 知识库"——模型这一项缺失。

**为什么值得做**：这既是产品能力（研究员工用强模型、格式转换员工用便宜模型），也是成本控制。DSB 把"编排"和"干活"分开配，因为两者对模型的要求本来就不同。

### 7.9 定时触发的 coalesced 语义

**DSB 怎么做**：`BotScheduleFiringState` 含 `coalesced`。桌面场景下电脑关机是常态。

**AI Workbench 现状**：没有员工级定时任务（有 `Planner` 但没有"这个员工每周一早上自己干这件事"）。

**为什么值得做**：如果要做"员工按时做事"，错过触发的处理方式必须**先定好**，否则用户开机时会收到 20 条补跑。DSB 的答案是"合并成一次"，并且把这件事写进状态机而不是藏在重试逻辑里。

### 7.10 术语表带反例

**DSB 怎么做**：`CONTEXT.md` 是唯一权威，词条下写 `_Avoid_: ...`。

**AI Workbench 现状**：`docs/product/PRIVATE_AI_EMPLOYEE_PRD.md` 有术语，但没有反例清单。而实际代码里"员工"和"岗位模板"和"运行"三个词经常混着用。

**为什么值得做**：反例清单是给**下一个改代码的人（或 agent）**看的，成本极低。

---

## 8. 明确不能借鉴 / 不适用的部分

写清楚"不做什么"和"做什么"同样重要。

### 8.1 整套插件架构（Cordis / DSH 插件层）—— 不可移植

DSB 的全部设计都建立在"宿主提供 agent / session / schedule / storage / credentials / sandbox，我只写身份层"这个前提上。AI Workbench 是 Python + Qt，引擎是自己的，**没有这样的宿主**。上一轮 `docs/architecture/adr/0001-open-source-reuse.md` 已经定了路线 C（只移植设计，不引入第二套运行时），这份分析不改变那个结论 —— 反而加强了它：DSB 值得学的是**它的边界划分**，不是它的代码。

### 8.2 跨平台 IM 接入（飞书 / Slack / Discord / QQ / 微信）—— 与产品定位冲突

这是 DSB 的立身之本，但对 AI Workbench 是负资产：

- **数据要出本机**。AI Workbench 的卖点是本地优先（`docs/product/PRIVATE_AI_EMPLOYEE_PRD.md` 的第一原则），接 IM 意味着把对话、文件、记忆的摘要送到第三方平台。
- **需要公网回调**，桌面应用做不到（要么开端口，要么走内网穿透）。
- **凭据复杂度陡增**（DSB 自己的 ADR-0006 专门管飞书 App Secret）。
- **它是"bot 以自己身份在群里说话"，而 AI Workbench 是"我一个人的工作台"**，用户模型不同。

可以借鉴的只有它的**策略层思路**：`group-policy` / `thread-policy` / `member-admission` / `bounded-delivery` 这套"先定规矩再收消息"的结构，将来如果做"多员工协作"，员工之间交接时的准入与长度限制可以照这个形状设计。

### 8.3 git 作为记忆存储 —— 理念对，改造不划算

DSB 用 git 托管 Markdown 记忆，好处是用户可读、可 diff、可 clone、可恢复。AI Workbench 的记忆已经在 PostgreSQL 里，且有**候选审批**流程（`MemoryEvaluator` + `memory_candidates` 表）—— 这是 DSB 没有的能力（它的记忆写入靠 `MemoryWriteError` + git commit）。

把存储从 PG 换成 git 是伤筋动骨，而且要用户装 git。**可借鉴的是它的"可导出、可 diff、可恢复"目标**：给记忆加一个"导出为 Markdown 文件夹"的按钮，成本远低于换存储，能拿到 80% 的好处。

### 8.4 多用户配对权限模型（role / group / person / delegation）—— 需求不存在

DSB 的权限是给**别人**的：群成员、被配对的人、角色、可委派的能力。AI Workbench 的权限是给**用户自己的员工**的，只有一层收窄关系（用户的策略 → 员工的合同），没有"别人"这个维度。引入 `delegateAllow` / `permissionSources` 里的 `group` 概念只会增加没人用的表面。

**只借鉴 §7.6 的来源可追溯**，不借鉴四层结构。

### 8.5 头像 / 像素艺术生成 —— 与设计语言冲突

`blobatar` + `pixel-avatar` + `pixel-banner` + `pixel-morph` + `morphicons` + `@resvg/resvg-js` + CanvasKit。DSB 的 bot 身份靠"画出来的脸"承载（它是个社交产品，bot 要有辨识度）。

AI Workbench 的设计语言是克制的深色工作台（`theme.js` 里 15 个 SVG 图标、无插图、无 emoji 装饰）。给员工生成像素头像会破坏它，而且不解决任何用户问题。**如果**将来要做，用现有 `NavIcon.qml` 的 24 单位网格画一组抽象符号即可，不要引入图像渲染依赖。

### 8.6 `silk-wasm`（微信 SILK 语音）—— 不需要

只为微信语音消息格式存在。AI Workbench 不接微信。

### 8.7 文档站 / 演示文稿 / OG 卡片

`apps/docs`（Astro + Nimbus）、`apps/presentations`（open-slide）、`apps/docs/src/pages/og/**`（CanvasKit 生成社交分享卡）、`og:font` 子集化中文字体。这些是**给一个开源项目做市场**的设施，与 AI Workbench 的规模不匹配。

**可借鉴的是 `docs/` 的组织方式**：`adr` / `agents` / `architecture` / `dev` / `evidence` 五个分区，以及"设计文档与参考文档分离"（Design 是规范目标态，Reference 是当前实现事实）。AI Workbench 现在 `docs/` 下是 `product` / `architecture` / `security` / `release`，缺的是**把"目标态"和"当前事实"分开标注**。

### 8.8 工具链（TypeScript 7 / oxlint / oxfmt / vitest / tsdown / pnpm）

语言与生态不同，没有借鉴意义。**可借鉴的是 `pnpm lint && pnpm format:check && pnpm typecheck && pnpm test` 这条"一个命令跑全部检查"的约定** —— AI Workbench 目前是分开的脚本（ruff、pytest、check_layout、check_qml_pages、verify_native_shell），没有单一入口，上一轮就出现过"跑了测试但没跑 lint"的情况。

### 8.9 具体数值不要照抄

`BOT_SCHEDULE_ENABLED_LIMIT = 20`、`BOT_SCHEDULE_HISTORY_LIMIT = 20`、`soul: 5000` / `coreMemory: 3000` —— 这些是 DSB 在它的场景下定的。借鉴的是**"有这个上限，且用户可以调，且有合法区间校验"**这个形状，数值应当由 AI Workbench 自己的实测决定。

### 8.10 双语文档

DSB 维护 48 组中英双语对（655 篇文档）。AI Workbench 是单语中文产品，双语维护成本翻倍而收益接近零。

---

## 9. 一页结论

**DeepSeekBot 是什么**：MIT 开源、跑在 DeepSeek Harness 上的**身份层插件**。它不写引擎，只解决一个问题 —— 让 AI 从"一个会回答的模型"变成"一组有名字、有记忆、有边界、能一起干活的同事"。

**它最值得学的三件事**（按重要性）：

1. **边界的划法**。它把全部工程量压在身份层，引擎一行不写。这印证了 AI Workbench 上一轮的审计结论（缺的不是引擎），也说明"员工"这一层的价值密度足够高，值得单独做。
2. **长任务的形状**。Orchestrator（面对人、持有身份）+ Assignment（干活、无对外能力）+ Continuity Key（同一条工作线可恢复）+ Ledger（执行事实与汇报结论分离、按需查询）。这是一套完整的、可抄的设计，直接对应 AI Workbench 任务书 §11 的未做项。
3. **让模型自己管自己的记忆**。字符预算 + 把用量渲染进提示词 + 超限时插入"请压缩"指令并保证原文仍在。比自动摘要安全，比硬截断诚实。

**它明确不适用的**：跨平台 IM、插件架构、多用户配对权限、像素头像、文档站。这些占了它代码量的大头，但对一个**本地优先的单人桌面工作台**是负担。

**和 AI Workbench 的根本差异**：DSB 假设"你在别的平台里已经有人要协作"，所以它把精力放在**接入与多人治理**；AI Workbench 假设"你自己一个人、在你自己机器上、要看到东西被做出来"，所以它的核心是**执行可见性与成果交付**。两者的交集只有"身份 + 记忆 + 边界"这一块 —— 而那正是 DSB 做得最深、最值得读的部分。

---

## 附：本次分析读过的关键文件

| 文件 | 读它的原因 |
|---|---|
| `package.json`、`packages/*/package.json` | 技术栈、插件声明、依赖边界 |
| `AGENTS.md` | 工程约定、tracer bullets、AX、密钥纪律、PR 纪律 |
| `CONTEXT.md` | 术语唯一权威；Assignment/Continuity Key/Ledger/归档 的定义 |
| `packages/core/src/bots/persona-bot.ts` | PersonaBot 数据模型与失败原因枚举 |
| `packages/core/src/roster/spec.ts` | 声明式存储 domain 的样板（全文 37 行） |
| `packages/core/src/memory/soul.ts` | 字符预算、用量渲染、超限指令 |
| `packages/core/src/memory/{repository,git,store}.ts` | git 托管记忆的接口 |
| `packages/core/src/messaging/permission-groups.ts` | 有效权限与来源清单 |
| `packages/core/src/schedules/bot-schedules.ts` | 调度上限、firing 状态机 |
| `packages/core/src/models/presets.ts` | 按 bot 的模型路由计划 |
| `packages/core/src/` 目录结构与最大文件清单 | 架构重心分布 |
| `docs/adr/`、`changes/`、`scripts/` | 工程实践证据 |
| `packages/client/package.json`、`packages/client/src` | 前端技术栈与插件化程度 |
