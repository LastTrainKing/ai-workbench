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
  [PASS   ]  7. 打包后的应用能启动并自检通过 — 11 UI checks passed in 17.8s (exit 0)
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

| 项 | 值 |
|---|---|
| 路径 | `build/installer/AI-Workbench-Native-Setup.exe` |
| 版本（文件资源） | `3.0.0` |
| 大小 | **124,990,171 字节**（119.2 MB） |
| SHA-256 | `7f4597c05e21284035b4668121a9a44e142e3e069ff7070bac01da010c73d9c9` |
| 构建时间 | 2026-10-15 00:58 |
| 对应源码 commit | **`a2288eb`** —— payload 内容最后变化的提交。**它不是 tag 指向的那个提交**，两者不同是正常的（见 §3.1） |
| payload | 485.3 MB / 3949 个文件（含 `vendor/runtime` 三个引擎） |
| 原生应用包 | `desktop-native/dist/AI-Workbench-Native/` |
| 包内自检 | **10/10**（对**组装后的 payload** 跑，不是对 `dist/`） |

### 3.1 版本对应关系怎么核

§16 明列"安装包与源码 commit 对不上"是发布闸门。核法分两半：**git 能证明的**，
和**git 证明不了的**。

**先分清两个"提交"，混起来会得出错误结论：**

| | 是哪个 | 什么时候变 |
|---|---|---|
| **安装包对应的提交** | `a2288eb` | 只在**会进包的内容**变化时变 —— 应用源码、`README.md`、`VERSION`、`LICENSE`、五个 `scripts/*.ps1` |
| **tag `v3.0.0` 指向的提交** | 发布时的 HEAD | 每次为发布改文档都会变 |

安装包构建于 `a2288eb` 的树；此后每个提交只动了 `docs/`、`RELEASE_NOTES.md`、
`CHANGELOG.md`、`tools/` —— **没有一样进 payload**，所以安装包仍然对应 `a2288eb`，
而 tag 跟着发布往前走。**这两个值不同不代表"对不上"。**

**git 能证明的那一半**（payload 里的受跟踪文件）：

```bat
:: 1. 工作区干净
git status --short                       :: 空

:: 2. 安装包对应的 commit 与 HEAD 之间，payload 里的受跟踪文件没有变过
git diff --stat 7d1c38b HEAD -- README.md VERSION LICENSE scripts
                                         :: 空

:: 3. 安装包内嵌的版本号
(Get-Item build\installer\AI-Workbench-Native-Setup.exe).VersionInfo.FileVersion
                                         :: 3.0.0
```

**git 证明不了的那一半：应用树本身。**

payload 里最大的东西是 `desktop-native/dist/AI-Workbench-Native/` —— PyInstaller 的产物，
里面装着 exe 与整个后端。它被 `.gitignore` 的 `dist/` 规则覆盖（`.gitignore:38`），
**不在版本控制里**，所以对它做 `git diff` 只会输出空：看起来"一致"，其实什么都没比。

这不是假设。2026-10-14 构建的一个 3.0.0 安装包里就装着这样的树：`dist/` 构建于 **10:14**，
而把版本号改成 3.0.0 的提交在 **10:37**。`_internal/VERSION` 里写的是 **2.5.0**，
而应用读版本号读的正是这个文件（`awbnative/resources.py::version()` 先找
`bundle_root()/VERSION`）。于是同一个安装包里有三个版本号：exe 资源 `3.0.0`、
安装根目录 `VERSION` `3.0.0`、**应用自己显示 `2.5.0`**。
抓到它的是包内自检的**第 5 项**，不是 `git diff`。

对这一半，用下面两条 —— 它们至少能证伪：

```bat
:: 4. 应用树里的版本号必须等于仓库根的 VERSION
type desktop-native\dist\AI-Workbench-Native\_internal\VERSION
                                         :: 3.0.0

:: 5. 对组装后的 payload 跑包内自检 —— 第 5 项做的就是第 4 步的比较
python desktop-native\verify_native_package.py --app build\native-installer-payload
                                         :: 10/10
```

