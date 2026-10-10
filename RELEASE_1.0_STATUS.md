# AI Workbench 1.0 —— 状态

## 1. 当前版本

| 项 | 值 |
|---|---|
| 版本 | **2.2.0** |
| 分支 | `fix/release-freeze-20261009` |
| 提交 | `d491d8b` 关闭流程 · `45744b6` 三个页面 · `0ef8e65` 快捷键 |
| 安装包 | `C:\Users\user\Desktop\AI-Workbench-Release\安装包\AI-Workbench-Native-Setup.exe` |
| 安装包大小 | 124,194,886 字节（118.4 MB） |
| 安装包 SHA-256 | `67e5d226453c6540eec302f356ce93352fdda998a89794f565780adf87496437` |
| 已安装 EXE | `C:\Users\user\AppData\Local\AI Workbench\AI-Workbench-Native.exe` |
| 已安装 EXE SHA-256 | `76d61b3009b7450c1fb85e4f1ada9fe9dc1dae5d4418506a00b707830c334393` |
| 数据目录 | `C:\Users\user\AppData\Local\AI Workbench Data` |
| 桌面快捷方式 | `C:\Users\user\Desktop\AI Workbench.lnk` |

## 2. 安装位置

- 程序：`C:\Users\user\AppData\Local\AI Workbench\`
- 数据：`C:\Users\user\AppData\Local\AI Workbench Data\`
- 卸载：`C:\Users\user\AppData\Local\AI Workbench\Uninstall.exe`（或「应用和功能」里找 AI Workbench）

卸载**不删数据目录**。重装会接回同一份数据。

## 3. 启动方式

双击桌面 **`AI Workbench`**。开始菜单里也有：`开始 → AI Workbench → AI Workbench`。

打开后窗口先出现，本地环境在背后准备，横幅写「正在准备本地环境…」，就绪后变成「已就绪，可以开始使用」。
不需要你做任何配置。

## 4. 已完成能力

| 能力 | 状态 |
|---|---|
| 首页（工作台）：今日任务、最近项目、知识库、AI 服务状态、四个快速操作 | 可用 |
| Agent 工作台：写目标 → 规划 / 步骤 / 工具调用 / 结果 / 产物 | 可用 |
| 项目：列表、详情（任务 / 资料 / Agent 运行记录） | 可用 |
| 知识库与文档导入 | 可用 |
| 对话（流式） | 可用 |
| 运行历史与详情、UNKNOWN 对账 | 可用 |
| 任务、笔记、搜索、知识图谱、Planner | 可用 |
| 权限：四档 + 路径级 + 拒绝清单，改动真实生效 | 可用 |
| 设置：AI Provider 与 API Key | 可用 |
| 安装 / 升级 / 卸载 / 重装，保留数据 | 已验证 |
| **正常关闭不残留进程，数据库优雅停止** | 已验证（连续 10 轮） |
| 读取迁移之前建立的旧数据库 | 已验证 |

测试：后端 **1343 passed / 10 skipped / 0 failed**；安装验收 **41/41**；原生 shell **31/31**；变异 **33/33**。

## 5. 未完成

| 项 | 状态 |
|---|---|
| 代码签名 | 没有证书。首次运行会有 SmartScreen 警告，点「仍要运行」 |
| 真实 Embedding | 无可用凭据。语义检索不可用，**关键词检索正常** |
| 多显示器 / 高 DPI | 本机单显示器，未验证 |
| 干净 Windows 机器安装 | 只有这一台环境 |
| `docker compose` 部署路径 | 未执行 |
| 静默安装 + 应用正在运行时 | 守卫分支逻辑相反，只在 `/S` 且应用运行中触发 |

## 6. 你的下一步

1. 双击桌面 `AI Workbench` 打开。
2. 首页右上角「填写 API Key」→ 填入 DeepSeek Key。填之前也能用，用的是内置离线引擎。
3. 想让它读你的资料：首页「导入资料」→ 建一个知识库 → 导入文件。
4. 想让 Agent 干活：左侧 **Agent** → 写下目标 → 开始执行。

**备份数据**：整个 `C:\Users\user\AppData\Local\AI Workbench Data` 目录复制走即可。
交付包里另有两份数据库转储（`备份\database-backup\`）。

**升级**：直接双击新的安装包。它升级现有安装，保留数据、配置和 API Key。

**可以删**：`Desktop\ai-workbench\build\`、`desktop-native\dist\` 与 `build\`。
**不要删**：数据目录、`Desktop\ai-workbench`（源码与 Git 历史）、`archive\releases`。

## 7. 判断

**RELEASE CANDIDATE 1.0** —— 可安装、可启动、历史数据可读、核心 Agent 页面可用、正常关闭无数据库异常。

进 1.0 正式版还差：代码签名。
