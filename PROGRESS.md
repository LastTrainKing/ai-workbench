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

### 第 1 步 · payload 优化（完成）

| 项 | 优化前 | 优化后 | 证据 |
|---|---|---|---|
| payload 原始 | 1,204.9 MB / 25,620 文件 | **439.7 MB / 8,671 文件** | `build/payload-report.json` |
| 压缩后 | 411.5 MB | **151.3 MB**（ZIP）/ **92.6 MB**（安装包） | `build/installer-report.json` |

砍掉的东西：
- `vendor/runtime/pgsql` 里 **800.1 MB** 与运行无关的目录（`pgAdmin 4` 615.9 MB / `symbols` 155.6 MB /
  `doc` 15.5 MB / `include` 12.4 MB / `StackBuilder` 0.8 MB）→ 919.8 MB 降到 119.7 MB
- `vendor/runtime/*.zip`（下载缓存，13.68 MB，同一份东西出现两次）

**裁剪已验证**：`scripts/verify_pgsql_trim.py` **13/13 PASS** —— 在临时集群上（端口 55432，
不碰开发库）跑 initdb → 启动 → pg_isready → `SELECT 1` → create/insert/count → 干净停止。
裁剪后 payload 也做过完整冷启动：PG → Redis → Qdrant → API 全 READY、health 200、UI 200、登录成功。

### 第 1 步 · 安装程序（已构建；验收 29/30，剩一个卸载缺陷）

| 项 | 值 |
|---|---|
| 安装包 | `build/installer/AI-Workbench-Setup.exe` |
| 体积 | **92.6 MB** |
| SHA256 | `346a6153d8536586f039eb1364a5a64b39ff364cff0dd4d859044139b508cdd0` |
| 压缩比 | 443.1 MB → 92.6 MB（20.9%） |
| 构建 | `python scripts/build_installer.py --build` |
| 验收 | `python scripts/verify_installer.py --apply` |

**已 PASS 的（29 条里的大部分）**：干净路径安装 8,673 文件 · 桌面+开始菜单快捷方式指向 exe ·
原生窗口 · API healthy · 无 5173 · 无新增浏览器进程 · 自建 `.runtime` 并 initdb ·
挂载 UI · 注册全新账号 · **强杀窗口后 4 个端口全部释放** · 卸载器存在 ·
安装目录被清除 · 快捷方式被清除

**唯一 FAILED：卸载时的「保留用户数据」路径。**

现象：卸载器把 `.runtime` 移到 `%LOCALAPPDATA%\AI Workbench Data` 这一步失败，
于是安装目录也删不掉（新加的 `Abort` 守卫生效了，这是**安全的**——宁可不卸载也不丢数据）。

已经排除的原因：
- 不是 MessageBox 卡住（三处 MessageBox 都加了 `IfSilent` 分支）
- 不是静默模式没生效（第一次跑时删数据分支执行了，说明 `IfSilent` 判断是对的）

**当前代码状态是安全的**：`Rename` 失败时 `Abort`，安装目录和 `.runtime` 都原样保留，
并弹出提示告诉用户数据在哪。**没有丢数据的可能。**

**下一步要查的具体问题**：`Rename "$INSTDIR\.runtime" "$LOCALAPPDATA\...\.runtime"` 为什么失败。
怀疑点（按可能性排序）：
1. `StopEverything` 之后 PostgreSQL 还没释放文件句柄（`taskkill` 后只 Sleep 2000）
2. NSIS `Rename` 对**目录**跨父目录移动的限制
3. `$LOCALAPPDATA` 在卸载器上下文里的取值

**验证方法**：在 `Rename` 前后各加一次 `IfFileExists` + `DetailPrint`，用
`Uninstall.exe /S` 后读 `%TEMP%` 下的 NSIS 详细日志（`/S` 时 `DetailPrint` 不显示，
需要改用 `nsExec::ExecToLog 'cmd /c echo ... >> log'` 落盘）。

---

## 未完成

### 第 1 步还差

1. **修上面那个卸载缺陷**（唯一未通过项）。
2. 首次启动的**进度可见性**尚未单独验证（Tauri splash 是否真的显示初始化进度与失败原因）。

### 第 2 步及以后

未开始（开局对话 + 空账号数据隔离）。规格见 `docs/prompts/08-第八轮.md`
的「能力 A」与第五部分。

---

## 下一步

```powershell
cd C:\Users\user\Desktop\ai-workbench

# 1. 定位卸载时 Rename 为什么失败（见上面「要查的具体问题」）
#    改完重建：约 6 分钟
.\.venv\Scripts\python.exe scripts\build_installer.py --build --skip-payload

# 2. 重跑验收，目标 30/30
.\.venv\Scripts\python.exe scripts\verify_installer.py --apply

# 3. 然后进第 2 步：开局对话 + 第二个空账号
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