### 3.2 已知缺口：应用树的出处无法机械证明

**这一条不是"检查通过"，是"检查不了"。**

`dist/` 的产物里**没有记录它是从哪个 commit 构建的**，所以"这个 exe 是从当前源码构建的吗"
这个问题**目前无法机械回答**。能说的只有三件事：构建时工作区是干净的、版本号对得上、
自检 10/10。三者都能被绕过，只是绕过它们需要刻意为之。

**更早一版的这一节给过一条"应用树构建时间必须晚于最后一次提交时间"的检查。那条规则是错的，
已删除。** 先构建、后提交是正常节奏 —— 2026-10-14 就是这样：`dist` 19:26 构建、19:37 提交。
那条规则会对一个**正确**的产物报 FAIL，然后教会读者"这条检查不用管"，
而那比没有检查更糟。

**要真正关掉它**，需要在构建时把 commit 写进产物（例如
`dist/AI-Workbench-Native/_internal/BUILD_COMMIT`，内容为 `git rev-parse HEAD`，
工作区脏时加 `-dirty` 后缀）。**本版没有做**，所以它留在这里作为一个已知缺口，
而不是一条看起来在检查的规则。

payload 的显式清单是 `scripts/build_native_installer.py` 里的 `INCLUDE` 列表：  
`desktop-native/dist/`、`vendor/runtime/{pgsql,redis,qdrant}`、五个 `scripts/*.ps1`、  
`VERSION`、`README.md`、`LICENSE`。**清单之外的东西一律不进包** —— 陈旧 payload 会发布  
上一个版本的产品，而没有任何东西会说。

**安装包不进 Git 历史。** 119 MB 的二进制进历史会让 clone 失去意义；它是 GitHub  
Release 资产（§18）。

---

## 4. 安装与启动验证

### 4.1 包内自检（自动，已执行）

见 §2.1 的第 7–10 项：打包后的应用**真的被启动过**，在净化过的环境里（无宿主机  
Python、无开发机的 `.runtime`），完成 **11 项** UI 自检并以 0 退出；首次运行创建了用户数据  
目录；**3949 个 payload 文件在运行后逐字节未变**。

**第 11 项是 2026-10-15 加的，它回答的是这个自检以前答不了的问题。** 前面十项看的是
"窗口起来了、QML 没错、服务能连上" —— 而「重新验证」按钮坏着的时候，这十项**全是绿的**，
因为它坏在一条只有用户会走到的路径上（见 `TEST_REPORT.md` §6.3）。第 11 项
（`main.py::_reverify_check`）在**应用自己内部**造一个真实交付物、走那个按钮调的同一个桥接槽、
读回验证证据 —— 所以**每一个安装包在构建时都会自己证明这条路是通的**，
不需要模型、不需要人按按钮、不需要另一台机器。

### 4.2 安装、启动、退出、卸载 —— **已执行**（2026-10-14 12:02–12:15）

§17 要求"新构建、安装、启动成功"。这一轮在本机真的走了一遍，**并且顺带抓到一个缺陷**。

本机原装的是 **2.5.0**，用户数据 241.7 MB（2179 个文件）。开始前先备份：

```
%LOCALAPPDATA%\AI Workbench Data
  -> %LOCALAPPDATA%\AI Workbench Data.backup-20261014-1202
2179 个文件 / 241.7 MB，2.4 秒，逐项比对一致
```

