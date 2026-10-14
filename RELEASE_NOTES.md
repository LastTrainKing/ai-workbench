# AI Workbench 3.0.0 — 发布说明

**这是一个发布候选版（release candidate），不是最终版。**

仓库里已完成的部分：源码、测试、文档、安装包、本地 tag。
尚未完成的两件事写在第 6 节，它们不影响"能不能装、能不能用"，影响的是"能不能宣称
已经过完整验证"。

---

## 1. 这一版是什么

私人 AI 员工工作台。你说要什么，员工在你的授权边界内做完，产出**真实的文件**，
系统读回文件验证，再由你决定接受还是退回 —— 而"退回 + 理由"会成为它记住的东西。

2.5.0 让状态说得准。这一版做的是另一半：**让结果出现在工作结束的地方**。

---

## 2. 新增

| | |
|---|---|
| **成果就在运行结束的地方** | 运行页原先只说"产出文件在「交付物」里"，然后给一个跳到另一页的按钮。现在文件名、类型、大小、版本、验证证据、可执行操作和**应用内预览**都在原地 |
| **统一结果契约** | 一个运行"产出了什么"只有一种形状（状态 / 摘要 / 产物 / 验证证据 / 后续动作 / 诊断引用）。**读侧投影，不是第二个状态权威** —— 每个字段现算，不落库 |
| **按类型验证** | `md`/`txt` 严格 UTF-8 且非空；`docx`/`xlsx`/`pptx` 必须是 zip **且**含标志性部件；`csv`/`pdf` 读回行数/页数；`code` 明确报告"没有运行任何检查" |
| **验证的第三种结果** | `pass` / `fail` / `unverified` 三态。读不出文件既不是通过也不是失败 |
| **员工岗位合同** | 12 节：使命、职责、SOP、需要的输入、不做什么、先问你的事、验收标准、产出格式与位置 |
| **普通 / 专家模式** | 普通模式 13 个入口；运行记录、图谱、Planner、权限、搜索、问答只在专家模式 |
| **`tools/`** | 验收与安全工具进了版本控制（原先在 `build/rc/`，干净克隆里没有） |
| **`SECURITY.md` + GitHub Actions** | 见第 5 节 |

## 3. 修复

- **`_persist_run` 从来没有执行过。** 读了一个不存在的列，异常被吞掉，整个函数跳过 ——
  运行照样显示完成，交付物照样登记。**那个想让成功有结束时间的提交，正是拿掉结束时间的提交。**
- **文件被删除后界面仍显示 `0 B`**，而 `0 B` 会被读成"文件是空的"。
- 两个秘密扫描器的缺陷，都是"工具说没问题，而它其实什么也没查"。
- `test_workspace_layout.py` 把运行时目录硬编码成 `.runtime`，测的已经不是它声称的东西。

## 4. 兼容性与升级

**数据库迁移：`a1e5c7b9d204`。** 从 2.5.0 升级不需要手工步骤 —— 启动时的 schema guard
会在版本落后且 head 可达时**先备份再前向迁移**，迁移后回读确认。

**用户数据不会被卸载删除。** 数据在 `%LOCALAPPDATA%\AI Workbench Data`，不在程序目录里。
卸载程序删的是程序，不是你的工作。

**密钥不在数据库里。** AI Provider 的 Key 在运行时的配置文件（`ai-config.json`）里，
所以数据库 dump 和备份里没有它。这是有意的，理由见 `SECURITY.md`。

### 升级前请备份

```powershell
# 数据目录（PostgreSQL 数据文件、上传的文档、日志）
Copy-Item "$env:LOCALAPPDATA\AI Workbench Data" "$env:USERPROFILE\Desktop\awb-backup" -Recurse

# 数据库逻辑备份（如果装了 psql）
& "$env:LOCALAPPDATA\AI Workbench Data\..\vendor\runtime\pgsql\bin\pg_dump.exe" `
  -U aiworkbench -d aiworkbench -F c -f "$env:USERPROFILE\Desktop\awb-backup\aiworkbench.dump"
