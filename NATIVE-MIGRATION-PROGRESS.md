# Native migration progress — round 6

安装交付、Agent 安全对账与核心 UI。本轮把「可运行的原生 EXE」推进为**可安装、可独立启动、可卸载**
的 Windows 桌面程序，给 `UNKNOWN` 的工具调用补上**对账**，并继续迁移原生 UI。

上一轮报告（round 5）的提交链为 `aa8e11b` → `63d6f6b` → `17819ff` → `0ffe287`。本轮从 `0ffe287`
开始。

---

## A. 工程状态

| 项 | 值 |
|---|---|
| 起始提交 | `0ffe287` |
| 本轮提交 | `59c6a96`（Agent 对账）· `…`（原生安装交付）· `…`（原生 UI） |
| 分支 | `fix/release-freeze-20261009` |
| 工作区 | 干净（提交后） |
| 2.2.0 归档 | `build/installer/AI-Workbench-Setup.exe` 与 `archive/releases/AI-Workbench-2.2.0-final.exe`，均 `97,311,474` 字节，SHA-256 `698db63662cc24e42873884077ada51b7b97c5ccd45ab3616acd15ad2df0d3d7`，**逐字节未变** |
| 用户数据 | `%LOCALAPPDATA%\AI Workbench Data` 保持原样：**1,530 个文件 / 51,775,575 字节**，本轮开始与结束的指纹一致 |

本轮新增一次数据库迁移 `3f1c9d7a52be`（`tool_calls` 加两列）。开发库与测试库在**先备份**之后升级：
`.runtime/backups/aiworkbench-before-tool-reconcile-20261010-083549.dump`。升级前后
`users=45`、`ai_runs=167`、`documents=11` 一致。

---

## B. 原生安装包

### B.1 构建方式

```bash
python desktop-native/build_native.py --clean --skip-verify     # PyInstaller onedir，约 170s
python scripts/build_native_installer.py --build                # 组装 payload + makensis，约 295s
```

NSIS 用的是上一版安装器同一条工具链（Tauri 缓存里的 `makensis`），没有更换构建体系。

### B.2 产物

| 项 | 值 |
|---|---|
| 安装包 | `build/installer/AI-Workbench-Native-Setup.exe` |
| 大小 | **124,138,107 字节（118.4 MB）** |
| SHA-256 | `31cc3e7a5b0e83d7ee2e35c35241537ac4a017899bdafd0e0aa5e23918679e5f` |
| payload | 507,314,624 字节（483.8 MB）/ 3,924 个文件，压缩比 4.09，编译 281 秒 |
| 独立运行目录 | `desktop-native/dist/AI-Workbench-Native/`，2,270 文件 / 261,601,307 字节，exe 20,934,457 字节 |

构建可重复：`build/native-installer-report.json` 记录大小、SHA-256、文件数、压缩比与耗时。

payload 的组成与**刻意不打包**的东西：

| 内容 | 大小 | 说明 |
|---|---|---|
| `desktop-native/dist/AI-Workbench-Native` | 248 MB | PyInstaller 产物，**原样复制** |
| `vendor/runtime/pgsql` | 120 MB | 内置 PostgreSQL |
| `vendor/runtime/qdrant` | 68 MB | 内置 Qdrant |
| `vendor/runtime/redis` | 46 MB | 内置 Redis |
| `scripts/`（5 个文件） | 74 KB | `lib.ps1`、`infra-start.ps1`、`stop-by-pid.ps1`、两个提权辅助 |
| **不打包** `vendor/runtime/python` | −323 MB | 上一版外壳需要它来启动 FastAPI；原生版自己就是解释器，从不运行 `dev.ps1` |
| **不打包** 其余 `scripts/` | — | `start-desktop.bat`、`dev.ps1` 是**上一版架构**的入口；装进原生版会让一个程序目录里有两套互相矛盾的说法 |
| **不打包** `.env` / `.runtime` / `pgdata` | — | 部署凭据与用户数据库 |