| # | 步骤 | 结果 |
|---|---|---|
| 1 | **卸载 2.5.0**（`Uninstall.exe /S`） | 退出码 0，8.4 秒。注册表项已移除，开始菜单已移除。**数据目录完好：2179 个文件，`pgdata/PG_VERSION` = 16** |
| 2 | **安装 3.0.0**（`/S`） | 退出码 0，22.5 秒。`VERSION` = `3.0.0`，注册表 `DisplayVersion` = `3.0.0`，开始菜单目录与桌面快捷方式都在，`data-dir.txt` 仍指向原数据目录，数据目录仍是 2179 个文件 |
| 3 | **启动** | 主进程 `AI-Workbench-Native` 起来（221 MB），并**自己拉起了捆绑的三个引擎**：PostgreSQL（6 个进程）、Redis、Qdrant。端口 5432 / 6379 / 6333 全部监听 |
| 4 | **它认出了原有数据** | 启动日志：`Infrastructure READY (4.1s total)`，其中 PostgreSQL 一行是 `schema 0.1s (already set up)` —— 它打开的是已有数据库，不是重新初始化 |
| 5 | **查库确认** | 用**安装版自带的 psql** 查它自己的库：36 张表齐全，`alembic_version` = `a1e5c7b9d204`（与代码 head 一致），`users` = 2 —— 卸载/重装没有丢任何一行 |
| 6 | **退出** | `CloseMainWindow` → 主进程退出；**捆绑的三个引擎全部停止，三个端口全部释放** |
| 7 | **卸载 3.0.0**（修复后的卸载程序） | 退出码 0。`cache` 从 **159 个文件 → 0**；安装目录只剩 `.env` 一个文件；数据目录仍是 2179 个文件；注册表已移除 |
| 8 | **重新安装** | 退出码 0。`VERSION` 3.0.0、注册表 3.0.0、开始菜单与桌面快捷方式都在、数据目录 2179 个文件 |

**为什么"卸载后只剩 `.env`"是对的**：它保存着这次安装的 `SECRET_KEY`，删掉它会让用户
重装后读不出自己的数据。安装脚本里对此有明确注释，静默模式下也是这么做的。

**但这个说法有一个前提，2026-10-14 在干净机器上被指出来了**：`.env` **不在安装包
payload 里**（见 §2.2），干净安装时它从来没被创建过 —— 它只出现在**覆盖安装/升级**
场景（旧版安装器留下的）。所以：

- 干净安装后卸载 → 程序目录被**整个移除**，什么都不剩（这比剩一个孤零零的 `.env` 更干净）；
- 覆盖安装后卸载 → 程序目录只剩 `.env`。

本节的第 7 步属于后者（本机原装过 2.5.0）。**把"目录被删干净"当成缺陷是错的**，
反过来把"只剩 `.env`"当成普遍现象也是错的。

**这一步抓到的缺陷**：卸载脚本的删除清单里没有 `cache`（157 个文件的 QML 编译缓存，
由应用在首次运行时创建，安装器从不写它 —— 正因为如此才没人想到要列它）。
更糟的是，因为它没被删掉，最后那句 `RMDir "$INSTDIR"` 也静默地什么都没做，
于是**整个程序目录留在磁盘上**。已修（`b71fe46`），并重建了安装包。

**一个额外的发现**：验证过程中我为了腾出端口停掉了开发栈（否则 5432 被占用）。
不停的话，安装版会打印 `runtime: already up` 并**直接使用开发栈的三个引擎**，一个自己的
进程都不启动 —— 因为它判断"基础设施是否就绪"的方式是**端口有没有人应答**
（`desktop-native/awbnative/runtime.py:302`），而不是"那是不是我的引擎"。

凭据不同时会明确报错；但本项目的开发栈与安装版用的是同一套凭据和同一个库名，
所以那一次它静默地连到了开发库上。已写进 `RELEASE_NOTES.md` 已知问题第 9 条。
**表里第 3 行那次启动是端口空闲时跑的**，所以它测的是"安装版能不能自己把引擎拉起来" ——
那是这一行要回答的问题。

**这一步也说明了一件事**：包内自检（§2.1 的 10 项）证明不了安装路径。
它跑的是打包出来的 exe，不是安装出来的产品 —— 上面 8 步里的每一步，它都测不到。

### 4.3 交付包的安装验证 —— 从 zip 里取出安装包再装一次（2026-10-14 20:07–20:12）

§4.2 那一轮用的是**更早一次的构建**（125,012,100 字节）。系统代理缺陷修完之后安装包
重建了（124,990,171 字节，见 §3），所以安装路径必须对新构建再走一遍 ——
否则 §4.2 证明的是一个已经不存在的产物。