```

### 回滚

1. 卸载 3.0.0（**不要**勾选"删除用户数据"）。
2. 装回 2.5.0。
3. 如果迁移已经跑过，用升级前的 dump 恢复：`pg_restore -U aiworkbench -d aiworkbench -c <dump>`。

**迁移是前向的。** 2.5.0 的代码不认识 3.0.0 的 schema，所以回滚 = 恢复备份，
不是"把文件换回去"。

---

## 5. 安装

### Windows 安装包

`AI-Workbench-Native-Setup.exe`（见第 7 节的校验和）。它自带 PostgreSQL / Redis /
Qdrant 的 Windows 二进制，以项目作用域的进程启动 —— **不装 Windows 服务，不改系统设置**。

安装包作为 GitHub Release 资产分发，**不进 Git 历史**（119 MB 的二进制在历史里
会让 clone 变得没有意义）。

### 从源码运行

```bat
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r backend\requirements-dev.txt
.venv\Scripts\python.exe desktop-native\main.py
```

`backend\requirements-dev.txt` 会带上 `desktop-native\requirements.txt`（PySide6），
所以一条命令就够 —— **测试套件也需要它**，有两个测试模块导入桥接层。

---

## 6. 已知问题

按影响排序。前两条是**发布候选**而不是最终版的原因。

| # | 问题 | 影响 | 规避 |
|---|---|---|---|
| 1 | **安装包尚未在第二台机器上验证安装/启动/升级** | 无法声称"安装路径已端到端验证"。本机打包与包内自检（10/10）通过，但那是构建机器 | 安装到一台干净 Windows 机器上按第 4 节升级流程走一遍 |
| 2 | **GitHub Actions 尚未实际运行过** | CI 文件已提供且命令与本地一致，但"本地通过"不等于"CI 通过" | 首次 push 后看 Actions 结果；README 里因此**没有**徽章 |
| 3 | 真实桌面截图未做 | 界面**内容**有 108 条投影断言覆盖，**字形**没有。构建环境为无头，`offscreen` 报告零个字体族，用它判断字形会得出错误结论（上一版的 emoji 豆腐块就是这么漏过去的） | 在有字体的机器上人工看一眼 |
| 4 | `code` 类产物不运行检查 | 验证里有一条 `unverified`：「本次没有运行任何编译或测试，不对代码正确性下结论」。**这是如实报告，不是缺口掩盖** | 自行运行项目测试 |
| 5 | 没有「导出」 | 这个构建没有文档转换器。做一个转不了格式的按钮就是做一个假功能 | 用系统程序另存为 |
| 6 | `ai_runs.total_tokens` 对自主运行不写入 | 运行记录里的 token 数会低估 agent 运行。**不影响**真实模型测试的计量（那些从 API 的 `usage` 字段读） | — |
| 7 | 仅 Windows | 安装包、捆绑运行时、全部验收都在 Windows 上。Qt/QML 本身跨平台，但**没有跨平台声明，也没有对应验证** | — |
| 8 | 集成测试需要机器本地的基础设施 | 三个引擎要跑起来、`pg_dump` 要在 `PATH` 上、Qdrant 若设了 key 则要放在 `.runtime/qdrant-api-key.txt`。README 的 Tests 一节列了这三样 | 按 README 补齐；**应用本身不需要这些就能构建和运行** |

**未纳入的候选功能**（不是缺陷，是没做）：多智能体协作、插件市场、远程服务、
浏览器自动化、WebView、第二套运行时。

---

## 7. 构建物

安装包路径、大小与 SHA-256：见 `docs/release/BUILD_AND_INSTALL.md`。

---

## 8. 推送（需要你授权）

**本仓库没有任何东西被 push 过。** 要公开发布，请自己执行：

```bash
# 1. 确认工作区干净、测试通过（见 docs/release/TEST_REPORT.md）
git status --short
git log --oneline -5

# 2. 创建远端（把 <your-org> 换成你的账号）
git remote add origin https://github.com/<your-org>/ai-workbench.git

# 3. 推送分支与 tag
git push -u origin feat/private-ai-employee
git push origin v3.0.0-rc.1

# 4. 在 GitHub 上创建 Release，把安装包作为 asset 上传
#    （不要 git add 安装包 —— 119 MB 的二进制属于 Release，不属于历史）
```

**推送前请再跑一次秘密扫描**，确认你本地的改动没有引入凭据：

```bash
python tools/scan_for_secrets.py
python tools/scan_git_history_secrets.py
```

`docs/release/SECURITY_RELEASE_CHECK.md` 记录了截至 3.0.0 的扫描结果：
全部 Git 历史（1877 个 blob / 63.5 MB）干净，无需轮换任何凭据。
