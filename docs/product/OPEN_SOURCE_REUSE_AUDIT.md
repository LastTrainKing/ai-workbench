# 开源复用审计：DeepSeekBot 与 DeepSeek Harness

审计时间：2026-10-13
结论：**路线 C —— 选择性移植设计与模块，不引入第二套运行时。**

---

## 1. 获取记录

| 项 | DeepSeek Harness | DeepSeekBot |
|---|---|---|
| 上游 | `https://github.com/deepseek-ai/deepseek-harness` | `https://github.com/BotHarness/DeepSeekBot` |
| 获取方式 | `codeload.github.com/.../tar.gz/refs/heads/master` | `.../refs/heads/main` |
| 获取时间 | 2026-10-13 19:46 GMT+8 | 2026-10-13 19:46 GMT+8 |
| 分支 / 提交 | `master` @ `d743267388641bc76f17c45ce8b4c231aed1d32c`（2026-10-09） | `main` @ `fdb68127192330ba6d948b38183eb14431c007ab`（2026-10-13） |
| 归档大小 | 33,991,317 字节 | 31,178,752 字节 |
| 本地路径 | `Desktop\_oss_research\deepseek-harness` | `Desktop\_oss_research\DeepSeekBot` |
| 许可证 | **MIT**，`Copyright (c) 2026 DeepSeek` | **MIT**，`Copyright (c) 2026 BotHarness` |
| 版本 | `@deepseek-ai/dsh-root` 0.2.1-alpha.2（`private: true`） | CHANGELOG 最新条目为 `[Unreleased]`，需 DSH ≥ 0.2.0 |
| 技术栈 | TypeScript / Node，pnpm@11.28.5 monorepo | TypeScript / Node / React，依赖 DSH |

**关于网络**：本机 `github.com:443` 不可达（curl 超时），`api.github.com` 与 `codeload.github.com` 可达。因此使用 codeload 归档而非 `git clone`——**没有 git 历史**，上面的 commit SHA 来自 GitHub API，用于标注归档对应哪一次提交。

**关于归档完整性**：第一次解压因 Windows 无法创建符号链接（`AGENTS.md` → `AGENTS.canonical.md` 之类）而中断，得到一个**缺 `packages/` 的残缺副本**。重新用容错方式解压后完整（`packages/` 1207 个条目）。**第一版审计结论因此作废**——这是本次审计踩到的最大坑：解压失败时 tar 仍返回 0，需要检查解压结果而不是退出码。

---

## 2. 两个仓库分别是什么

### 2.1 DeepSeek Harness（DSH）—— Agent 运行时

53 个包，`packages/` 下 7075 个文件。按规模：

| 包 | 行数 | 职责 |
|---|---|---|
| `session` | 48,983 | 会话运行时、消息投影、格式版本迁移（v0→v1）、checkpoint 策略 |
| `subagent` | 34,703 | 子代理 |
| `context` | 13,558 | 上下文装配 |
| `sandbox` | 10,513 | 沙箱：`sandbox-local` / `sandbox-policy` / **`sandbox-windows-acl`** |
| `schedule` | 10,149 | 定时 |
| `credentials` | 7,834 | 凭据 |
| `skill` | 7,411 | 技能 |
| `goal` | 5,871 | 目标 |
| `jobs` | 5,549 | 作业 |
| `workflow` | 5,515 | 工作流 |
| `deliverables` | 2,897 | `tool-present`（呈现产物）+ `workspace-changes`（git diff 捕获） |
| `workspace` | 2,808 | 工作区 |
| `plan` | 2,138 | 计划 |
| `guard` | 974 | `repeat-tool-reminder`（重复调用提醒）、`timeout-policy`（超时策略） |
| `identity` | 205 | **只是匿名用户 ID**，不是员工身份 |

还有 `acp`（含 `approval.spec.ts`）、`mcp`、`lsp`、`browser-use`、`computer-use`、`extensions`、`preset`、`bundle`、`host`、`client`。

**关键事实：DSH 没有"员工/Bot 身份"模型。** `packages/identity/` 只有一个 `anonymous-user-id`（205 行）。员工身份是 DeepSeekBot 这一层做的。

### 2.2 DeepSeekBot —— 产品层

1225 个 TS/TSX 文件。`packages/`：

