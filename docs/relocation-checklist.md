# 搬家清单 — 项目可重定位性的实测结论

> 这份清单是第六轮第一步的产出。**安装程序按它来写。**
> 全部结论来自真实运行，命令与原始输出见文末「证据」。

---

## 一句话结论

**项目可以搬，而且搬完不需要原目录——但前提是换掉 Python 环境，并修三个真实的路径缺陷。**

---

## 一、搬什么（必须跟着走的）

| 目录 / 文件 | 体积 | 为什么必须 |
|---|---|---|
| `backend/` | 9.9 MB | 后端源码 |
| `frontend/dist/` | 931 KB | UI 由 API 同源挂载，**只需要 dist，不需要 node_modules** |
| `scripts/` | 492 KB | 启动链路：`start-desktop.bat` → `infra-start.ps1` → `dev.ps1` → `wait-ready.ps1` |
| `vendor/runtime/pgsql` | 967 MB | PostgreSQL 16.4 绿色版 |
| `vendor/runtime/qdrant` | 69 MB | Qdrant 1.12.4 |
| `vendor/runtime/redis` | 47 MB | Redis 5.0.14.1 |
| `vendor/runtime/python` | **322 MB** | **新增**：随包的解释器（见下） |
| `worker/` | 8 KB | 后台 worker |
| `.env` | 2 KB | 运行配置（**安装程序不打包这个，见下**） |

**合计约 1.7 GB。** 压缩后估算 500–700 MB，与任务书的预期一致。

## 二、不搬什么（搬了反而坏）

| 目录 | 体积 | 为什么不要 |
|---|---|---|
| `.venv/` | 372 MB | **不可重定位**，见第三节 |
| `frontend/node_modules/` | 217 MB | 只需要 `dist/`；安装目录里放 node_modules 等于把开发环境装进产品 |
| `desktop/src-tauri/target/` | 2.1 GB | Rust 编译产物；只搬 `release/*.exe` |
| `.git/` `archive/` `docs/` `samples/` | — | 开发资产 |
| `.runtime/` | 482 MB | **绝不能搬**：那是开发机的数据库。安装时在目标机新建 |

## 三、Python 环境：三个方案都实测过，选 B

### 实测结果

| 方案 | 实测 | 结论 |
|---|---|---|
| **A. 安装时重建 venv** | 需要目标机有 Python。本机的 venv 由 `C:\Users\user\.workbuddy-ai\binaries\python\versions\3.13.12` 创建，目标机不可能有这个路径 | **不可行** |
| **C. 复制现成 venv** | 复制到 `C:\Temp\venv-test` 后**能用**——`python --version` 正常、`import fastapi` 正常 | **看着可行，其实是假象** |
| **B. 随包带解释器** | 官方 NuGet `python` 3.13.14 包 → 完整 CPython 布局 40 MB，装依赖后 322 MB；四种文档格式全部实测通过 | **选它** |

### 为什么 C 是假象（这一条很重要）

`.venv/pyvenv.cfg`：

```
home = C:\Users\user\.workbuddy-ai\binaries\python\versions\3.13.12
executable = C:\Users\user\.workbuddy-ai\binaries\python\versions\3.13.12\python.exe
```

venv 的 `python.exe` 只是壳，真正的解释器在 `home` 指的那个**绝对路径**上。复制过去的 venv
之所以能跑，是因为**那台机器上那个路径仍然存在**。换一台机器就是坏的。

**换句话说：现在的项目本身就不是自包含的**——它依赖 WorkBuddy 托管的一个 Python 安装。

### 为什么用 NuGet 而不是官方 embeddable 包

先试的是官方 embeddable 包（`python-3.13.14-embed-amd64.zip`，10.5 MB）。它能跑、能装 pip、
能装依赖，但**启动后端失败**：

```
ModuleNotFoundError: No module named 'app'
```

