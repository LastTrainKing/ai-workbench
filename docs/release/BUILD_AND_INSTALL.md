# 构建与安装

§15 / §17 要求。日期：2026-10-14　分支 `feat/private-ai-employee`

**只写已证实的。未执行的标 `NOT RUN`。**

---

## 1. 环境

| 项                               | 值                                                                           |
| ------------------------------- | --------------------------------------------------------------------------- |
| 操作系统                            | Windows                                                                     |
| Python                          | 3.13.14（`.venv`）                                                            |
| PySide6 / Qt                    | 6.12.0                                                                      |
| PyInstaller                     | 6.22.3                                                                      |
| NSIS                            | `%LOCALAPPDATA%\tauri\NSIS\Bin\makensis.exe`（Tauri 缓存里的那份，与上一版安装包用的是同一个编译器） |
| PostgreSQL / Redis / Qdrant 二进制 | 由 `scripts/fetch-runtime.ps1` 从官方地址下载到 `vendor/runtime/`                    |



---

## 2. 构建步骤

### 2.1 原生应用包（PyInstaller）

```bat
.venv\Scripts\python.exe desktop-native\build_native.py
```

产物：`desktop-native/dist/AI-Workbench-Native/`（**256 MB**，含 Qt、Python 运行时与 10 个迁移）。

**构建脚本会在打包后自动运行 `verify_native_package.py`，任何一项失败即整体失败。**  
一个"在构建机上能启动、在别处不能"的包会在这里被拦住，而不是在用户的机器上。

本次结果 —— **10/10 通过，0 失败，0 阻塞**：

```
  [PASS   ]  1. 可执行文件已生成 — AI-Workbench-Native.exe, 20.8 MiB
  [PASS   ]  2. Qt 运行时与平台插件已打包 — Qt libraries, Shiboken binding, 4 platform plugins
  [PASS   ]  3. QML 界面与 Qt QML 模块已打包 — 35 interface files and the Qt QML modules
  [PASS   ]  4. Alembic 迁移已打包 — 10 migrations
  [PASS   ]  5. VERSION 与仓库一致 — 3.0.0
  [PASS   ]  6. 包内没有密钥与用户数据 — 742 text files and the executable scanned, nothing found
  [PASS   ]  7. 打包后的应用能启动并自检通过 — 10 UI checks passed in 4.5s (exit 0)
  [PASS   ]  8. 首次运行会创建用户数据目录 — created AI Workbench Data and used it
  [PASS   ]  9. 运行不会写入安装目录 — 2297 files unchanged
  [PASS   ] 10. 不依赖宿主机 Python — ran as AI-Workbench-Native.exe with a sanitized environment
```

第 6 项值得单独说：**它对包内 742 个文本文件和可执行文件逐个扫描，确认没有把开发机  
的密钥或用户数据打进发布物。** 这是 §16 的一条发布闸门，而它是自动化的。

### 2.2 Windows 安装包（NSIS）

```bat
.venv\Scripts\python.exe scripts\build_native_installer.py --build
```

三步，顺序不能换：

1. **payload 从头组装**，用的是显式清单。陈旧 payload 会发布上一个版本的产品，而  
   没有任何东西会说。
2. **编译前先测量。** 485 MB 的 payload 通常要试几次，而"NSIS 装不下"这件事在写完  
   hook 之后才发现是很贵的。
3. **直接调用 NSIS**，不从 PATH 猜。

**不进 payload 的东西**（每一项都有理由，写在脚本 docstring 里）：

- `vendor/runtime/python`（323 MB）—— PyInstaller 自带的解释器就是它，多一份没人启动的
- `vendor/runtime/python-nuget.zip` —— 下载缓存，运行时没有东西读 `.zip`
- `scripts/` 的其余部分 —— 上一代架构的入口（`start-desktop.bat` / Tauri 链）
- `.env`、`.runtime`、`data-dir.txt`、`pgdata` —— 部署的密钥与用户的数据库

### 2.3 版本号

`VERSION` 是**唯一**来源：

```bat
.venv\Scripts\python.exe scripts\sync_version.py           :: 写入所有需要自己那份的文件
.venv\Scripts\python.exe scripts\sync_version.py --check   :: 校验一致，不一致则退出 1
```

`--check` 由测试套件运行（`test_release_freeze_versioning.py`）。这次把 `2.5.0` 提升到  
`3.0.0` 时，它同步了 6 个文件：两个 `.nsi`、`frontend/package.json`、  
`desktop/package.json`、`desktop/src-tauri/tauri.conf.json`、`desktop/src-tauri/Cargo.toml`。