| 包 | TS 文件 | 说明 |
|---|---|---|
| `client` | 458 | 客户端（React） |
| `core` | 449 | 核心：`bots/`、`memory/`、`runtime/` |
| `browser` | 72 | 浏览器控制 |
| `computer` | 46 | 桌面控制 |
| `market` | 19 | 市场 |
| `links` | 19 | 外部链接/身份绑定 |
| `links-cli` | 6 | |
| `ingest` | 2 | |

它是 DSH 的**插件/profile**，不是独立应用。

---

## 3. 能力矩阵与源码状态分级

分级：**A** 源码存在且有针对性测试 · **B** 源码存在但测试不足 · **C** 只有文档/设计 · **D** 稳定发行版已包含且验证 · **E** 当前环境无法验证

| 能力 | DSH 实现位置 | DeepSeekBot 实现位置 | AI Workbench 对应模块 | 状态 | 推荐动作 |
|---|---|---|---|---|---|
| **员工身份模型** | 无（`identity` 只是匿名 ID） | `packages/core/src/bots/*`（`bot-create-cli.ts`、`bot-descriptor-sync.ts`、`avatar-*.ts`） | **无** | DeepSeekBot **B** | **选择性移植设计** |
| **记忆：文件优先 + 字符上限** | 无 | `packages/core/src/memory/soul.ts`：`DEFAULT_STANDING_LIMITS = { soul: 5000, coreMemory: 3000 }`，范围 500–50,000 | `ai/memory/`（DB 实现） | **A** | **保留现有**，借鉴"上限"与"分区渲染" |
| **记忆：Git 版本 / 回滚** | 无 | `memory/git.ts`、`managed-git.ts`、`accepted.ts`、`recovery.ts`、`jail.ts` | 无 | **A** | **暂缓**（见 §5） |
| **收件箱 Inbox** | 无 | `client/src/client/activity-attention.ts`、`human-inbox-scope.ts` | `activity_events` 表 + `runs` 页 | **B** | 借鉴"事件→准入→注意力"分层 |
| **工作区授权 Workspace Grant** | 无 | `client/src/client/workspace-grant-events.ts`、`group-workspace-authorization.ts` | `ai/tools/permissions.py`（用户级） | **B** | **选择性移植**：把授权从用户级下沉到员工级 |
| **分级审批 / 不透明工具必须审批** | `packages/acp/*/tests/approval.spec.ts`、`boot/plugin-manager/src/build-approval.ts` | 工具规则 + 分级审批 | `ai/tools/confirmation.py`（`ConfirmationBroker`） | **A**（本项目已实现） | **保留** |
| **多 Bot 协作 / hop guard** | `packages/subagent`（34.7k 行） | Channel + `MAX_BOT_HOPS` | 无 | **B** | **暂缓**（任务书 §16 要求先做单员工闭环） |
| **Bot 导出 / 导入** | 无 | `core/src/bots/bot-zip-http.ts`、`client/test/bot-zip-ui.test.ts` | 无 | **B** | **暂缓**（P2） |
| **Browser / Computer use** | `packages/browser-use`、`computer-use` | `packages/browser`(72)、`packages/computer`(46)，按 Bot 逐个开启、默认关闭 | 44 个计算机工具（含受控 shell，无浏览器/桌面控制） | **B** | **暂缓**（P3） |
| **沙箱** | `sandbox-local` / `sandbox-policy` / **`sandbox-windows-acl`** | — | `ai/tools/executor.py` 的 `ResourceLimits` + 路径 allowlist | DSH **A** | 借鉴 Windows ACL 沙箱思路 |
| **技能** | `packages/skill`(7.4k) | — | `ai/skills/`（含 `scanner`、`sandbox`） | **A**（本项目已实现） | **保留** |
| **会话运行时** | `packages/session`(49k)，含格式迁移 | — | `agent_runs` / `ai_runs` / `tool_calls` | **A** | **保留** |
| **计划 / 目标 / 待办** | `packages/plan`、`goal`、`todo` | — | `ai/agent/plan.py`（`ExecutionPlan`/`PlanStep`） | **A** | **保留** |
| **定时 / 作业** | `packages/schedule`(10k)、`jobs`(5.5k) | — | `jobs` 表 | **A** | **保留** |
| **交付物** | `packages/deliverables`（`tool-present` + `workspace-changes` 的 git diff 捕获） | — | `api/v1/delivery.py` 19 个接口 + `Deliverable` 模型 | **A** | **保留后端**，补界面 |
| **插件 / 宿主边界** | `boot/plugin-manager`、`extensions`、`preset`、`bundle` | 以插件形式接入 | `ai/skills/` | **A** | **保留** |

