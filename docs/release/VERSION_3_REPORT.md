# 版本三报告：公开发布准备与发布候选

§13–§19 要求。日期：2026-10-14　分支 `feat/private-ai-employee`
**只写已证实的。未执行的标 `NOT RUN`。**

---

## 1. 实际完成

| 产出 | 性质 |
|---|---|
| `SECURITY.md` | **新增**。漏洞报告渠道 + 密钥处理原则 |
| `.github/workflows/ci.yml` | **新增**。ruff + 后端全量测试（真实 PG / Redis / Qdrant service container） |
| `.github/workflows/windows-installer.yml` | **新增**。**仅手动触发**的安装包构建 |
| `tools/`（4 个工具 + README） | **新增目录**。原先在 `build/rc/`，而 `build/` 被 gitignore —— 干净克隆里没有它们 |
| `docs/release/TEST_REPORT.md` | **新增**。§20 要求 |
| `docs/release/BUILD_AND_INSTALL.md` | **新增**。§20 要求 |
| `RELEASE_NOTES.md` | **新增**。§20 要求 |
| `README.md` | 补上缺失的一节：原生桌面应用才是产品，Web 栈是旧路径 |
| `CONTRIBUTING.md` | 补上界面验收命令与"先让断言失败"的规则 |
| `THIRD_PARTY_NOTICES.md` | 更正一处**不实声明**（见 §3） |
| `CHANGELOG.md` | `[3.0.0]` 段 |
| `VERSION` + 6 个文件 | `2.5.0` → `3.0.0`，经 `scripts/sync_version.py` |
| `desktop-native/requirements.txt` | **新增**。PySide6 —— 测试套件需要它，而依赖文件里一直没有 |
| `backend/tests/integration/test_permissions_effective.py` | 新增 `project_env_file` 夹具，不再要求一个被 gitignore 的文件必须存在 |
| `desktop-native/verify_projection.py` | 修掉一处 3.12 才合法的 f-string（该文件在 3.11 上根本解析不了） |
| `.github/workflows/ci.yml` | 删掉会让 `per-file-ignores` 失效的 `--config` |
| `installer/ai-workbench-native.nsi` | 卸载删除清单补上 `cache` —— 漏掉它会让整个程序目录留在磁盘上 |
| `README.md` | 补上原生桌面一节、一条命令装依赖、以及测试所需的三样机器本地前提 |

### 1.1 干净克隆：这一轮最大的产出

§15 要求的干净克隆不是走过场，它抓到 **4 个"本地全绿、换台机器就红"的缺陷**，
其中 3 个会让一个新用户在第一分钟就放弃：

| # | 缺陷 | 换台机器时的表现 |
|---|---|---|
| 1 | `verify_projection.py` 用了 3.12 的 f-string 语法 | Python 3.11 上**根本解析不了** |
| 2 | CI 的 `ruff --config` 让 `per-file-ignores` 失效 | CI 报 131 个错误，本地 0 个 |
| 3 | 依赖里没有 PySide6，而 README 让人直接跑 `pytest` | 测试在**收集阶段**就死了，一个都没跑 |
| 4 | 测试套件依赖三个没写进 README 的机器本地前提 | 29 条失败，症状读起来像产品 bug |

修完之后，干净克隆：**一条命令装依赖 → 从零跑完 10 个迁移 → 全量测试 1526 passed /
0 failed / 11 skipped → 原生桌面端启动成功**。

### 1.2 交付前最后两个缺陷

打包并对**组装后的 payload** 做包内自检时发现的。两个都只有对着真正要发出去的那个产物
才看得见：

| # | 缺陷 | 表现 |
|---|---|---|
| 5 | **本地服务被发给了系统代理** | Windows 上 `getproxies()` 不读 `ProxyOverride`，于是 `127.0.0.1:6333` 进了代理。横幅报「向量检索降级」而 Qdrant 一直健康；`AsyncQdrantClient` 同样被拦 —— **语义检索静默退化成关键词检索** |
| 6 | **安装包里的应用自称 2.5.0** | 打包用的 PyInstaller 树比版本号提交早 23 分钟，而应用读的正是树里的 `_internal/VERSION` |

