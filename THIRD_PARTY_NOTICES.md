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
| PostgreSQL 16.4 | 数据库 | PostgreSQL License |
| Redis 5.0.14.1 | 缓存（Windows 移植版） | BSD-3-Clause |
| Qdrant 1.12.4 | 向量库 | Apache-2.0 |
| python-docx | 文档生成（论文初稿用，非产品运行时） | MIT |
| pytest | 测试 | MIT |
| Ruff | 静态检查 | MIT |

完整的依赖版本以 `backend/pyproject.toml`、`desktop-native/requirements*.txt`、
`frontend/package.json` 为准。

### 关于 Qt 的 LGPLv3

PySide6 以**动态链接**方式使用，未修改 Qt 源码，未做静态链接。LGPLv3 的要求因此是：
保留版权声明（本节）、允许用户替换 Qt 库（安装目录下的 `.dll` 是独立文件，可替换）、
提供许可证全文。若将来改为静态链接或修改 Qt 源码，本节的结论即失效，必须重新评估。

## 三、随 Windows 安装器分发的二进制

**这一节回答的是"用户下载的安装包里有什么别人的东西"**，而不是"开发者装了哪些包"。

`AI-Workbench-Native-Setup.exe`（119 MB）内含：

| 组件 | 版本 | 许可证 | 来源 |
|---|---|---|---|
| Python 运行时 | 3.13（PyInstaller 打包） | PSF-2.0 | python.org |
| Qt 6 / PySide6 | 6.12.0 | LGPLv3 | qt.io / pypi |
| PostgreSQL | 16.4 | PostgreSQL License（≈MIT） | EnterpriseDB 官方二进制 |
| Redis（Windows 移植） | 5.0.14.1 | BSD-3-Clause | tporadowski/redis |
| Qdrant | 1.12.4 | Apache-2.0 | qdrant/qdrant 官方 release |
| 应用依赖（FastAPI / SQLAlchemy / …） | 见 `backend/requirements.txt` | MIT / BSD / Apache-2.0 | PyPI |

三者由 `scripts/fetch-runtime.ps1` 从**官方地址**下载。**版本被钉在 URL 里**
（`postgresql-16.4-1-windows-x64-binaries.zip`、`Redis-x64-5.0.14.1.zip`、
`qdrant-x86_64-pc-windows-msvc.zip` 来自 `v1.12.4`），所以"拿到的是哪个版本"是可确定的。

> **诚实说明：目前没有做校验和验证。** `fetch-runtime.ps1` 与 `lib.ps1` 里都没有
> `Get-FileHash` 调用 —— 这一点在本轮核对时才发现，此前 `THIRD_PARTY_NOTICES.md`
> 曾声称"脚本内含固定版本号与校验"，那句话是错的，已删。
>
> 影响：下载走 HTTPS 到官方域名，但一次被劫持的 TLS 或一次上游替换无法被检测到。
> 缓解：二进制由 NSIS 打包进安装包后，安装包自身的 SHA-256 会公布（见
> `docs/release/BUILD_AND_INSTALL.md`），所以**发布者与用户之间**的完整性是可验证的；
> 不可验证的是"发布者拿到的上游文件是否正确"。这是一个已知的供应链缺口，已列入
> `RELEASE_NOTES.md` 的已知问题。

二进制**不进 Git 历史**（见 `.gitignore` 的 `vendor/*.zip`）—— 安装包作为 GitHub
Release 资产分发。

**每一项的许可证都允许再分发**，且都不要求本项目改变自身许可证：
PostgreSQL License 与 MIT 同族；Redis 5 是 BSD-3-Clause（注意：Redis 7.4 起改为
RSALv2/SSPL 双许可，**本项目捆绑的是 5.0.14.1，不是 7.x**，所以不受影响 ——
`docker-compose.yml` 里的 `redis:7-alpine` 只用于本地开发，不随安装包分发）；
Qdrant 是 Apache-2.0。

**未随包分发的**：`vendor/runtime/python`（PyInstaller 自带解释器，不需要它）与
`vendor/runtime/python-nuget.zip`（下载缓存）。理由见
`scripts/build_native_installer.py` 的模块 docstring。

## 四、本文件未包含的内容

- **未包含任何 DeepSeekBot / DSH 的源代码**。若将来引入，必须在此补充：文件范围、修改内容、原始版权声明的保留位置。
- 未包含 Qt 的静态链接或修改版本。当前为动态链接，符合 LGPLv3 要求。
- 未包含 Python 标准库与 PSF 许可证全文；PyInstaller 在 `dist/` 中保留 Qt 与 Python
  的许可证文件，安装包把它们一并带入程序目录。