这一轮刻意**从交付出去的 zip 里取出安装包**，而不是用构建目录里的那个文件：

| # | 步骤 | 结果 |
|---|---|---|
| 1 | 从 `AI-Workbench-3.0.0-交付包.zip` 取出 `installer/AI-Workbench-Native-Setup.exe` | 124,990,171 字节，SHA-256 `7f4597c0…d9c9` —— 与 §3 记录的一致 |
| 2 | 停掉开发栈（腾出 5432 / 6379 / 6333） | 三个端口全部释放 |
| 3 | **卸载旧的 3.0.0**（`/S`） | 退出码 0；**安装目录只剩 `.env` 一个文件**（`cache` 那个修复生效；`.env` 来自更早的覆盖安装，见 §4.2 的说明）；数据目录 **2180 个文件完好** |
| 4 | **安装新构建**（`/S`） | 退出码 0，27.7 秒。**四处版本号第一次全部一致**：根 `VERSION` `3.0.0`、**应用树 `_internal/VERSION` `3.0.0`**（旧安装这里是 `2.5.0`）、`exe` 文件版本 `3.0.0`、注册表 `DisplayVersion` `3.0.0` |
| 5 | **启动** | 主进程起来，**自己拉起捆绑的三个引擎**；启动日志 `Infrastructure READY (14.2s total)`，其中 PostgreSQL 一行是 `schema 0.2s (already set up)` |
| 6 | **看窗口** | **没有「降级 / 部分功能不可用」横幅**（那正是系统代理缺陷的症状）；底部显示 `AI 服务: deepseek-flash，可以调用云端模型。` |
| 7 | 数据目录 | 2180 个文件，自始至终未变 |
| 8 | **退出** | 主进程退出；捆绑的三个引擎停止；端口释放；开发栈已恢复 |

**一处要说清楚**：界面上「还没有员工」是**正确的**，不是数据丢失 —— 安装版自己的库
（与开发栈是两个独立的 PostgreSQL 集群）里本来就是 `users=2 / employees=0`，
而且数据目录的文件数在卸载前后**完全一样**（2180）。

**第 4 步是 §4.2 抓不到的**：§4.2 那一轮安装包的 `_internal/VERSION` 写的是 `2.5.0`，
只是当时没有人查这一处。查它的方法现在写在 §3.1 第 2 步。

### 4.4 第三轮的安装验证 —— 安装包重建之后（2026-10-14 23:41–23:45）

第二轮验收报告指出，回环豁免只挂在两个进程入口上，而判定语义检索的工具两个都不经过
（详见 `TEST_REPORT.md` §6.1）。豁免因此下沉到 `app.core.config`，**payload 变了**，
安装包重建（124,990,171 字节，SHA-256 `7f4597c0…d9c9`），安装路径必须对新构建再走一遍。

| # | 步骤 | 结果 |
|---|---|---|
| 1 | 停掉开发栈（否则安装版会认领它的服务 —— 已知问题第 9 条） | 5432 / 6379 / 6333 全部释放 |
| 2 | **覆盖安装**（旧 3.0.0 → 新 3.0.0，`/S`） | 退出码 0，23.5 秒 |
| 3 | 四处版本号 | 根 `VERSION` `3.0.0`、**应用树 `_internal/VERSION` `3.0.0`**、exe 资源 `3.0.0`、注册表 `DisplayVersion` `3.0.0` |
| 4 | 数据目录 | **2180 个文件未变** |
| 5 | **启动** | 主进程起来，**自己拉起捆绑的三个引擎**；日志 `Infrastructure READY (4.2s total)` |
| 6 | **看窗口** | **没有降级横幅**；底部 `AI 服务: deepseek-flash，可以调用云端模型。`（本机装了 Key，走的是"已配置"那一支） |
| 7 | 包内自检（对组装后的 payload） | **10/10**，`qdrant: PASS` |
| 8 | 退出 | 主进程退出；捆绑引擎停止；端口释放；开发栈已恢复 |