第 5 条的范围：本机留下的 **92 份包内自检报告，每一份**都报 Qdrant 被阻塞。
修法见 `TEST_REPORT.md` §6。修完之后自检从 **9/10 → 10/10**，`qdrant: PASS`。

第 6 条暴露了发布流程本身的一个洞：`desktop-native/dist/` 被 gitignore，
所以 `BUILD_AND_INSTALL.md` §3.1 原先那条 `git diff` **对应用本身什么都没比** ——
它一直是空的，看起来却像"一致"。已改成三步可执行的核对。

### 1.3 提交

| commit | 内容 |
|---|---|
| `d77d039` | release: version three — the repository a stranger can actually build |

---

## 2. §17 最终验收清单

| 检查项 | 必须状态 | 证据 | 实际 |
|---|---|---|---|
| Git 工作树 | 干净，或已明确哪些内容不提交 | `git status` | **干净**（安装包与 `build/` 在 gitignore 中，属"明确不提交"） |
| 源码提交 | 发布 commit 可识别 | commit SHA | **`d77d039`** |
| 密钥扫描 | 当前树及历史均有结果 | 扫描命令、命中复核 | **当前树无真实凭据（84 处形状命中均为变量名赋值）；历史 1877 个 blob / 63.5 MB 全扫，干净，无需轮换** |
| 后端全量测试 | 真实执行并报告通过/失败/跳过 | 测试日志摘要 | **开发机 1537 passed / 0 failed / 5 skipped（14:47，含新增 4 条）；干净克隆 1526 / 0 / 11（13:26）** |
| 投影/桌面测试 | 关键断言通过 | 测试输出、截图 | **投影 108/108 · shell 31/31 · 桥接 18/18 · 布局 0 问题 · QML 20/20**；**真实桌面截图 7/7**（`tools/shot_real_pages.py`，1280×860 真实窗口，存 `screenshots/native/`） |
| 真模型端到端 | 真实执行一次且产物可见 | 脱敏验收记录 | **6 条 live 测试 6 passed（5 次调用 / 1275 tokens）**；`verify_live_acceptance.py` 10/11 |
| 干净克隆 | 按文档可复现 | 命令与结果 | **是**（见 `BUILD_AND_INSTALL.md` §5）：新建 venv + **一条命令**装依赖 + 新建空库 + 从零跑完 10 个迁移 + lint + 全量测试 1526/0/11 + **原生桌面端启动成功**。**真实模型端到端 11/11 通过**（62 秒，真模型真文件，见 `BUILD_AND_INSTALL.md` §5.6） |
| 数据库迁移 | 新库与升级库分别验证 | 迁移测试报告 | **空库从零跑完 10 个迁移**（head `a1e5c7b9d204`）；**安装版启动时报告 `schema already set up`**，即它认出并复用了既有库；升级路径由 `test_schema_forward_upgrade.py` 覆盖 |
| Windows 安装器 | 新构建、安装、启动成功 | 文件、SHA-256 | **全部执行**：124,998,399 字节，SHA-256 `e4fbe535…47bf`，`FileVersion 3.0.0`，包内自检 10/10；**卸载 2.5.0 → 装 3.0.0 → 启动 → 退出 → 卸载 → 重装** 八步全部通过，数据 2179 个文件自始至终完好 |
| 许可证 | 本项目与分发依赖核实 | License / notices | **MIT（仓库既有）**；捆绑组件 PostgreSQL License / BSD-3-Clause / Apache-2.0 / LGPLv3，均已核实可再分发 |
| 文档 | 安装、使用、测试、限制齐全 | 文档路径 | **齐全**，见 §5 |
| Git tag | 指向最终发布 commit | tag SHA | **`v3.0.0`**（annotated） |
| 发布说明 | 与实际能力一致 | `RELEASE_NOTES.md` | **是**，含 7 条已知问题 |

---

## 3. 发布阻断

