# 简历项目描述

<!-- scope -->
> **Scope: the agent.** What was added on top of the platform, and — at the end — what
> was deliberately not built.
>
> For the knowledge pipeline, RAG and the backend engineering, see
> [`resume-project.md`](resume-project.md). Neither supersedes the other.


> 只写真实实现并且验证过的能力。每一条都能在代码里找到对应实现，在运行记录里找到对应证据。

---

## 一句话版本

**AI Workbench** —— 本地优先的 Windows 私人 AI Agent：能在用户自己的电脑上规划、执行、验证并修复真实任务，全部操作受权限管控、可审计、可回滚。

---

## 完整版本（简历项目栏）

### AI Workbench · 本地优先的私人 AI Agent
*个人项目 · 2026 · Python / TypeScript / Rust*

一个运行在 Windows 上的桌面 AI Agent。用户说出目标，它在授权范围内操作文件、代码、Git、终端，验证结果，失败时自行修复，全部过程可追溯、可回滚。

**核心能力**

- **自主 Agent 循环**：PLAN → EXECUTE → OBSERVE → VERIFY → FIX → RETRY → DONE。关键设计是**模型决定做什么、代码决定是否成功**——验证是一次工具调用，读它的 exit code，而不是让模型评价自己的输出。支持暂停 / 恢复 / 取消，取消会真正终止子进程树。
- **42 个计算机工具 + 权限体系**：文件、代码、终端、Git、构建测试、进程、网络、屏幕观察。四级权限（观察 / 工作区 / 系统 / 完全），deny 优先于 allow，受保护路径（SSH 私钥、浏览器凭据、项目 .env）内置且设置无法解除。高风险命令弹确认框并说明原因与影响。
- **快照 / Diff / 回滚**：运行前自动快照，结束后可看逐文件 diff。回滚记录运行前后两份 manifest，**如果文件在运行结束后被改动过就拒绝回滚**，避免销毁用户自己的修改。
- **Personal Context Layer**：Profile / Memory / Preferences / Context 四层严格分离。记忆评估器做规则预筛 + 模型结构化判定 + 去重 + 冲突检测 + 置信度路由；敏感信息永不落库；冲突生成候选问用户而非自动覆盖。上下文组装有硬 token 预算并按优先级裁剪，实际注入内容全部可回看。
- **RAG 知识库**：文档导入 → 解析 → 分块 → 向量化 → 混合检索 → 重排 → 带引用作答。
- **文档产物**：`document_write` / `document_read` 支持 docx / xlsx / pptx / pdf / md / csv，风格读用户偏好（简洁 / 学术 / 商务），**生成后必须回读校验**才算完成。
- **Windows 桌面交付**：Tauri + WebView2 原生窗口，无浏览器、无地址栏、无控制台窗口。关闭应用时通过 Windows 作业对象（`KILL_ON_JOB_CLOSE`）保证后端服务被清理，崩溃或被强杀也不例外。

**工程数据**

- 后端约 2 万行 Python（FastAPI + PostgreSQL + Redis + Qdrant），29 张表
- 前端约 1.2 万行 TypeScript（React + Vite），Rust 约 900 行
- 测试：后端单元测试 + 前端 114 个 + 三套真实运行测试脚本（浏览器交互 26+22 项、桌面九条断言 × 三种退出场景、API 31 项、记忆闭环 18 项）
- 交付：EXE + NSIS 安装包 + 源码包，密钥扫描 0 处

---

## 可以单独展开的三个技术点

### 1. 验证交给代码，不交给模型

大多数 Agent 框架让模型给自己打分。这里 VERIFY 是一次工具调用，读 exit code——`run_tests` 返回 1 就是失败，模型说"应该修好了"没用。

配套的细节是**区分诊断步骤和断言步骤**："运行测试并记录失败"这一步的目的是看到失败，exit 1 说明它正常工作了；"修复函数"这一步 exit 1 就是失败。这个区分是被测试逼出来的——Demo 里 Agent 明明正确定位了 bug，却因为诊断步骤被判失败而反复重试到耗尽次数。

### 2. 三层套娃的关窗 bug

用户反馈"关掉窗口后数据库和 API 还在跑"。第一次猜"关窗钩子没触发"——**错**。加了全链路生命周期日志之后：

1. 钩子确实触发了
2. `project_root` 字段在"后端已运行"的提前返回路径上永远是 `None`，`stop()` 在守卫处直接返回——而这正是用户每次重开程序走的路径
3. 真正的拦路虎：停止脚本的注释里含 UTF-8 破折号，**cmd.exe 按 ANSI 读 .bat**，字节被破坏后注释里的词被当成命令执行，脚本 0.3 秒就以 255 退出

修好前两层它依然跑不起来，因为脚本自己是坏的。最终方案是作业对象（内核级进程分组）+ 生命周期日志 + 纯 ASCII 脚本 + BOM 检查。

**教训**：加日志之前的所有结论都是假设。这个项目三轮下来，我没有一次推断是对的。

### 3. 记忆为什么不许自动覆盖

用户说"以后都用 React"，三个月后说"改用 Vue 了"。哪条对？只有用户知道。自动覆盖等于替用户做他才有资格做的判断。

所以矛盾不落库、不覆盖，而是生成候选问用户。

这里也有个真实的坑：最初的冲突检测是**词法的**（字符重合度 + 否定词）。它对近义改写有效，但对真正的矛盾完全失效——"默认用 React"和"改用 Vue 了"意思相反，用词却几乎不重合。修复是把这个问题交给模型（它本来就在被调用，多问一句"是否与已有记忆矛盾"零额外成本），语义矛盾正是模型擅长而字符串比较不擅长的事。

---

## 技能关键词

`Python` `FastAPI` `PostgreSQL` `Redis` `Qdrant` `SQLAlchemy` `Alembic`
`TypeScript` `React` `Vite` `TanStack Query` `Tailwind`
`Rust` `Tauri` `WebView2` `Win32 API`（作业对象 / 进程管理）
`LLM Agent` `Tool Calling` `RAG` `向量检索` `Prompt Engineering`
`权限模型` `审计日志` `快照回滚` `Windows 桌面交付`

---

## 不建议写进简历的（因为没做到）

诚实比好看重要，这几条如果被追问会很难看：

- **没有浏览器控制**——只有 `screenshot` / `screen_read` 观察能力，没有点击、输入、页面自动化
- **没有鼠标键盘控制**
- **没有 OCR**——扫描版 PDF 明确报错
- **Embedding 用的是离线哈希**，不是真实语义向量（DeepSeek 不提供 embedding 接口）
- **Ollama / OpenAI 端点未实测**——代码路径在，但没有真实端点验证过
- **安装包仍需项目目录存在**，不是自包含安装

如果被问到这些，直接说"这一版没做，因为优先级排在核心闭环之后"——比含糊过去好。