**逐项核实过的事实**（不是读 README）：`soul.ts:17` 的默认上限、`memory/` 下 20 个文件的职责、`bots/` 下的 zip 导出实现、`workspace-grant-events.ts` 的存在，均由 `grep` 在本机源码上确认。

---

## 4. 三条路线比较

### 路线 A：整体采用 DSH + DeepSeekBot 作为运行时核心

**做法**：把 AI Workbench 的界面接到 DSH/DeepSeekBot 上，由它们承担 Bot 身份、会话、记忆、任务委派、插件。

**证据**：
- DSH 的 `package.json` 有 `package:desktop` / `dev:desktop` / `start:desktop`——它**自己就有桌面打包路径**（Electron 系）。
- DeepSeekBot 的 `client` 是 React（458 个 TS 文件），`apps/` 下有 `docs`、`presentations`。

**代价**：
1. **两套数据库、两个模型配置中心、两套任务队列、两个生命周期管理器。** 现有 34 张表、9 个 Alembic 迁移、`agent_runs`/`ai_runs`/`tool_calls` 与 DSH 的 session 会各存一份运行记录，而任务书 §19 明确要求"每项能力只有一个权威来源"。
2. **Node 运行时**。安装包要同时带 Python 3.13 + Qt6 + PostgreSQL + Redis + Qdrant（现在 118 MB）**再加** Node + pnpm + Electron 外壳。
3. **界面推倒重来**。现有 16 个 QML 页面全部作废。
4. **产品定位冲突**。DeepSeekBot 的重心是 IM 渠道绑定（Lark / 微信配对）、群组协作、像素头像——这与"本地优先的个人 AI 员工"不是同一个产品。
5. DSH 版本是 `0.2.1-alpha.2`，`private: true`，且 DeepSeekBot 的 CHANGELOG 里大量 "Breaking Changes" 与 "Profile schema Generation 8x"。

**收益**：省下自研运行时的工作量。但本项目**已经有**运行时。

**结论：否。** 这会把一个已完成 80% 的系统换成别人的 30%。

### 路线 B：DSH/DeepSeekBot 作为独立本地 sidecar

**做法**：本机跑一个 DSH 进程，FastAPI 通过 RPC/HTTP 调它。

**代价**：
- 需要定义 RPC 契约、端口分配、启动/关闭顺序、认证、日志汇聚、崩溃恢复、升级、Windows 打包。**这些每一条都是独立的失败模式。**
- **权威来源分裂**：一次运行的状态同时在 `agent_runs` 表和 DSH session 里，谁的算数？任务书 §19 要求必须明确，而这里没有干净的答案。
- 认证与数据一致性：本地 sidecar 仍需要令牌保护（任务书 §17 明确"不能认为 loopback 天然安全"）。
- 体积与启动时间：现有启动已含三个数据库进程，再加 Node 进程会拖慢冷启动（当前崩溃恢复 5 秒、正常启动 3.3 秒，都是实测值）。

**收益**：可以复用 DSH 的 session/subagent/sandbox 实现。

**结论：否。** 收益集中在本项目已经实现的部分（会话、计划、沙箱限制），代价集中在集成与权威来源分裂。

### 路线 C：选择性移植设计与模块 ✅

**做法**：保持 Python + FastAPI + PostgreSQL + Qt/QML 技术栈不变，把 DeepSeekBot/DSH 中**经过验证的设计**在现有栈内实现。

**具体移植什么**（按 P1 优先级）：

| 移植项 | 来源 | 在现有栈内怎么实现 |
|---|---|---|
| **员工身份模型** | DeepSeekBot `core/src/bots/*` 的字段设计 | 新增 `employees` 表 + `role_templates`；字段对齐任务书 §6.1 |
| **岗位合同** | DeepSeekBot 的 Bot 描述符 | 模板即数据（JSONB），不写六套代码 |
| **记忆分区与上限** | `soul.ts` 的 `{soul: 5000, coreMemory: 3000}` 与分区渲染 | `MemoryEntry` 已有 `kind`/`scope`；补"员工身份记忆"分区与**注入上限**（`ContextSnapshot` 已有 `truncated` 字段） |
| **收件箱分层** | `activity-attention.ts` 的"来源事件 → 准入 → 注意力决策" | `activity_events` 表已有；补投影与"待处理"视图 |
| **授权下沉到员工** | `workspace-grant-events.ts` | `permissions.py` 已有 L0–L3；增加按 `employee_id` 的覆盖层，执行端仍走 `checkPermission` |

