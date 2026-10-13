# 第三方来源与许可声明

本文件记录本项目参考或使用的第三方材料。最后更新：2026-10-13。

## 一、参考了设计但**未复制代码**的项目

以下两个项目在 2026-10-13 被完整下载并做源码级审计（见 `docs/product/OPEN_SOURCE_REUSE_AUDIT.md`）。**本轮改造没有复制其中任何一行源代码**，只参考了数据模型与流程设计。为遵守 MIT 的署名精神，仍在此逐条列出参考了哪个机制、对应哪个文件。

### 1. DeepSeek Harness

- 上游：`https://github.com/deepseek-ai/deepseek-harness`
- 审计提交：`d743267388641bc76f17c45ce8b4c231aed1d32c`（master，2026-10-09）
- 许可证：MIT，`Copyright (c) 2026 DeepSeek`（原文见本地归档 `_oss_research/deepseek-harness/LICENSE`）
- 参考的机制：
  - `packages/sandbox/sandbox-windows-acl/` —— Windows ACL 沙箱的分级思路（**未实现**，仅在 ADR 中记录为可选项）
  - `packages/guard/timeout-policy/` —— 超时策略的分层（本项目已有 `ResourceLimits`，未改动）
  - `packages/acp/*/tests/approval.spec.ts` —— 审批接缝的测试组织方式

### 2. DeepSeekBot

- 上游：`https://github.com/BotHarness/DeepSeekBot`
- 审计提交：`fdb68127192330ba6d948b38183eb14431c007ab`（main，2026-10-13）
- 许可证：MIT，`Copyright (c) 2026 BotHarness`（原文见本地归档 `_oss_research/DeepSeekBot/LICENSE`）
- 参考的机制：
  - `packages/core/src/memory/soul.ts` —— 常驻记忆的字符上限（`soul: 5000` / `coreMemory: 3000`，可调范围 500–50,000）与分区渲染。本项目的 `ai/memory/assembler.py` 参考了"分区 + 上限"这一做法
  - `packages/core/src/bots/*` —— 员工（Bot）身份字段的划分方式，用于设计 `employees` 表的字段
  - `packages/client/src/client/activity-attention.ts` —— 收件箱的"来源事件 → 准入 → 注意力决策"分层
  - `packages/client/src/client/workspace-grant-events.ts` —— 授权事件化
- **未采用**：其记忆 Git 仓库、多 Bot 协作、浏览器/桌面控制、IM 绑定、插件市场。理由见复用审计 §5

## 二、本项目实际依赖的第三方组件

| 组件 | 用途 | 许可证 |
|---|---|---|
| FastAPI | HTTP 框架 | MIT |
| SQLAlchemy 2.x | ORM | MIT |
| Alembic | 数据库迁移 | MIT |
| Pydantic | 数据校验 | MIT |
| PySide6 / Qt 6 | 桌面界面 | LGPLv3（动态链接，未修改 Qt 源码） |
| PostgreSQL 16 | 数据库 | PostgreSQL License |
| Redis 5 | 缓存 | BSD-3-Clause |
| Qdrant 1.12 | 向量库 | Apache-2.0 |
| python-docx | 文档生成（论文初稿用，非产品运行时） | MIT |
| pytest | 测试 | MIT |
| Ruff | 静态检查 | MIT |

完整的依赖版本以 `backend/pyproject.toml`、`desktop-native/requirements*.txt`、`frontend/package.json` 为准。

## 三、运行时二进制

`vendor/runtime/` 下的 PostgreSQL / Redis / Qdrant 由 `scripts/fetch-runtime.ps1` 从官方地址下载，脚本内含固定版本号与校验。这些二进制**不进入源码交付包**，构建前需先运行该脚本。

## 四、本文件未包含的内容

- **未包含任何 DeepSeekBot / DSH 的源代码**。若将来引入，必须在此补充：文件范围、修改内容、原始版权声明的保留位置。
- 未包含 Qt 的静态链接或修改版本。当前为动态链接，符合 LGPLv3 要求。
