# ADR-0001：不引入第二套运行时，采用选择性设计移植

- 状态：**已接受**
- 日期：2026-10-13
- 决策者：本项目改造负责人
- 相关：`docs/product/OPEN_SOURCE_REUSE_AUDIT.md`、`docs/product/BASELINE_AUDIT.md`

## 背景

任务要求把现有 AI Workbench 从"AI 技术工作台"重新定型为"私人 AI 员工工作台"，并强制要求先审计官方 DeepSeekBot 与 DeepSeek Harness（DSH）的源码，再在三条路线中做出选择。

审计发现：DSH 是一个 TypeScript/Node 的 Agent 运行时（53 个包，MIT，`0.2.1-alpha.2`），DeepSeekBot 是它之上的产品层（1225 个 TS 文件，MIT，React 客户端）。两者**都没有**本项目已有的部分——反而本项目在记忆治理、权限分级、审批等待、工作区快照回滚、运行中断对账上更完整。

## 决策

**保持 Python + FastAPI + PostgreSQL + Qt/QML 技术栈不变**，只从 DeepSeekBot/DSH 移植**设计**：

1. 员工身份模型（对齐任务书 §6.1 的字段）
2. 岗位模板作为数据而非代码
3. 记忆的分区与**注入上限**（借鉴 `soul.ts` 的 `{soul: 5000, coreMemory: 3000}`）
4. 收件箱的"来源事件 → 准入 → 注意力决策"分层
5. 授权从用户级**下沉到员工级**

## 四个权威来源的归属（本 ADR 的核心）

任务书要求"不得留给运行时临时猜测"，明确如下：

| 问题 | 权威来源 | 说明 |
|---|---|---|
| **谁是任务运行时？** | 本项目 `agent_runs` / `ai_runs` / `tool_calls` + `AutonomousAgent` | DSH 的 session 不参与。一次运行只有一条记录 |
| **谁是记忆权威来源？** | 本项目 `memory_entries` / `memory_candidates` | 不引入 Git 记忆仓库，避免第二权威 |
| **谁保管员工权限？** | 本项目 `ai/tools/permissions.py` + `policy_store.py`，本轮增加按员工的覆盖层 | 执行端校验，UI 禁用不构成安全控制 |
| **模型配置放哪里？** | 本项目 `ai-config`（8 个接口）+ Provider 注册表 | 不引入第二个模型配置中心 |

## 被否决的选项

- **路线 A（整体采用 DSH + DeepSeekBot）**：会造成两套数据库、两个模型配置中心、两套任务队列、两个生命周期管理器；安装包需额外携带 Node + Electron；16 个 QML 页面作废。
- **路线 B（DSH 作为 sidecar）**：一次运行的状态会同时存在于 `agent_runs` 与 DSH session，权威来源分裂；且 loopback 仍需令牌保护，集成成本集中在收益之外。

## 后果

**正面**：现有 1363 个通过的测试、34 张表、9 个迁移全部保留；改造是增量的、可回滚的；安装包体积基本不变。

**负面**：放弃 DSH 的 `sandbox-windows-acl`、`subagent` 等成熟实现，需要时得自己写。**这是有意接受的**——现有 `ResourceLimits` + 路径 allowlist 已覆盖当前工具集的需求。

**需要警惕**：不能因为 DeepSeekBot 有某个功能就顺手加进来。任务书 §23 明确把"为了用 DeepSeekBot 而硬塞"列为未完成任务的标志。

## 复核条件

出现以下情况时重新评估本决策：
- 需要真正的多 Agent 协作且现有编排层无法承载
- 需要浏览器/桌面控制（届时 DSH 的 `browser-use` / `computer-use` 值得重新审计）
- 需要 IM 渠道绑定（届时 DeepSeekBot 的 `links` 包值得重新审计）