### B.3 安装布局与数据分离

- 安装到 `$LOCALAPPDATA\AI Workbench`（**与上一版同一个目录、同一个卸载注册表项**），所以装原生版是
  **升级**，不是第二个产品：设置里一条记录、一个目录、一个数据目录。
- 安装时**显式删除上一版外壳的文件**：`AI Workbench.exe`、`backend\`、`frontend\`、`worker\`、
  `vendor\runtime\python\`（323 MB）、旧的 `scripts\`。
- **不删 `.env`**：它装着这次安装的 `SECRET_KEY`，删掉会轮换密钥、把用户登出，还可能让旧密钥加密的
  数据读不出来。
- 数据目录写在 `data-dir.txt`，**在程序目录之外**。读取顺序（三处实现必须一致）：
  `AI_WORKBENCH_DATA_DIR` → `<程序目录>\data-dir.txt` → `%LOCALAPPDATA%\AI Workbench Data\.runtime`。
  本轮修掉了一个不一致：`resources.configure()` 会**无条件**把环境变量设成默认值，从而**盖掉安装器写的
  `data-dir.txt`**——装完启动会看到一个**崭新的空数据库**，就在真实数据库旁边。现在标记文件优先。

### B.4 安装验收

`scripts/verify_native_installer.py`：静默装到隔离目录、用隔离数据目录、**实际运行装出来的 EXE**、
最后卸载，并把注册表项与快捷方式快照后还原。运行环境与本机限制写在 B.6。

验收结果见 **F.1**（此处不重复数字，避免两处不一致）。

### B.5 打包缺陷（本轮通过「真的启动它」找到的）

1. **`shiboken6.Shiboken` 曾被 spec 排除**（round 5 修）。它是 PySide6 全部类的 C++ 绑定层，产物在
   `import PySide6` 时崩溃；GUI 子系统下表现为**弹框等待**而不是报错。
2. **payload 的 `.zip` 后缀规则吃掉了 `base_library.zip`**（本轮修）。它是 PyInstaller 的引导归档。
   安装照样「成功」，文件数 3,912 而不是 3,910，**可执行文件在引导阶段挂起，没有消息、没有日志、没有
   窗口**——GUI 子系统在 Python 起来之前失败就没有地方报告。两次验收各挂了 10 分钟才被发现。
   现在 PyInstaller 产物**原样复制**（`VERBATIM_SOURCES`），并且**拷贝后比对文件数**，少了就中止构建。
3. **`stop-by-pid.ps1` 在安装布局下认不出自己的进程**。它从「运行时目录的父目录」推产品根目录
   （`<data>\.runtime` → `<data>`），这对开发检出成立；安装版的数据目录在程序目录**之外**，于是
   `<data>\vendor` 不存在，**每个进程都被跳过**——卸载后 PostgreSQL、Redis、Qdrant 全部留在运行中。
   改用 `$PSScriptRoot` 的父目录，并支持显式 `-ProductRoot`。
4. **窗口化的打包应用没有任何地方可以报告致命错误**。本轮加了崩溃日志：在**第一个可能失败的 import
   之前**安装处理器，写到 `<data>\logs\native-crash.log`（拿不到数据目录时退到程序目录），退出码
   70/71 与「正常退出」区分开。`print` 也换成受保护的写入——GUI 子系统下 `sys.stdout` 是 `None`，
   `print` 会抛 `RuntimeError: lost sys.stdout`，而 Qt 消息处理器里正好有一个 `print`。

### B.6 环境限制（如实记录，不当作通过）

- **没有代码签名证书**：安装包未签名，Windows 会显示未知发布者。→ `BLOCKED`
- **没有干净的 Windows 环境**：无法验证「未装过 Python 的机器」。→ `BLOCKED`
  （已有的近似证据：验收把 `PATH` 削到只剩系统目录并清掉 `PYTHONHOME`/`PYTHONPATH`/`VIRTUAL_ENV`
  后，打包应用仍能启动并完成自检。）
- **单显示器**：多屏 DPI 行为未测。→ `BLOCKED`
- 安装/修复/卸载的**交互式**流程未人工点过；验收走的是 `/S` 静默路径。→ `PARTIAL`

---

## C. Agent 执行安全

### C.1 两个「幂等」声明是错的

round 5 把 `file_write`、`directory_create`、`git_add` 标为可重放。本轮逐条读了实现：

- **`file_write`**：理由曾是「留下的文件是 `path` + `content` 的函数」。这对**它写的那个文件**成立，对
  **它找到的那个文件**不成立——重放会把 `content` 写到 `path` 现在的内容上。如果这中间有别人写过
  （用户、另一个工具、编辑器），重放就把它毁了。
- **`git_add`**：理由曾是「同一个路径 add 两次记录同样的路径」，并把 `git add -A` 的注意事项
  当作「只有别的东西同时在写工作区才要紧，而崩溃重放不是那种情况」。崩溃重放**恰恰是那种情况**：
  它发生在**之后**。用户可能已经改过文件，重放就把原调用没见过的内容放进索引；用户也可能跑过
  `git reset`，重放会把一个刻意的动作撤销掉。**索引是用户的状态，不是这个程序的暂存区。**
- **`directory_create` 保留**：它的全部效果是一个 bit——「这个目录存在」，`mkdir(parents=True,
  exist_ok=True)` 只产生这个 bit，别的什么也不产生。重放不可能产生不同的结果。

### C.2 工具级对账

`Tool.reconcile()` 去看世界，返回三种结论：

| 结论 | 含义 |
|---|---|
| `APPLIED` | 这次调用想要的**状态**现在就在。重跑不会多出任何东西。 |
| `NOT_APPLIED` | 想要的状态不在。这次调用没有生效，或者之后被撤销了——两种情况重跑都不会重复。 |
| `UNDECIDABLE` | 看不出来。**默认值，也一直是默认值。** |

**结论说的是状态，不是历史。** 崩溃销毁了工具当时唯一的那份观测记录，没有任何东西能重建它。能做的
只有看**现在**，而恢复真正要问的正是「还有没有必要重跑」——状态就是那个问题的答案。

实现了三个：

| 工具 | 检查方式 |
|---|---|
| `file_write` | 比对目标文件的 SHA-256 与本次要写入内容的摘要 |
| `directory_create` | `is_dir()` |
| `git_add` | `git diff --cached --name-only`——git 自己关于索引的回答 |

另外二十多个工具没有对账方法，**默认 `UNDECIDABLE`**。「没人写过检查」不能读成「什么都没发生」。

三种情况给了谨慎的答案而不是方便的答案：

- **文件在、内容不同 → `UNDECIDABLE`，不是 `NOT_APPLIED`**。它同时符合「这次写入没发生」和「发生了
  但之后被改写」，而这两种情况的正确处理相反。报 `NOT_APPLIED` 会诱导一次重放，把用户现在放在那里
  的内容覆盖掉——正是这套机制存在的理由。
- **`git add -A` → `UNDECIDABLE`**。索引现在的状态无法归因于它。
- **路径无法解析 → `UNDECIDABLE`**。为此 `tool_calls` 新增 `working_directory`：
  `file_write("out.txt")` 每次运行指的都是不同的文件，而对账发生在**之后的另一个进程**里，没有运行、
  没有上下文。把相对路径按当前进程的目录解析，不是在做一个更弱的检查，而是在检查**另一个文件**并给出
  关于它的确定结论。

### C.3 UNKNOWN 恢复规则

- 可以证实已成功 → `SUCCEEDED`，`reconciled_at` 写入，**`finished_at` 保持 NULL**（工具自己的进程
  从未观测到结束，写上去就抹掉了这两列存在的意义）。
- 可以证实未发生副作用 → 新的 `ToolCallStatus.NOT_APPLIED`。**不是 `SKIPPED`**：`SKIPPED` 是派发**之前**
  做的决定，`NOT_APPLIED` 是事后取得的证据。合并两者会让「检查发现什么都没有」被读成「这个调用根本
  没发生过」。
- 已确认失败 → `FAILED`（工具自己报的）。
- 无法判断 → 保持 `UNKNOWN`，**不写 `reconciled_at`**，让之后上下文更好的一个 pass 还能再试。

**没有任何重放路径。** `app/ai/tools/reconcile.py` 里不存在重放，也不打算加：这套记录存在的理由就是
重复的副作用事后无法检测，唯一安全的做法是不做。API 显式返回 `replay_allowed: false`，而不是留给
界面去推断。

入口：`POST /agent/runs/{id}/reconcile`、`GET /agent/runs/{id}/unresolved`，以及启动时对没人看着的运行
跑一遍同一个检查。

### C.4 故障注入与对账测试

沿用 round 5 的真实子进程故障注入（8 个崩溃窗口，`Popen.kill()` 从外部杀），本轮新增：

- `backend/tests/integration/test_release_freeze_tool_reconcile.py`：**21 个**。含
  「对账不修改目标文件」（一次「检查」如果偷偷把操作重做一遍，在数据库里看起来完全一样，而文件已经
  被毁）、「只检查 `UNKNOWN` 行」（已 `SUCCEEDED` 的行是证据不是问题；重新检查会把一次确定发生过的
  调用改写成 `NOT_APPLIED`）、「相对路径没有工作目录时不检查」。
- `test_release_freeze_tool_replay.py`：分类断言更新为 42 个，含「三个可对账的工具确实覆盖了
  `reconcile`」——基类实现返回 `UNDECIDABLE`，忘了覆盖的子类会被静默当成「查不了」。
- 变异校验 26/26 全部命中（新增 4 个：文件写入声称可重放、内容不同被报成未生效、没有检查的工具声称
  已生效、对账会写入目标）。

一个测试第一次是**夹具**错：它用 `write_text` 建文件，Windows 上会把 LF 翻成 CRLF，于是拿 7 字节的
文件去比 6 字节的意图，把一次真的落地的写入报成 `UNDECIDABLE`。检查是对的，准备工作错了。

### C.5 边界

- 数据库提交与外部副作用不可能天然同事务。**没有声称 exactly-once。**
- `UNDECIDABLE` 是**报告**，不是解决方案。没有任何工具能回答「这事做过没有」。
- RAG 与 Agent 的恢复机制不同：RAG 没有副作用，不存在这套问题。
- 仍未覆盖：工具在**对账之前**产生的副作用被第三方改动，会被读成 `UNDECIDABLE`（这是有意的），
  需要人工判断。

---

## D. 原生 UI

### D.1 旧版页面清单（从代码重新清点）

`frontend/src/pages/` 下 **25 个** `.tsx`——本轮数的，不是抄上一轮的。状态是**原生**外壳的能力。

| 旧页面 | 原生状态 | 依据 |
|---|---|---|
| `DashboardPage` | IMPLEMENTED | `DashboardPage.qml` / `loadDashboard` |
| `KnowledgePage` | IMPLEMENTED | `KnowledgePage.qml` / `loadKnowledgeBases` / `createKnowledgeBase` |
| `KnowledgeDocumentsPage` | IMPLEMENTED | `DocumentsPage.qml` / `loadDocuments` / `importDocument` |
| `DocumentsPage` | IMPLEMENTED | 同上 |
| `TasksPage` | IMPLEMENTED | `TasksPage.qml` / `loadTasks` / `createTask` / `completeTask` |
| `ProjectsPage` | IMPLEMENTED | `ProjectsPage.qml` / `loadProjects` / `createProject` |
| `NotesPage` | IMPLEMENTED | `NotesPage.qml` / `loadNotes` / `createNote` |
| `SettingsPage` | IMPLEMENTED | `SettingsPage.qml` / `loadAiConfig` / `saveAiConfig` |
| `AIRunsPage` | **IMPLEMENTED** | `RunsPage.qml` 的运行历史卡：真实 `ai_runs` 行，重启后仍在 — **本轮** |
| `AIRunDetailPage` | **IMPLEMENTED** | 同一页的详情卡：状态、步骤、tokens、工具调用明细 — **本轮** |
| `ChatPage` | **IMPLEMENTED** | `ConversationPage.qml`，真实流式 — **本轮** |
| `ConversationPage` | **IMPLEMENTED** | 同上 — **本轮** |
| `AgentPage` | PARTIAL | 运行与取消已覆盖；权限管理未做 |
| `MyAIPage` | PARTIAL | Provider 配置在设置页 |
| `AgentPermissionsPage` | NOT_STARTED | bridge 未暴露权限槽 |
| `AgentWorkbenchPage` | NOT_STARTED | — |
| `ProjectDetailPage` | NOT_STARTED | — |
| `PlannerPage` | NOT_STARTED | — |
| `GraphPage` | NOT_STARTED | — |
| `SearchPage` | NOT_STARTED | — |
| `WorkbenchPage` | NOT_STARTED | — |
| `HomePage` / `LoginPage` / `OnboardingPage` / `NotFoundPage` | N/A | 单窗口、单用户、首次启动初始化代替引导 |

**12 IMPLEMENTED · 2 PARTIAL · 7 NOT_STARTED · 4 N/A。**

### D.2 本轮完成的业务闭环

**运行历史与运行详情**（`RunsPage.qml`）：从数据库读真实 `ai_runs`，可选中查看步骤、tokens、
工具调用明细。对 `UNKNOWN` 的调用单独列成一块，带「对账」按钮，并**明说不会重跑**——只被告知
「可以检查」的用户，会默认成「可以重试」。

**对话与流式聊天**（`ConversationPage.qml`）：每次发送把**完整历史**一起交给模型（这才是追问能接上
上文的原因）。三种状态不假装：等待（显示已等待秒数）、推理（显示已收到多少字，并说明这不是回答）、
不支持流式（直接说，而不是把一块内容当成很多块来动画）。推理模型的空回答会明确说明「输出预算全用在
推理上了」。

**启动状态**：窗口先出来，本地服务在它**后面**起。PostgreSQL 首次 `initdb` 要几十秒，放在窗口之前
意味着用户双击图标后什么都没有发生；数据库缺失时窗口甚至不出现，唯一的证据是一个日志文件。

**崩溃日志**：见 B.5.4。

### D.3 键盘与无障碍

`desktop-native/verify_native_shell.py`（29 项）继续维护：`QTest` 发真实按键，再问窗口焦点去了哪里，
不读 QML 源码来判断键盘能不能用。焦点从 `QWindow.focusObject()` 读——Repeater 的委托在 Python 里
`findChildren` 够不着。结果见 F.1。

---

## E. 模型与费用

- **本轮真实模型调用：0 次，¥0.00。** 累计仍为 ¥0.01（round 4 的那次最小验收）。
- 本轮的打包、迁移、对账、键盘与全部 UI 工作**不需要**真实调用；用能本地证明的证据，不用花钱的调用
  去加强。
- API Key 未写入源码、Git、报告、命令行参数或日志：`scripts/verify_qdrant_key.py` 会遍历 **688 个
  Git 跟踪文件**确认，并且**不把密钥作为命令行参数**（那会把它放进进程列表）。
- **Embedding：`BLOCKED`。** DeepSeek `/embeddings` 此前返回 404，本轮没有可用的独立 Embedding
  Provider，也没有下载重型模型。知识库的向量化仍走本地 hash/n-gram 方案，**没有**把它说成语义
  Embedding。

---

## F. 测试与风险

### F.1 本轮实际运行结果

| 验证 | 命令 | 结果 |
|---|---|---|
| **原生安装验收** | `python scripts/verify_native_installer.py` | **41/41 PASS**，退出 0，用时 5 分 12 秒 |
| **Qdrant key 校验** | `python scripts/verify_qdrant_key.py` | **12/12 PASS** |
| 变异校验 | `python scripts/verify_release_freeze_mutations.py` | **26/26 CAUGHT** |
| 打包验收 | `python desktop-native/verify_native_package.py` | **10/10 PASS**（Qdrant 一项为 `BLOCKED`，见 E） |
| 键盘与无障碍 | `python desktop-native/verify_native_shell.py` | **30/30 PASS** |
| 工具分类单元测试 | `pytest tests/unit/test_release_freeze_tool_replay.py` | 42 passed |
| 工具对账集成测试 | `pytest tests/integration/test_release_freeze_tool_reconcile.py` | 21 passed |
| 崩溃窗口故障注入 | `pytest tests/integration/test_release_freeze_tool_invocation.py` | 12 passed |
| 安装脚本语法（两个 NSI） | `python scripts/check_installer_syntax.py` | 均 PASSED，无致命警告 |
| 后端全量回归 | `pytest tests -q -p no:randomly` | **1310 passed · 10 skipped · 0 failed**（690 秒） |

安装验收的 41 项覆盖：静默安装到隔离目录；安装树完整（17 项必需内容）；无凭据、无私钥、无用户数据；
注册表 5 项正确；开始菜单与桌面快捷方式；数据目录与安装目录分离；**安装后的可执行文件启动并自检 9 项
全过**；**在清空的数据目录里自己把 PostgreSQL / Redis / Qdrant 拉起来（14 秒）**；**schema 由迁移从零
建成**；真实业务写入（一个账号 + 一个项目，经服务层）并由安装后的 EXE 读回；重启后数据仍在且 schema
改为**校验**已有结构；`--stop-services` 释放全部端口、无残留进程；卸载删除程序、注册表项与两个快捷
方式，**保留数据目录且内容逐字节未变**；重新安装后仍能启动；最后还原注册表与快捷方式快照，并确认
**用户真实数据目录（1,530 文件 / 51,775,575 字节）前后一致**。

### F.2 数据完整性

- 真实数据目录 `%LOCALAPPDATA%\AI Workbench Data`：**1,530 文件 / 51,775,575 字节**，本轮开始与结束
  的（文件数，字节数）一致。
- **中途确实被改动过一次，已发现并还原。** `verify_native_package.py` 只在调用方给了数据目录时才设置
  `AI_WORKBENCH_DATA_DIR`，而有两处调用没给——于是打包应用按安装版的规则解析到
  `%LOCALAPPDATA%\AI Workbench Data\.runtime`，`runtime.ensure()` 往里面写了
  `logs\native-startup.log`。没有数据丢失，但一次验证运行修改了它不被允许触碰的东西，而且是静默的。
  现在该函数**默认创建一次性临时目录**，任何调用点都不可能因为漏传参数而碰到真实目录。原文件已备份到
  `build/restored-user-data/`，真实目录恢复到 1,530 文件 / 51,775,575 字节，重跑打包验收后仍然是这个
  数字。
- 安装验收在 `finally` 里把注册表项与两个快捷方式**快照后还原**；即使中途失败也还原。
- 卸载**默认保留**数据目录，并且验收断言卸载后数据目录仍在、内容未变、`pgdata\PG_VERSION` 还在。
- 迁移先 `pg_dump` 备份再执行；迁移幂等（只补缺失的列）。

### F.3 本轮引入并修掉的缺陷

1. `resources.configure()` 盖掉安装器写的 `data-dir.txt`（B.3）。
2. payload 的 `.zip` 规则吃掉 `base_library.zip`（B.5.2）。
3. `stop-by-pid.ps1` 在安装布局下跳过全部进程（B.5.3）。
4. **`subprocess.run(timeout=…)` 在孙进程持有管道时死锁**。`infra-start.ps1` 会留下
   `postgres.exe`，它继承了脚本的 stdout；超时触发后 `run` 杀掉直接子进程，然后**等管道关闭**，
   而存活的后代让它永远等下去——超时不再有意义。实测：装好的应用在一个**已经起来**的服务栈上挂了
   900 秒。改成**输出重定向到文件** + `Popen.wait` + 超时后 `taskkill /T`。
5. **同一个文件句柄问题的第二种表现：临时目录清理失败导致应用崩溃。** 第一版把启动脚本的输出写进
   `TemporaryDirectory`。那些引擎是通过 `Start-Process` / 计划任务起来的，**不在 PowerShell 的进程树
   里**，`taskkill /T` 够不着它们，它们继续持有 stderr 句柄；Windows 于是拒绝删除文件，
   `TemporaryDirectory.__exit__` 抛 `PermissionError`，这个异常一路穿出 `main()`——装好的应用以
   **退出码 71** 结束，错误信息是一句关于临时文件的回溯，而它**已经成功启动了数据库栈**。
   现在这些日志写在 `<data>\logs\` 下，不删除，和启动器自己的日志放在一起——那本来也是排查启动失败
   时第一个会看的地方。
6. **启动器不退出，而应用在等它退出。** 实测：三个端口都已就绪、服务栈健康，PowerShell 进程四分钟后
   仍然活着（提权路径把工作交给 `runas` 或计划任务，那些会留下进程）。等它退出意味着安装版首次启动
   会**在一个已经在提供服务的栈上**空转满 300 秒。现在以端口为准：三个端口连续 5 秒都开着就结束启动器
   并继续。宽限期不是零——PostgreSQL 在崩溃恢复完成**之前**就打开端口，脚本在那之后还在用
   `pg_isready` 和 `psql "select 1"` 探测。
7. **验收脚本自己的两个缺陷**：
   - **NSIS 卸载器不会卸载。** 它把自己复制到 `%TEMP%`、启动副本、然后退出。等它退出的脚本会在真正
     的工作刚开始时就往下走——于是「注册表项已删除」「快捷方式已删除」在启动器退出几秒后就检查了
     （当时当然还在），而紧接着的重新安装和仍在跑的卸载**抢文件**，安装器自己的 `VerifyInstall`
     正确地以退出码 3 拒绝了它。改用 `_?=<目录>` 就地卸载，并以目录消失作为完成信号。
   - 把 `.pem` 后缀一律当作凭据（`certifi/cacert.pem`、gRPC 的 `roots.pem` 是每个 TLS 客户端都带的
     公共 CA 包），以及把 `-----BEGIN … PRIVATE KEY-----` 这个**字面量**当作私钥（Qt 的 TLS 后端
     带着它是因为它要**解析** PEM）。现在私钥要求 header 后面跟 200 字节以上的 base64 实体。
   - 清理测试服务时，安装版的 `stop-by-pid.ps1` 在一次失败的重新安装后已经不存在，脚本**静默返回**，
     于是测试安装的引擎继续占着 5432，而 `infra-start.ps1` 拒绝启动开发栈。现在回退到仓库里的那份，
     并说明为什么。
8. **本轮在测试套件里引入的两个回归**（全量跑出 47 个失败，两个都是我的）：
   - `test_release_freeze_tool_replay.py` 里我用 `asyncio.run` 跑了一个协程。它**关掉了自己创建的
     事件循环**，并让事件循环策略变成「没有当前循环」，于是**之后**每一个异步测试都以
     「There is no current event loop in thread 'MainThread'」失败——46 个。改成 `async def`。
     这个错误只在全量运行时出现，单独跑那几个文件是绿的，所以它是「顺序」而不是「代码」的问题。
   - `test_release_freeze_process_safety.py::test_installer_runner_does_not_pass_shell_true` 断言的是
     `subprocess.run` 收到的参数，而 `run_nsis_installer` 已经改用 `Popen`（为了把输出写进文件而不是
     管道）。测试改为断言 `Popen`，并保留原来那两条真正重要的要求：`shell=False`，以及命令是一个
     **字符串**而不是列表。
9. `verify_native_shell.py` 的收尾会打印三条
   `TypeError: Cannot read property 'startupState' of null`。它们出现在判定**之后**，是 Qt 拆掉 QML
   引擎时绑定重新求值造成的——应用本身在加载阶段一条警告都没有（`--self-check` 的
   「QML 加载无警告或错误」为「无」）。试过用 keepalive 修，没有效果，所以没有留下那段无用的代码；
   如实记在这里。

### F.4 未解决的风险

| 风险 | 等级 | 说明 |
|---|---|---|
| 安装包未签名 | P1 | 环境限制，需要证书 |
| 交互式安装/修复/卸载未人工验证 | P1 | 只走了 `/S` 静默路径 |
| 干净机器安装未验证 | P1 | 需要第二台机器 |
| 多屏 DPI 未验证 | P2 | 单显示器 |
| 无对账方法的工具仍只有 `UNKNOWN` | P2 | 设计如此；需要人工判断 |
| `file_write` 相对路径 + 缺 `working_directory` 的旧行 | P2 | 老记录没有这一列，只能 `UNDECIDABLE` |
| 权限管理、Planner、图谱、搜索、Workbench 页未迁移 | P2 | 见 D.1 |

---

## G. 交付位置

| 内容 | 绝对路径 |
|---|---|
| 原生安装包（本轮交付） | `C:\Users\user\Desktop\ai-workbench\build\installer\AI-Workbench-Native-Setup.exe` |
| 独立运行目录 | `C:\Users\user\Desktop\ai-workbench\desktop-native\dist\AI-Workbench-Native\` |
| 2.2.0 归档（未改动） | `C:\Users\user\Desktop\ai-workbench\archive\releases\AI-Workbench-2.2.0-final.exe` |
| 安装验收脚本 | `C:\Users\user\Desktop\ai-workbench\scripts\verify_native_installer.py` |
| 安装验收报告 | `C:\Users\user\Desktop\ai-workbench\build\native-installer-acceptance.json` |
| 安装包构建报告 | `C:\Users\user\Desktop\ai-workbench\build\native-installer-report.json` |
| Qdrant key 校验 | `C:\Users\user\Desktop\ai-workbench\scripts\verify_qdrant_key.py` → `build\qdrant-key-report.json` |
| 键盘与无障碍校验 | `C:\Users\user\Desktop\ai-workbench\desktop-native\verify_native_shell.py` |
| 打包验收 | `C:\Users\user\Desktop\ai-workbench\desktop-native\verify_native_package.py` |
| 变异校验 | `C:\Users\user\Desktop\ai-workbench\scripts\verify_release_freeze_mutations.py` |
| 对账实现 | `C:\Users\user\Desktop\ai-workbench\backend\app\ai\tools\reconcile.py` |
| 安装脚本 | `C:\Users\user\Desktop\ai-workbench\installer\ai-workbench-native.nsi` |
| 运行时托管 | `C:\Users\user\Desktop\ai-workbench\desktop-native\awbnative\runtime.py` |
| 数据库备份 | `C:\Users\user\Desktop\ai-workbench\.runtime\backups\` |

---

## 下一步（只列确实还没做的）

1. 给安装包签名（需要证书）。
2. 在干净 Windows 上跑一遍安装验收；人工点一遍交互式安装/修复/卸载。
3. 迁移 Agent 权限管理、Planner、图谱、搜索、Workbench 页。
4. 为更多工具实现对账（`file_append` 有真实且不可重复的副作用，目前只能 `UNKNOWN`）。
5. 真实 Embedding Provider（当前 `BLOCKED`）。
6. 多屏 DPI 与高 DPI 下的界面检查。
