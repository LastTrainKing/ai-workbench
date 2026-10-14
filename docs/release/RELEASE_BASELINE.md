# AI Workbench 发布基线

§4.1 要求。**只写已证实的结果**，未执行的项目标 `NOT RUN`。
采集时间：2026-10-14

---

## 1. 仓库

| 项 | 值 |
|---|---|
| 项目根目录 | `C:\Users\user\Desktop\ai-workbench`（**源码仓库**，不是 `%LOCALAPPDATA%\AI Workbench` 安装目录） |
| 分支 | `feat/private-ai-employee` |
| HEAD | `b5cf78dd86c1ae788e6db8a471002daf5eaab15f` |
| 工作区 | 干净（`git status --short --branch` 只有分支行） |
| `git diff --stat` | 空 |
| `git diff --check` | 空（无空白错误） |
| 历史提交数 | 266（`git rev-list --count --all`） |
| 已有 tag | `baseline-2.3.0-freeze`、`pre-productization` —— **都不是发布 tag** |
| 版本号 | `2.5.0`（`VERSION` 文件） |

## 2. 工具链

| 项 | 版本 |
|---|---|
| Python | 3.13.14 |
| PySide6 / Qt | 6.12.0 |
| Node | v22.22.2 |
| Rust / cargo | 1.98.1 |

## 3. 入口

| 用途 | 路径 |
|---|---|
| 后端应用（FastAPI，开发/测试用） | `backend/app/main.py` |
| 桌面端启动 | `desktop-native/main.py` |
| 桌面端构建 | `desktop-native/build_native.py` |
| 安装包构建 | `scripts/build_native_installer.py` |
| 本地基础设施（PostgreSQL/Redis/Qdrant） | `scripts/infra-start.ps1` |

**桌面端不起 HTTP 服务** —— `ServiceBridge` 在进程内直接调 service 层。这条影响很大：
`backend/app/main.py` 的 `lifespan` **在产品里不执行**，所以"启动时应该发生的事"必须挂在
`desktop-native/awbnative/bootstrap.py` 的 `ensure_ready()` 上（`591fa2e` 已修过一次）。

## 4. 数据库与迁移

| 项 | 值 |
|---|---|
| 迁移文件数 | 10 |
| 迁移 head | `a1e5c7b9d204` |
| 开发库当前版本 | `a1e5c7b9d204`（一致） |
| 测试库当前版本 | `a1e5c7b9d204`（一致） |

**只读核验，未执行任何迁移。** 上一轮 `181d5ed` 修过"装在新版本上不升级"的缺陷，其前向升级
路径在 2.4.0→2.5.0 的实际安装中验证过。

## 5. 命令

| 用途 | 命令 |
|---|---|
| 后端 lint | `.venv/Scripts/python.exe -m ruff check backend/app backend/tests desktop-native` |
| 后端全量测试 | `cd backend && ../.venv/Scripts/python.exe -m pytest tests -q -p no:randomly` |
| QML 页面加载 | `.venv/Scripts/python.exe tools/check_qml_pages.py` |
| Shell 验收（31 项） | `.venv/Scripts/python.exe desktop-native/verify_native_shell.py` |
| 布局审计（19 页 × 3 档） | `.venv/Scripts/python.exe tools/check_layout.py` |
| 投影层回归（108 条断言） | `.venv/Scripts/python.exe desktop-native/verify_projection.py` |
| 桥接层实测（18 项） | `.venv/Scripts/python.exe desktop-native/verify_bridge_employees.py` |
| 真实端到端 | `.venv/Scripts/python.exe desktop-native/verify_employee_delivery.py` |
| 秘密扫描（当前树） | `.venv/Scripts/python.exe tools/scan_for_secrets.py` |
| 秘密扫描（全部历史） | `.venv/Scripts/python.exe tools/scan_git_history_secrets.py` |
| 应用构建 | `.venv/Scripts/python.exe desktop-native/build_native.py` |
| 安装包构建 | `.venv/Scripts/python.exe scripts/build_native_installer.py --build` |

**版本三已修掉的可复现性缺口**：`check_qml_pages.py` / `check_layout.py` /
`scan_for_secrets.py` 原先在 `build/rc/` 下，而 `build/` 被 gitignore 覆盖 —— 干净克隆
里没有它们，于是"按文档可复现"这条不成立。三个工具已移入 **`tools/`（受版本控制）**，
`scan_git_history_secrets.py` 也从 `scripts/` 移入同一目录。见 `tools/README.md`。

**投影层断言数已从 58 增至 108**（版本二加了交付卡片与普通/专家模式导航两组）。

## 6. 现有构建产物

| 文件 | 大小 | 说明 |
|---|---|---|
| `build/installer/AI-Workbench-Native-Setup.exe` | 124,987,470 字节 | 构建于 2026-10-14 00:37 |
| `build/installer/AI-Workbench-Setup.exe` | 97,311,474 字节 | 旧（Tauri 路线，2026-10-09） |

**安装包落后于源码**：源码最后提交于 00:54，安装包构建于 00:37 —— 岗位合同与记忆投影的改动
**不在**这个安装包里。版本一不重建（版本三的闸门要求重建并对齐 commit）。

## 7. NOT RUN

| 项 | 原因 |
|---|---|
| 干净克隆复现（§15） | 版本三范围 |
| 安装包重建与安装验证 | 版本三范围；本轮只做基线记录 |
| 真实桌面（非 offscreen）截图检查 | 当前执行环境为无头；`offscreen` 报告零个字体族，**不能**用来判断真实桌面渲染 |
| 性能测量 | 见 `AIWB_3_0_PHASE2_PERFORMANCE.md`：产品代码未变，无改前/改后可对比 |
| 多显示器 / 混合 DPI | 无该环境 |
| Linux / macOS 构建 | 无该环境；**不得据此宣称跨平台支持** |