---

## 3. 构建物

| 项           | 值                                                                  |
| ----------- | ------------------------------------------------------------------ |
| 路径          | `build/installer/AI-Workbench-Native-Setup.exe`                    |
| 版本（文件资源）    | `3.0.0`                                                            |
| 大小          | **125,005,709 字节**（119.2 MB）                                       |
| SHA-256     | `56e9812f4dc5bf25b7c30a9511204854f957209e05f9f12764004b86d78c374e` |
| 构建时间        | 2026-10-14 10:36                                                   |
| 对应源码 commit | **`d77d039`**（tag `v3.0.0-rc.1` → `2a420a9`，两者之间**只有文档变化**）        |
| payload     | 485.3 MB / 3949 个文件（含 `vendor/runtime` 三个引擎）                       |
| 原生应用包       | `desktop-native/dist/AI-Workbench-Native/`，256 MB                  |

### 3.1 版本对应关系怎么核

§16 明列"安装包与源码 commit 对不上"是发布闸门。核法：

```bat
:: 1. 工作区干净
git status --short                       :: 空

:: 2. 安装包 payload 里那些文件，与 HEAD 逐字节一致
git diff --stat HEAD -- README.md VERSION LICENSE scripts desktop-native/dist
                                         :: 空

:: 3. 安装包内嵌的版本号
(Get-Item build\installer\AI-Workbench-Native-Setup.exe).VersionInfo.FileVersion
                                         :: 3.0.0

:: 4. tag 与安装包构建的那个 commit 之间，payload 文件没有变过
git diff --stat d77d039 v3.0.0-rc.1 -- README.md VERSION LICENSE scripts desktop-native/dist
                                         :: 空（只改了 docs/）
```

payload 的显式清单是 `scripts/build_native_installer.py` 里的 `PAYLOAD` 常量：  
`desktop-native/dist/`、`vendor/runtime/{pgsql,redis,qdrant}`、五个 `scripts/*.ps1`、  
`VERSION`、`README.md`、`LICENSE`。**清单之外的东西一律不进包** —— 陈旧 payload 会发布  
上一个版本的产品，而没有任何东西会说。

**安装包不进 Git 历史。** 119 MB 的二进制进历史会让 clone 失去意义；它是 GitHub  
Release 资产（§18）。

---

## 4. 安装与启动验证

### 4.1 包内自检（自动，已执行）

见 §2.1 的第 7–10 项：打包后的应用**真的被启动过**，在净化过的环境里（无宿主机  
Python、无开发机的 `.runtime`），完成 10 项 UI 自检并以 0 退出；首次运行创建了用户数据  
目录；**2297 个安装目录文件在运行后逐字节未变**。

### 4.2 安装到系统并启动（`NOT RUN`）

§17 要求"新构建、安装、启动成功"。**本轮没有执行**，理由如下：

- 安装会修改系统状态（注册表、`%LOCALAPPDATA%`、开始菜单），而这是一个正在使用的  
  开发机 —— 上一版的安装验证是在一次专门的会话里做的（`build/rc/check_installed_window.py`）。
- 本轮可做的是**包内自检**，它覆盖了同一批不变式里最要紧的几条：能启动、不依赖宿主  
  机 Python、不写安装目录、首次运行建立数据目录。

**这是一条真实的缺口**，已写进 `RELEASE_NOTES.md` 的已知问题第 1 条。要补齐，需要一台  
干净的 Windows 机器，按下面的步骤走。

### 4.3 待执行的验证步骤

```bat
:: 1. 在干净机器上安装
AI-Workbench-Native-Setup.exe

:: 2. 启动（开始菜单或桌面快捷方式），确认窗口出现、能登录

:: 3. 退出，确认进程与 PostgreSQL / Redis / Qdrant 都停了

:: 4. 升级：装回 2.5.0，再装 3.0.0，确认数据保留、schema 前向迁移

:: 5. 卸载，**不勾选"删除用户数据"**，确认 %LOCALAPPDATA%\AI Workbench Data 还在
```

---

## 5. 干净克隆验证

§15 要求在不依赖开发者的 `.runtime` / `.env` / 缓存 / 全局包 / 现存数据库的干净目录里
按 README 走一遍。

### 5.1 已执行

克隆到 `%TEMP%wb-clean`，在**没有** `.venv`、`vendor/runtime/`、`.runtime/` 的状态下：