| 级别 | 项 | 根因 | 影响 | 是否阻断 GitHub 发布 |
|---|---|---|---|---|
| ~~**P0**~~ | ~~安装包落后于源码~~ | **已解除**：重建于 `d77d039` 的树，`FileVersion 3.0.0`，包内自检 10/10 | — | **否** |
| ~~P1~~ | ~~`build/rc/` 不在版本控制~~ | **已解除**：四个工具移入 `tools/` 并纳入版本控制 | — | **否** |
| ~~P1~~ | ~~干净克隆不可复现~~ | **已解除**：见 §4。干净克隆现在能从零装依赖、跑迁移、跑全量测试（1526/0/11）并启动应用。**顺带抓到四个只有换台机器才看得见的缺陷**，全部已修 | — | **否** |
| ~~P1~~ | ~~安装包未在第二台机器上验证~~ | **已解除**：在本机完整走了 卸载 → 安装 → 启动 → 退出 → 卸载 → 重装 八步，见 `BUILD_AND_INSTALL.md` §4.2。**并抓到一个卸载残留缺陷**（`cache` 未被清理，连带整个程序目录留下），已修 | — | **否** |
| **P1** | **GitHub Actions 未实际运行** | 本机没有 GitHub runner | CI 文件已提供但未验证。**不过它的命令现在被逐条跑过了** —— 干净克隆那次正是照它执行的，并因此发现了 `--config` 那个会让 CI 首跑即红的错误 | **否**（已明说，README 无徽章） |
| ~~P1~~ | ~~真实桌面截图未做~~ | **已解除**：新增 `tools/shot_real_pages.py`，真实显示器 7/7 页，存 `screenshots/native/`，README 已换成原生客户端的截图 | — | **否** |
| P2 | 根目录有 40 余份历史报告 | 历次会话的产物 | 公开仓库看起来杂乱 | 否 |
| P2 | 测试夹具的 `sk-…` / `AKIA…` 持续触发扫描告警 | 有意为之 | 每次扫描需人工复核 | 否 |
| P2 | 运行时二进制下载**没有校验和验证** | 脚本里没有 `Get-FileHash` | 一次被劫持的 TLS 或上游替换无法检测 | 否 |
| P2 | **安装版会认领占用端口的别的服务** | `runtime.ensure()` 的就绪判定只看端口是否应答（`desktop-native/awbnative/runtime.py:302`）。端口被别人占用时会连上去；凭据相同则**静默使用另一个数据库** | 正常用户机器上这三个端口是空的，所以影响面小；但开发机同时跑开发栈与安装版时必然撞上 | **否** —— 已写进 `RELEASE_NOTES.md` 已知问题第 9 条。修法（让判定核对 `pgdata/postmaster.pid`）属于改动启动路径，按 §13「版本三冻结功能范围」留到下一版 |
| P2 | `desktop-native/**` 未用项目规则集 lint | 它没有自己的 ruff 配置 | 套用项目规则会报 100 个问题（本轮未修，§4.4 说不要中途换标准） | 否 |
| P2 | `requirements` 是范围不是锁 | `ruff>=0.5,<1.0` 等 | 换台机器可能装到不同版本。本次两边结论一致，但那是验证过的巧合 | 否 |

**§16 的七条禁止发布项逐条对照：**

| 禁止项 | 是否触发 |
|---|---|
| 真实凭据进入 Git 历史或 Release 资产且未处理 | **否**（历史干净） |
| 任何 P0 未解决 | **否** |
| 关键用户数据存在不可恢复风险 | **否**（数据在 `%LOCALAPPDATA%`，卸载不删；迁移前备份） |
| 用户要求文件交付却可能在无产物时显示成功 | **否**（`decide_final_status` 14 条测试 + 穷举不变式） |
| 关键权限/账户隔离测试失败 | **否**（346 passed） |
| 启动、迁移或卸载可能破坏用户数据且无恢复说明 | **否**（`RELEASE_NOTES.md` §4 给了备份与回滚步骤） |
| 最终干净全量测试没有实际执行且被报告成通过 | **否**（1537 passed 是真实执行的结果） |
| 安装包与源码 commit 对不上 | **否**（核法见 `BUILD_AND_INSTALL.md` §3.1） |

---

## 4. 本版本构建物

| 项 | 值 |
|---|---|
| 安装包 | `build/installer/AI-Workbench-Native-Setup.exe` |
| 大小 | **124,998,399 字节** |
| SHA-256 | `e4fbe535c747e464d1e19a6df21e3db05dbf0f9224b1e91c5cb34a00460047bf` |
| 版本资源 | `3.0.0` |
| 对应 commit | `996ebe9`（payload 内容最后变化的提交；tag 指向的是发布 commit，两者不同是正常的） |
| 原生应用包 | `desktop-native/dist/AI-Workbench-Native/`，256 MB，包内自检 10/10 |