**不移植**：Git 记忆仓库、多 Bot 协作、浏览器/桌面控制、IM 绑定、插件市场——见 §5。

**结论：采用。**

---

## 5. 明确不采纳的部分与理由

| 不采纳 | 理由 |
|---|---|
| **记忆 Git 仓库**（`memory/git.ts` 等） | 任务书 §8.2 自己说"不要机械地把每种数据都改为 Markdown"；本项目 `memory_entries` + `memory_candidates` 已提供事务、查询、审批链、`ContextSnapshot` 审计。加一层 Git 会引入第二个权威来源。**但借鉴其"字符上限 + 分区渲染"**——这是防上下文膨胀的关键，且 `ContextSnapshot.truncated` 已留好接入点。 |
| **多 Bot 协作 / `MAX_BOT_HOPS`** | 任务书 §16 明确"首先确保单员工闭环可靠"、"默认不多智能体"。现有系统没有多员工协作，先不做。 |
| **Browser / Computer use** | 任务书 §4.2 第 10 条："没有真实浏览器/桌面控制时，不能用 UI 暗示已有这些功能。"现有 44 个工具里没有这两类，**保持没有**。 |
| **IM / Channel 外部身份绑定** | 任务书 §20 P3：需真实用户需求与端到端测试。 |
| **插件市场** | 现有 `ai/skills/` 已有发现/导入/沙箱/复盘，够用。 |
| **React 客户端 / Electron 外壳** | 用户已明确不接受"浏览器壳套后台管理页面"（任务书 §5）。 |

---

## 6. 许可证与来源标注义务

两个仓库均为 **MIT**。MIT 允许使用、复制、修改、合并、发布、分发、再许可和销售，条件是**保留版权声明与许可声明**。

由于本轮决策是**只移植设计思想、不复制代码**，MIT 的再分发条款不直接触发。但仍然履行标注义务，在 `THIRD_PARTY_NOTICES.md` 中记录：

- 项目名、上游地址、审计所用 commit SHA、许可证类型、获取时间
- **明确写出"未复制任何源代码，仅参考设计"**，并逐条列出参考了哪个文件/机制
- 若后续真的复制了代码，必须在该文件中补上文件范围、修改内容与原始版权声明

同时遵守任务书 §3.4：
- 不把 DSH/DeepSeekBot 的代码复制进应用主目录
- 不把它们的 demo、测试数据、开发密钥打进安装包
- 不删除他人版权声明、不把上游代码冒充为原创

---

## 7. 结论

**采用路线 C。** 一句话理由：

> DeepSeekBot/DSH 已经用 Node 把"AI 员工运行时"做了一遍，而本项目已经用 Python 把同一件事做了一遍——且本项目那一遍**更完整**（34 张表、记忆治理、权限分级、审批等待、工作区快照回滚、中断对账都已实现且有测试）。真正值得拿的是 DeepSeekBot 在**产品层**的字段设计（员工身份、岗位合同、记忆分区与上限、收件箱分层、授权下沉），这些是**数据模型与流程**，与技术栈无关，可以在现有系统里低成本实现。

**最大的风险不是技术选型，而是自我认知**：本轮审计发现，本项目缺的从来不是引擎，而是把已有引擎**变成用户能理解和操作的东西**。引入第二套运行时只会让这个差距更大。

---

## 附：审计用到的命令（可复现）

```bash
# 获取（github.com 不可达时走 codeload）
curl -sSL -o harness.tar.gz https://codeload.github.com/deepseek-ai/deepseek-harness/tar.gz/refs/heads/master
curl -sSL -o bot.tar.gz     https://codeload.github.com/BotHarness/DeepSeekBot/tar.gz/refs/heads/main
# 解压必须容错——符号链接在 Windows 上必然失败，且 tar 仍返回 0
tar xzf bot.tar.gz -C bot --exclude='*/.git/*'

# 许可证（逐字读，不凭印象）
head -4 deepseek-harness/LICENSE ; head -4 DeepSeekBot/LICENSE

# 包规模
for d in packages/*/; do echo "$d $(find $d -name '*.ts' | wc -l)"; done

# 关键机制核实
grep -n "DEFAULT_STANDING_LIMITS" DeepSeekBot/packages/core/src/memory/soul.ts
ls DeepSeekBot/packages/core/src/memory/
grep -rln "workspaceGrant" DeepSeekBot/packages/ --include=*.ts
grep -rln "requiresApproval" deepseek-harness/packages/*/*/src/*.ts
```
