# AI Workbench 1.0 —— 状态

## 1. 当前版本

| 项 | 值 |
|---|---|
| 版本 | **2.2.0** |
| 分支 | `fix/release-freeze-20261009` |
| 提交 | `d491d8b` 关闭流程 · `45744b6` 三个页面 · `0ef8e65` 快捷键 · `58079c2` 首页 session · `b054105` 图标 · `4d9d487` API Key 校验 · `d70f179` 首页等待环境 · `d5845bf` 无头运行收尾 · `23d17b1` 界面重构 · `42ffaa9` 去卡片化与布局 · `93c614d` 按钮状态 · `e2091e1` 启动器 pid 回收 · `c3f3a66` AI 状态阶梯与设置页表单 |
| 安装包 | `C:\Users\user\Desktop\AI-Workbench-Release\安装包\AI-Workbench-Native-Setup.exe` |
| 安装包大小 | 124,519,024 字节（118.8 MB） |
| 安装包 SHA-256 | `e5df0c9547cae2beca1aee197d825e69ebd019db694a94f59b4305e3dbf57d93` |
| 已安装 EXE | `C:\Users\user\AppData\Local\AI Workbench\AI-Workbench-Native.exe` |
| 已安装 EXE SHA-256 | `d03b93fe7ab9af9604b83b81131521f03811bef7155a76cd8cbd22f931025ff8` |
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
| 首页（工作台）：顶部直接向 AI 下达任务 / 最近项目 / 待办任务 / 最近运行（含「继续」）/ 知识与资料 / AI 服务状态。一屏一个主操作，其余为文字按钮 | 可用 |
| Agent 工作台：写目标 → 规划 / 步骤 / 工具调用 / 结果 / 产物 | 可用 |
| 项目：列表、详情（任务 / 资料 / Agent 运行记录） | 可用 |
| 知识库与文档导入 | 可用 |
| 对话（流式） | 可用 |
| 运行记录：按内容自适应高度的行、发丝线分隔、整条记录在行下展开（含「技术详情」看原始异常）；UNKNOWN 对账。执行入口只有 Agent 页一处 | 可用 |
| 任务、笔记、搜索、知识图谱、Planner | 可用 |
| 权限：四档 + 路径级 + 拒绝清单，改动真实生效 | 可用 |
| 设置：AI Provider 与 API Key，五档状态（未配置 / 已配置未测试 / 连接成功 / 连接失败 / 离线模式），「测试连接」发一次真实请求并记录结果 | 可用，**已配置 DeepSeek**（见下） |
| 安装 / 升级 / 卸载 / 重装，保留数据 | 已验证 |
| **正常关闭不残留进程，数据库优雅停止** | 已验证（连续 10 轮） |
| 读取迁移之前建立的旧数据库 | 已验证 |

测试：后端 **1367 passed / 10 skipped / 0 failed**；原生 shell **31/31**；**布局审计**（走真实控件树，15 个页面 × 1920×1080 / 1280×800 / 960×640）**0 处**（文字硬切 / 越界 / 重叠 / 零尺寸）；QML 错误 0；核心闭环（导入 → 入库 → 引用问答 → Agent 执行 → 结果可读）走通。

## 5. 未完成

| 项 | 状态 |
|---|---|
| 代码签名 | 没有证书。首次运行会有 SmartScreen 警告，点「仍要运行」 |
| 真实 Embedding | 无可用凭据。语义检索不可用，**关键词检索正常** |
| 多显示器 / 高 DPI | 本机单显示器，未验证 |
| 干净 Windows 机器安装 | 只有这一台环境 |
| `docker compose` 部署路径 | 未执行 |
| 静默安装 + 应用正在运行时 | 守卫分支逻辑相反，只在 `/S` 且应用运行中触发 |
| 用任务管理器强杀应用 | 数据库会被留下未正常关闭；下次启动会崩溃恢复（已修：陈旧 pid 不再阻塞启动，实测 9.6 秒恢复） |

## 6. 你的下一步

1. 双击桌面 `AI Workbench` 打开。
2. **AI 已经配好了**（DeepSeek，模型 `deepseek-flash`），可以直接对话和跑 Agent，不用再填 Key。
   要换 Key 或换模型：首页底部「AI 设置」。
3. 想让它干活：在首页最上面的输入框写下目标 → 「交给 Agent 执行」。它会跳到 Agent 页并**直接开始**，
   页面下面显示规划 / 步骤 / 工具调用 / 结果。
4. 想让它读你的资料：首页「导入资料」→ 建知识库 → 导入文件 → 到「问答」提问，答案后面带引用。
5. 上次没跑完的任务：首页「最近运行」里那一条会带「继续」按钮，点了就用原来的目标重跑。

**关于费用**：用的是你自己的 DeepSeek 额度。当前配置只用 `deepseek-flash` 这一个模型。
一次真实调用的实测：82 tokens、0.98 秒。注意**应用自己的费用估算对 `deepseek-flash` 显示 0**——
它的价格表里没有这个模型（只有 `deepseek-chat` / `deepseek-reasoner`），所以「运行记录」里的
成本是空白的。要知道花了多少，看 DeepSeek 后台，不要看这个数字。

**Key 存在哪里**：`C:\Users\user\AppData\Local\AI Workbench Data\.runtime\ai-config\<账号>.json`。
不在源码里、不在 Git 里、不在安装包里、不在任何报告里；界面读回时只返回「是否已填」和掩码。
换机器或重装：这个文件在数据目录里，会跟着数据一起保留（备份时一起复制走）。

**备份数据**：整个 `C:\Users\user\AppData\Local\AI Workbench Data` 目录复制走即可。
交付包里另有两份数据库转储（`备份\database-backup\`）。

**升级**：直接双击新的安装包。它升级现有安装，保留数据、配置和 API Key。

**可以删**：`Desktop\ai-workbench\build\`、`desktop-native\dist\` 与 `build\`。
**不要删**：数据目录、`Desktop\ai-workbench`（源码与 Git 历史）、`archive\releases`。

## 7. 判断

**RELEASE CANDIDATE 1.0** —— 可安装、可启动、历史数据可读、核心 Agent 页面可用、正常关闭无数据库异常、AI 已接入真实模型。

进 1.0 正式版还差：代码签名。