---

## 5. 最终交付物清单（§20 对照）

| 要求 | 路径 | 状态 |
|---|---|---|
| `README.md` | 根目录 | 已更新（补上原生桌面一节） |
| `LICENSE` | 根目录 | **仓库既有**（MIT）。见 §6 |
| `CHANGELOG.md` | 根目录 | 已加 `[3.0.0]` |
| `CONTRIBUTING.md` | 根目录 | 已更新 |
| `SECURITY.md` | 根目录 | **新增** |
| `.env.example` | 根目录 | 已核实：只含变量名与空值/占位符 |
| `.gitignore` | 根目录 | 已核实：覆盖 `.env` / `.runtime` / `*.key` / `vendor/*.zip` / `build/` |
| `docs/release/RELEASE_BASELINE.md` | | 已更新（工具路径与断言数） |
| `docs/release/SECURITY_RELEASE_CHECK.md` | | 已更新（扫描器路径） |
| `docs/release/TEST_REPORT.md` | | **新增** |
| `docs/release/BUILD_AND_INSTALL.md` | | **新增** |
| `docs/release/VERSION_1_REPORT.md` / `VERSION_2_REPORT.md` / `VERSION_3_REPORT.md` | | 齐 |
| `RELEASE_NOTES.md` | 根目录 | **新增** |
| 第三方许可证清单 | `THIRD_PARTY_NOTICES.md` | 已更正并补充"随安装包分发"一节 |
| GitHub Actions | `.github/workflows/` | **新增** 2 个 |
| 结果契约文档 | `docs/product/RUN_OUTCOME_CONTRACT.md` | **新增**（版本二） |
| 可复现的源码与测试 | `backend/tests/`、`desktop-native/verify_*.py`、`tools/` | 齐 |
| 对应发布 commit 的安装器 | `build/installer/` | 已构建，作为 Release Asset（**未推送**） |

---

## 6. 许可证：核实过程与结论

§14 要求"先核实当前 License 和第三方组件的许可义务；**不得因为 DeepSeekBot 是 MIT 就
推断 AI Workbench 自身必须采用 MIT**；若仓库尚无许可证，提出最合适的选择并记录理由；
除非用户已授权采用特定许可证，否则不要擅自加上与项目意图不符的许可"。

核实结果：

* **仓库本来就有一份 `LICENSE`（MIT，`Copyright (c) 2026 AI Workbench contributors`）。**
  它不是本轮加的。**本轮没有改动它** —— 改许可证不是工程决定，是所有者决定。
* 分发依赖的许可义务（见 `THIRD_PARTY_NOTICES.md` §三）：PostgreSQL License、
  BSD-3-Clause、Apache-2.0、LGPLv3、PSF-2.0 —— **全部允许以 MIT 许可的项目再分发**，
  且都不要求本项目改变自身许可。
* **LGPLv3 是唯一需要注意的一条**：PySide6 以动态链接方式使用、未修改 Qt 源码。
  LGPLv3 因此要求保留版权声明、允许用户替换 Qt 库、提供许可证全文 —— 三条都满足。
  若将来改为静态链接或修改 Qt 源码，该结论即失效。

**这是一条留给你的决定**：`RELEASE_NOTES.md` 与本文档都没有替你确认许可证选择。
如果项目的意图与 MIT 不符（例如希望专利授权条款），现在是改的时候。

---

## 7. 下一版本范围

**没有"下一版本"。** 按 §2 的要求，三轮交付到此为止。

剩下的工作是**验证**而不是**开发**，按优先级：

2. 首次 push 后看 Actions 结果。它的命令已经被逐条跑过（干净克隆那次就是照它执行的），
   所以首跑失败的概率已经降低，但"没跑过"仍然是"没跑过"。
4. 给 `fetch-runtime.ps1` 加 SHA-256 校验（上游 release 页面提供）。
5. 把 `requirements` 钉到具体版本 —— 现在的范围让"换台机器"和"这台机器"可能装到不同版本。

**不做**：多智能体、插件市场、远程服务、浏览器自动化、WebView、第二套运行时。