**界面文案的改动（§6.2 那两条）不在这一轮的截图上**，因为本机配了 Key，走不到"未配置"
那一支。它的验证方式是**投影断言**：`verify_projection.py` 注入未配置状态、读**渲染出来的
那句话**，断言它不说「功能可用」、且说清写不出交付文件。见 `TEST_REPORT.md` §4。

### 4.5 第四轮的安装验证 —— 修掉「重新验证」按钮之后（2026-10-15 00:58–01:00）

第三轮报告指出桥接层按位置传参调用了一个关键字专用的服务方法（见 `TEST_REPORT.md` §6.3），
修的是 `desktop-native/awbnative/bridge.py` —— **在 payload 里**，所以安装包重建
（124,990,171 字节，SHA-256 `7f4597c0…d9c9`），安装路径再走一遍。

| # | 步骤 | 结果 |
|---|---|---|
| 1 | 停开发栈（腾出 5432 / 6379 / 6333） | 三个端口全部释放 |
| 2 | 覆盖安装（`/S`） | 退出码 0 |
| 3 | 四处版本号 | 根 `VERSION` / 应用树 `_internal/VERSION` / exe 资源 / 注册表 **全部 `3.0.0`** |
| 4 | 数据目录 | **2180 个文件未变** |
| 5 | 启动 | 自己拉起捆绑的三个引擎；`Infrastructure READY (3.8s total)` |
| 6 | 看窗口 | **没有降级横幅**；底部 `AI 服务: deepseek-flash，可以调用云端模型。` |
| 7 | 包内自检（对组装后的 payload） | **10/10**，`qdrant: PASS` |
| 8 | 退出 | 主进程退出；捆绑引擎停止；开发栈已恢复 |

**一处诚实的边界**：这一轮的修复是「重新验证」按钮，而**本机安装版的库里
`deliverables = 0`**（9 次运行、没有交付物），所以那个按钮**在这台机器上按不到**。
它由两条自动断言覆盖 —— 一条驱动真实槽、断言调用约定（旧代码下报 `assert (item,) == ()`），
一条断言界面路径 —— 见 `TEST_REPORT.md` §6.3。**在真机上按下它，是下一轮的活。**

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
| 5b | 测试 | **是**。补齐 §5.4 那三样后：**1526 passed / 0 failed / 11 skipped**（13:26） |
| 6 | 启动原生桌面端 | **是**。`desktop-native/main.py` 在无头模式下启动并持续运行 45 秒被超时终止，无 QML 错误 |
| 7 | 无 Key 场景的清晰提示 | **是**（干净克隆里没有 `.runtime/ai-config.json`，即未配置状态） |
| 8 | 受控的真实模型端到端 | **是**。真模型、真文件，**11/11 通过**（62 秒），见 §5.6 |
| 9 | 构建 Windows 安装器 | **是**，见 §3 |
| 10 | 安装、启动、退出、升级 | **`NOT RUN`**，见 §4.2 |

### 5.2 这一步抓到了四个真实缺陷

**没有一个是开发机上看得到的**，四个都是"本地全绿、换台机器就红"的形态：

1. **`desktop-native/verify_projection.py` 在 Python 3.11 上解析不了。** 一处 f-string
   用了嵌套转义引号（3.12 语法）。项目声明 `requires-python = ">=3.11"`，
   ruff 报 6 处 `invalid-syntax`。
2. **CI 的 ruff 命令会失败。** `--config backend/pyproject.toml` 让 `per-file-ignores`
   的 `tests/**` 不再匹配 —— CI 报 131 个错误，本地 0 个。
3. **README 让新用户跑不起来测试。** 测试套件里两个模块导入 `awbnative.bridge`
   （需要 PySide6），而依赖文件里没有它 —— 测试在**收集阶段**就死了，一个都没跑。
4. **测试套件依赖三个 README 没写的机器本地前提**（Qdrant 的 key、`pg_dump`、
   项目自己的 `.env`），缺一个就是 29 条失败。