| # | §15 的步骤 | 结果 |
|---|---|---|
| 1 | 获取源码 | **是**。`git clone` → `c380f11`（tag `v3.0.0-rc.1`） |
| — | 是否带入 `.env` / `.runtime` / `.venv` / `vendor/runtime` / 安装包 | **全部否** |
| — | 文档引用的 22 个文件是否都在 | **全部存在** |
| 2 | 安装明确版本的依赖 | **是**。`python -m venv .venv` + `pip install -r backend/requirements-dev.txt` → **一条命令**，2 分 53 秒，81 个包 |
| 3 | 初始化一个新的测试数据库 | **是**。`DROP/CREATE DATABASE aiworkbench_test`（表数 0）与 `aiworkbench_clean`（表数 0） |
| 4 | 运行迁移 | **是**。`alembic upgrade head` 在空库上跑完 **10 个迁移**，head `a1e5c7b9d204` |
| 5a | 静态检查 | **是**。`ruff check backend/app backend/tests desktop-native` → All checks passed（ruff **0.17.0**，与开发机的 0.16.10 不同版本） |
| 5b | 测试 | 见 §5.4 |
| 6 | 启动原生桌面端 | 见 §5.4 |
| 7 | 无 Key 场景的清晰提示 | **是**（干净克隆里没有 `.runtime`，即未配置状态） |
| 8 | 受控的真实模型端到端 | **`NOT RUN`**，见 §5.4 |
| 9 | 构建 Windows 安装器 | **是**，见 §3 |
| 10 | 安装、启动、退出、升级 | **`NOT RUN`**，见 §4.2 |

### 5.2 这一步抓到了三个真实缺陷

**没有一个是开发机上看得到的**，三个都是"本地全绿、换台机器就红"的形态：

1. **`desktop-native/verify_projection.py` 在 Python 3.11 上解析不了。** 一处 f-string
   用了嵌套转义引号（3.12 语法）。项目声明 `requires-python = ">=3.11"`，
   ruff 报 6 处 `invalid-syntax`。
2. **CI 的 ruff 命令会失败。** `--config backend/pyproject.toml` 让 `per-file-ignores`
   的 `tests/**` 不再匹配 —— CI 报 131 个错误，本地 0 个。
3. **README 让新用户跑不起来测试。** 测试套件里两个模块导入 `awbnative.bridge`
   （需要 PySide6），而依赖文件里没有它 —— 测试在**收集阶段**就死了，一个都没跑。

详见 `TEST_REPORT.md` §5.2。**这三条是这一轮干净克隆唯一的产出，而它们是这一轮里
价值最高的产出。**

### 5.3 一处诚实说明

Qt 相关的验收工具（`tools/check_qml_pages.py`、`tools/check_layout.py`）在克隆里是用
**克隆自己的 `.venv`** 跑的（第 2 步装出来的那个），不是开发机的。
第 1 步之前还额外核对过：`scripts/sync_version.py --check` 在克隆里 6 个文件全部一致，
`tools/scan_for_secrets.py` 在克隆里未发现真实凭据。

### 5.4 未执行的部分（`NOT RUN`）

* **真实模型端到端。** 干净克隆里没有 `.runtime/ai-config.json`，也就没有 Key。
  第 7 步（无 Key 的清晰提示）是这一条的反面，它做了。
  真实模型验证在开发机上做过，见 `TEST_REPORT.md` §3。
* **把安装包装到系统上并启动。** 需要一台干净机器，见 §4.2。
  这是唯一一条会改变"可发布"判断的缺口。

## 6. 复现命令汇总

```bat
:: 静态检查
.venv\Scripts\python.exe -m ruff check backend/app backend/tests desktop-native

:: 后端全量测试（需要真实 PostgreSQL / Redis / Qdrant）
cd backend && ..\.venv\Scripts\python.exe -m pytest tests -q -p no:randomly --tb=line -rs

:: 界面验收
.venv\Scripts\python.exe desktop-native\verify_projection.py
.venv\Scripts\python.exe desktop-native\verify_native_shell.py
.venv\Scripts\python.exe tools\check_layout.py
.venv\Scripts\python.exe tools\check_qml_pages.py

:: 安全
.venv\Scripts\python.exe tools\scan_for_secrets.py
.venv\Scripts\python.exe tools\scan_git_history_secrets.py

:: 版本一致性
.venv\Scripts\python.exe scripts\sync_version.py --check

:: 构建
.venv\Scripts\python.exe desktop-native\build_native.py
.venv\Scripts\python.exe scripts\build_native_installer.py --build
```