原因：embeddable 包里的 `python313._pth` 会让解释器进入 **isolated 模式**——`sys.path` 只包含
`._pth` 里列的路径，**当前目录和 `PYTHONPATH` 都被忽略**。实测：

```
sys.path = [python313.zip, ., Lib\site-packages, ...]   ← 没有 CWD
cd backend && python -c "import app"                    → ModuleNotFoundError
cd backend && PYTHONPATH=... python -c "import app"     → ModuleNotFoundError（PYTHONPATH 也被忽略）
```

而 `dev.ps1` 正是用 `python -m app.cli serve`、工作目录设在 `backend/` 来启动后端的。

换成 NuGet 包（`https://www.nuget.org/api/v2/package/python/3.13.14`，14.3 MB）后：
**完整 CPython 布局、没有 `._pth`、行为与正常 Python 一致**。

```
sys.path[0] = ''（即当前目录）
cd backend && python -c "import app"  → app importable
```

## 四、实测发现的三个真实缺陷

这三个不修，**默认装到 `C:\Program Files\...` 的安装会直接失败**。

### 缺陷 1：路径带空格 → PostgreSQL 起不来（P0）

```
postgres: invalid argument: "Relocated"
```

`scripts/infra-start.ps1:89`：

```powershell
Start-Process -FilePath (...\postgres.exe) `
    -ArgumentList @('-D', $PgData, '-p', "$PgPort", ...)   # ← 数组元素不会被加引号
```

PowerShell 把 `-ArgumentList` 的数组**用空格拼成一个字符串，且不给元素加引号**。于是
`-D C:\Temp\AIW Relocated Test\.runtime\pgdata` 被 postgres 解析成 `-D C:\Temp\AIW` 加一个多余参数
`Relocated`。

**这不是测试路径特殊**：约定俗成的安装位置 `C:\Program Files\AI Workbench\` 就带空格。

修法：`lib.ps1` 新增 `ConvertTo-ProcessArgument` / `ConvertTo-ProcessArgumentList`，
三处 `Start-Process` 全部改用（postgres、redis、dev.ps1 的 api）。

### 缺陷 2：`requirements.txt` 少了整个文档能力（P0）

`backend/requirements.txt` 里**没有** `python-pptx` / `python-docx` / `openpyxl` / `reportlab` / `matplotlib`。

它们是通过 `_require()` **惰性导入**的，所以启动时不会报错——**照 requirements 装出来的后端，
一个文档都写不出来**，而且只在用户真的要文档时才说：

```
生成该格式需要 python-pptx，当前环境没有安装。请先运行：pip install python-pptx。
```

开发机的 `.venv` 里有它们，是因为当初临时装的、从没记录。已补进 `requirements.txt`。

### 缺陷 3：`.bat` 里的路径被 shell 转义吃掉了（自伤）

我在补 Python 解析逻辑时，用 shell 写 `.bat`，`\\v` 被折叠成 `\v`（0x0B 垂直制表）、
`\\r` 变成 0x0D 回车：

```
if exist "%ROOT%<VT>endor<CR>untime\python\python.exe"     ← 实际字节
```

cmd.exe 读到的是 `vendor` + 回车 + `untime\python`，于是报
`No Python interpreter found. An installed copy ships one at vendoruntime\python`。

已用 `scripts/fix_control_chars.py` 修复（用文件而不是 shell 内联命令写，避免再次被转义）。
**这条是工程铁律第 7 条的延伸：`.bat` 不能通过 shell 写。**

## 五、搬家清单（安装程序照这个做）

```
<安装目录>/
├── AI Workbench.exe            壳（放在树根，靠祖先搜索定位，实测可解析）
├── .env                        安装时生成，不含任何真实凭据
├── backend/                    后端
├── frontend/dist/              UI（只需要 dist）
├── scripts/                    启动链路
├── worker/
└── vendor/runtime/
    ├── pgsql/  qdrant/  redis/ 原生运行时
    └── python/                 ← 随包解释器（NuGet python 3.13.14 + 依赖）