详见 `TEST_REPORT.md` §5.2。**这四条是这一轮干净克隆唯一的产出，而它们是这一轮里
价值最高的产出** —— 前三条各自会让一个新用户在第一分钟就放弃。

### 5.3 一处诚实说明

Qt 相关的验收工具（`tools/check_qml_pages.py`、`tools/check_layout.py`）在克隆里是用
**克隆自己的 `.venv`** 跑的（第 2 步装出来的那个），不是开发机的。
第 1 步之前还额外核对过：`scripts/sync_version.py --check` 在克隆里 6 个文件全部一致，
`tools/scan_for_secrets.py` 在克隆里未发现真实凭据。

### 5.4 补齐前提之后

第一次跑是在**什么都没补**的状态下，结果 **29 failed / 1493 passed / 13 skipped**。
29 条逐条追溯到三个机器本地前提（Qdrant 的 key、`pg_dump`、项目自己的 `.env`），
其中只有一个是真的测试缺陷 —— 见 `TEST_REPORT.md` §5.2。

按 README 新增的那一节补齐三样之后：

| | 通过 | 失败 | 跳过 | 耗时 |
|---|---|---|---|---|
| 开发机 `.venv`（那次对照用的运行） | 1532 | 0 | 5 | 15:05 |
| 开发机 `.venv`（**最新**，含新增 4 条 `test_local_net.py`） | **1537** | **0** | **5** | 14:47 |
| **干净克隆（补齐后）** | **1526** | **0** | **11** | 13:26 |

**数字对得上**：1532 + 5 = 1537，1526 + 11 = 1537（同一时刻的两次运行，总数相等）。
后来新增了 4 条 `test_local_net.py`，所以最新一次开发机全量是 1537 / 0 / 5。差的 6 条
正是真实模型 live 测试 ——
干净克隆里没有 Key，它们按设计跳过（每条 skip 都带着原因）。**没有一条失败被藏进跳过。**

### 5.5 未执行的部分（`NOT RUN`）

* **真实模型端到端** —— 见 §5.6，已执行。
* **把安装包装到系统上并启动。** 需要一台干净机器，见 §4.2。
  这是唯一一条会改变"可发布"判断的缺口。

### 5.6 第 8 步：干净克隆里的真实模型端到端 —— **11/11 通过**

§15 第 8 步要求"配置测试 Key 后，仅运行一次受控的真实模型端到端：创建 Markdown 文件、
登记产物、校验、显示、打开/预览"。在克隆里做了，**Key 只从环境变量进来**
（`DEEPSEEK_API_KEY`，解析顺序的第三层），克隆里**没有** `ai-config.json`：

```bash
cd <clone>
export DEEPSEEK_API_KEY=<测试 Key>          # 不落盘、不进任何文件
export AI_WORKBENCH_DATA_DIR=<clone>/.runtime
export DATABASE_URL=...aiworkbench_clean_e2e
./.venv/Scripts/python.exe desktop-native/verify_employee_delivery.py
```

数据库也是克隆自己建的：新建空库 → `alembic upgrade head` 跑完 10 个迁移 → 库内 0 张表起步。

结果：

```
终态: COMPLETED（62 秒）      状态序列: EXECUTING → COMPLETED
新增交付物: 1 个（原有 0 个）
  --- report-local-first ---
      路径: <clone>/.runtime/workspace/report-local-first.md
      类型: md   大小: 1011 字节   验证: True   版本: 1
      验证证据: {"checks": [{"name": "file_exists", "outcome": "pass", ...},
                            {"name": "encoding",   "outcome": "pass", "detail": "UTF-8"},
                            {"name": "content",    "outcome": "pass", ...}]}
      开头: # 本地优先（local-first）软件为什么对个人用户重要
员工状态: idle（不是卡在「工作中」）
结果：11/11 通过
```

**这一条同时证明了三件只有合起来才成立的事**：干净克隆能真的跑完一次交付；
**版本二做的按类型验证在真实链路上生效**（`encoding` / `content` 两条新检查出现在证据里，
不是 `file_exists` 一条了）；以及退出后员工被正确释放回 `idle`。

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
