# 在这台机器上无法完成的验收 —— 以及怎么补上

> 这份是给**你**看的，不是给下一个对话看的。
> 结论一句话：**东西都做出来了，但有一部分验收在这台机器上做不了，因为 Windows 的
> Smart App Control 把开发环境和第三方编译模块拦住了。**

---

## 一、被挡住的是什么

**Smart App Control**（智能应用控制）按**文件信誉**判定能不能运行，不看路径也不看程序名。
实测对照：

| 解释器 | 能不能跑 |
|---|---|
| `vendor\runtime\python\python.exe`（随包，新下载的） | ✅ 能跑 |
| `.venv\Scripts\python.exe`（开发用的） | ❌ 被阻止 |
| 那个 venv 的原件（`.workbuddy-ai\binaries\...`） | ❌ 被阻止 |

**同一个 Python：原件被拦、新下载的能跑、复制出来的被拦。**

即使随包解释器本身放行，它的**第三方编译模块**也被拦：

```
ImportError: DLL load failed while importing _pydantic_core:
应用程序控制策略已阻止此文件。
```

所以准确的说法是：**开发环境这条路径断了，不是整个项目都不行。**

---

## 二、⚠️ 关于关闭 Smart App Control

**从"开启"切到"关闭"是不可逆的。关掉之后只能重装 Windows 才能再打开。**

所以我**不建议**你关它，也没有尝试任何绕过（改注册表、改策略、给二进制签名）——
那些要么没用，要么会把这台机器的安全状态弄得更糟。

**推荐做法：换一台机器补验收。** 见下面第四节。

---

## 三、这台机器上无法完成的验收（逐条）

| # | 验收项 | 为什么做不了 |
|---|---|---|
| 1 | 后端全量测试（438 个） | 需要 `import pydantic` → 编译模块被拦 |
| 2 | 安装程序：启动应用 | 需要启动进程 |
| 3 | 安装程序：原生窗口 / 无浏览器 / 无 5173 | 同上 |
| 4 | 安装程序：首次启动完成数据库初始化 | 同上 |
| 5 | 安装程序：注册并登录 | 同上 |
| 6 | 安装程序：强杀窗口后四个服务退出 | 同上 |
| 7 | 安装程序：卸载干净 | `Uninstall.exe` 被拦（WinError 4551） |
| 8 | 首次启动的进度可见性 | 同上 |
| 9 | 第 2 步及以后（开局对话、空账号隔离、Skill 层…） | 全部需要跑起来 |

**这些一律标 `NOT VERIFIED`，不标 PASS，也不写"应该没问题"。**

---

## 四、换一台机器怎么补

### 需要什么

- 一台 Windows 10/11 x64，**Smart App Control 处于关闭或未启用状态**
- 建议 8 GB 内存、5 GB 空闲磁盘

**前置条件分两种情况，别混：**

```
用【安装包】装   → 不需要任何前置。
                   Python / PostgreSQL / Redis / Qdrant 全都自带，
                   双击 exe 就行。

用【源码包】跑   → 需要 Node.js。
                   frontend/dist 不在 git 里（frontend/.gitignore 排除了它），
                   必须自己构建；start-desktop.bat:88 会尝试 npm run build，
                   那台机器没有 Node.js 就会失败。
                   Python 与原生运行时不用管，scripts\fetch-runtime.ps1 会物化。
```

**一句话：安装包什么都要，源码包要 Node.js。**

### 步骤

```
1. 把这两样拷到新机器：
     build\installer\AI-Workbench-Setup.exe        （92.6 MB，安装包）
     archive\releases\AI-Workbench-e9df30f-final.zip（7.0 MB，源码包）

2. 校验没拷坏（可选但推荐）：
     certutil -hashfile AI-Workbench-Setup.exe SHA256
   应等于：
     4a9d1d63c48c41c8707f56df5946c5b4532f6e1d2c1c761f1bc41d478965070c

3. 双击 AI-Workbench-Setup.exe
     · 选安装位置（默认 %LOCALAPPDATA%\AI Workbench）
     · 完成后勾选「立即启动」

4. 等它初始化（第一次要一两分钟：建数据库、建表、灌模板）
   界面会显示进度；失败会给出原因和日志路径

5. 注册一个账号，走一遍开局对话
```

### 补验收要跑的命令

解压源码包后，在项目根目录：

```powershell
# 后端测试（需要能跑 Python）
.\.venv\Scripts\python.exe -m pytest tests\ -q

# 安装程序九条验收（会真的安装、启动、卸载）
.\.venv\Scripts\python.exe scripts\verify_installer.py --apply

# 安装包静态自检（不需要跑进程，本机也能跑）
.\vendor\runtime\python\python.exe scripts\verify_installer_static.py

# 搬家实验（证明不依赖开发目录）
.\.venv\Scripts\python.exe scripts\verify_relocation.py --apply
```

### 在新机器上准备开发环境

```powershell
# 1. 物化原生运行时 + 随包 Python（一条命令，约 5 分钟）
powershell -ExecutionPolicy Bypass -File scripts\fetch-runtime.ps1

# 2. 建开发用 venv（只有跑测试需要）
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend\requirements-dev.txt
```

---

## 五、这台机器上**已经验过**的部分（不用重做）

| 项 | 结果 |
|---|---|
| 搬家实验（不依赖开发目录） | **18/18 PASS** |
| pgsql 裁剪后仍可用 | **13/13 PASS** |
| 安装包静态自检 | **41/41 PASS** |
| 源码包归档自检 | **PASS**（无凭据、无运行时产物、条目齐全） |
| payload 优化 | 1,204.9 MB → 439.7 MB；安装包 92.6 MB |
| 后端测试（SAC 生效前跑的） | 438 个全通过 |
| 安装验收第 1~7 步（SAC 生效前跑的） | PASS，含强杀窗口后四端口全释放 |

---

## 六、一句话

**能交的已经交出来了：安装包和源码包都在，静态检查全绿，搬家实验和裁剪验证都是真跑过的。
差的是"装上以后能不能用"这一段——那需要一台不被 Smart App Control 拦住的机器。**