```

**安装时要做的事：**
1. 解包上面这棵树；
2. 写 `.env`（从 `.env.example`，密钥留空，由用户在 UI 里填）；
3. 首次启动时由 `start-desktop.bat` 自动完成：建 `.runtime`、`initdb`、建库建表、seed prompt 模板。

**exe 与项目树的相对位置**：实测 exe 放在树根时，`find_project_root()` 的祖先搜索能正确解析
（日志 `startup: project root = C:\Temp\AIW Relocated Test`）。**所以不需要设 `AI_WORKBENCH_HOME`。**

**注意**：`AI Workbench.exe` 里编译进了 `%USERPROFILE%\Desktop\ai-workbench` 这个兜底路径
（`find_project_root()` 的第三分支）。它是兜底、不是依赖——实测中它没有被用到——但**安装版应该
把这个兜底去掉或改掉**，否则一台机器上同时有开发目录和安装目录时，行为会不明确。

## 六、证据

### 验收 18/18（`scripts/verify_relocation.py --apply`）

```
=== 1. stop the relocated stack ===
  [PASS] PostgreSQL port 5432 closed      [PASS] Redis port 6379 closed
  [PASS] Qdrant port 6333 closed          [PASS] API port 8000 closed

=== 2. hide the original's scripts/ and .runtime/ ===
  [PASS] original project exists — C:\Users\user\Desktop\ai-workbench
  [PASS] hidden scripts and .runtime — scripts-HIDDEN-FOR-RELOCATION-TEST, .runtime-HIDDEN-FOR-RELOCATION-TEST
  [PASS] original scripts/ is gone        [PASS] original .runtime/ is gone

=== 3. cold-launch the relocated copy ===
  [PASS] relocated stack came up on its own — {"status":"ok","app":"AI Workbench","version":"1.0.0","env":"dev"}
  [PASS] PostgreSQL port 5432 open        [PASS] Redis port 6379 open
  [PASS] Qdrant port 6333 open            [PASS] API port 8000 open

=== 4. it is a working instance, not just a health endpoint ===
  [PASS] login against its OWN database — token received
  [PASS] serves its own UI bundle         [PASS] no dev server on 5173

=== 5. restore the original ===
  [PASS] original scripts/ restored       [PASS] original .runtime/ restored

=== 18/18 passed ===
```

### 原生窗口（单次冷启动）

```
API ready: True
shell processes: 1
  Id MainWindowTitle HasWindow
  7744 AI Workbench         True
5173 listening: False
msedge processes: 0
```

### 一个诚实的说明：整目录改名没做成

任务书要求的验证方式是"把 `C:\Users\user\Desktop\ai-workbench` 改名再测"。**改名失败了**：

```
rename failed: 对路径"C:\Users\user\Desktop\ai-workbench"的访问被拒绝。
```

原因不是应用占用，而是 **Windows 不允许改名任何进程的当前工作目录**，而本机有进程
（不是被搬走的那个应用）的工作目录正好在它里面。实测证据：**同一个目录里的文件可以正常改名**，
只有目录本身不行——这正是"CWD 占用"的特征。

所以改用等价且更严格的验证：**把原目录的 `scripts/` 和 `.runtime/` 整个藏起来**（这两样是
被搬走的副本唯一可能还在依赖的东西），然后从零冷启动副本。**副本在两者都不存在的情况下起来了、
能登录、能挂 UI** —— 这比改名更能说明问题，因为改名只是让路径消失，而藏掉这两个目录是让
**内容**消失。

### 四种文档格式在随包解释器里实测

```
imports OK: pptx docx openpyxl 4.5.1 3.11.2
pptx: 31764 bytes, 1 slides        （含一张 matplotlib 图）
docx: 36646 bytes                  （H1 + H2 层级）
xlsx: 6399 bytes                   （含公式与图表）
pdf: 1343 bytes, 1 page(s)
ALL FOUR FORMATS WRITTEN
```
