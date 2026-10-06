# PROGRESS — 实时断点

> 给下一个接手的对话：读完这一页就能开工，不需要读任何历史报告。
> 每次 commit 之前更新它，让它跟着代码一起提交。

---

## 当前阶段

**第 1 步（共 8 步）：安装程序。**

目标：先把 payload 优化好（剔除 `python-nuget.zip`、裁剪 pgsql 并验证），
再做最小可行安装包——先让它能装能跑，再补首次启动初始化和卸载逻辑。

**核心验收只有一条：删掉 `C:\Users\user\Desktop\ai-workbench` 之后，软件仍然能装、能跑、能用。**

推进顺序（来自 `发给新对话.txt`，不要改）：

```
1 安装程序            ← 当前
2 开局对话 + 第二个空账号验证（8 类数据全为 0）
3 分级检索（先建立"更快"的度量：三种情况的真实毫秒数）
4 Skill 层（本轮核心，从零建：自己去找 → 评估 → 验证 → 你确认 → 导入）
5 知识库自动补充
6 记忆生命周期
7 界面重组
8 全量回归 + 冷启动 + 文档同步 + 交付重建 + 最终报告
```

**硬约束**：第 1、2 步没做完之前不要动第 7 步的界面重组。

---

## 已完成并验证

### 上一轮（第七轮）：搬家实验 + payload 实验

| 项 | 证据 |
|---|---|
| 搬家实验 18/18 PASS | `scripts/verify_relocation.py --apply`；原目录的 `scripts/` 与 `.runtime/` 藏起来后副本仍能冷启动、登录、挂 UI |
| 三项搬迁缺陷已修 | 提交 `351f0bc`：PowerShell 参数不加引号 / `requirements.txt` 缺整套文档库 / venv 不可重定位 |
| payload 体积实测 | `build/payload-report.json`：**1,204.9 MB / 25,620 文件**，ZIP **411.5 MB** |
| payload 实测能跑 | 装到 `%LOCALAPPDATA%\AI Workbench Test` 后冷启动：60s API UP、login 成功、UI 挂载、`HasWindow: True`、自建 `.runtime` 并 initdb 成功 |
| 随包 Python 物化 | `fetch-runtime.ps1` 新增 Python 目标；`vendor/runtime/python` = 322 MB，四种文档格式实测通过 |

---

## 未完成

### 第 1 步（安装程序）—— 具体还差这几件事

1. **payload 优化**：确认 `python-nuget.zip` 等 `vendor/*.zip` 未进 payload（已按设计排除，需复验并记录）；
   裁剪 `vendor/runtime/pgsql` 里运行不需要的部分（`pgAdmin 4/`、`StackBuilder/`、`symbols/`、`include/`、
   `lib/` 中的开发库等）并**验证裁剪后 PG 仍能 initdb + 启动**。
2. **最小可行安装包**：Tauri NSIS `bundle.resources` 带 payload + exe 放树根；先做到"能装、能跑"。
3. **首次启动初始化**：`.runtime` 建立、PG 数据目录初始化、建库建表、seed——进度可见、失败给真实原因与日志路径。
4. **卸载**：先停进程再删目录；用户数据默认保留并询问。
5. **验收九条**：干净路径安装 / 删原目录仍能跑 / 桌面+开始菜单快捷方式指向 exe / 双击 → 原生窗口无浏览器无 CMD 无 5173 /
   首次启动完成初始化 / 能注册登录 / 关窗后四个服务全退 / 卸载干净 / 安装包路径+体积+SHA256。

### 第 2 步及以后

未开始。第 2 步的详细要求见下「已知缺口」。

---

## 下一步

下一个人打开就该做的第一件事，具体到命令：

```powershell
cd C:\Users\user\Desktop\ai-workbench

# 1. 看 payload 里到底装了什么、pgsql 里哪些是可裁的
.\.venv\Scripts\python.exe scripts\build_installer_payload.py
Get-ChildItem build\installer-payload\vendor\runtime\pgsql | Select-Object Name
Get-ChildItem build\installer-payload\vendor\runtime\pgsql\pgsql | Select-Object Name

# 2. 裁 pgsql（保留 bin/ lib/ share/，去掉 pgAdmin 4/ StackBuilder/ symbols/ include/ doc/）
#    裁完必须验证：initdb 成功 + 端口 5432 起来 + psql "SELECT 1" 通过

# 3. 再跑一次 payload 组装，记录新的体积
.\.venv\Scripts\python.exe scripts\build_installer_payload.py --zip
```

---

## 阻塞

无。

> **一处已解决的误判（记录下来，因为它是一个教训）**：我曾在这一节写过"第八轮提示词在本机找不到"。
> 那是错的——`Get-ChildItem | Format-Table` 把中文文件名截断了，我**从截断的显示里得出了"文件不存在"的结论**。
> 用转义码点重新枚举后，文件一直在：`AI-Workbench-第八轮提示词.md`，25,459 B。
> 已复制为 `docs/prompts/08-第八轮.md`。**又是"用推断代替证据"，这次是"用不完整的输出代替证据"。**

---

## 环境备忘

- 仓库：`C:\Users\user\Desktop\ai-workbench`，`vendor/` 与 `build/` 都在 `.gitignore` 里
- 随包解释器：`vendor/runtime/python/python.exe`（322 MB，含全部依赖）
- 原生运行时：`vendor/runtime/{pgsql,qdrant,redis}`
- 一次性物化：`powershell -File scripts\fetch-runtime.ps1`
- 测试：后端 `.\.venv\Scripts\python.exe -m pytest tests\ -q`；前端 `cd frontend && npm test`
- 编码铁律：`.bat` 纯 ASCII 且**不能用 shell 内联命令写**；`.ps1` 带 UTF-8 BOM
