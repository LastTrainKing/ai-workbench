# 测试报告

§20 要求的 `docs/release/TEST_REPORT.md`。日期：2026-10-14
分支 `feat/private-ai-employee`

**每个数字都是本机实际跑出来的。** 未执行的项标 `NOT RUN`，不写"全绿"。

---

## 1. 环境

| 项 | 值 |
|---|---|
| Python | 3.13.14（`.venv`） |
| PySide6 / Qt | 6.12.0 |
| Node | v22.22.2 |
| Rust | 1.98.1 |
| PostgreSQL | 16.4（本地，非容器） |
| Redis | 7（本地） |
| Qdrant | 1.12.4（本地） |
| 迁移 head | `a1e5c7b9d204`（开发库与测试库一致，只读核验） |
| 平台 | Windows（`QT_QPA_PLATFORM=offscreen` 用于无头界面验收） |

**基础设施是真实的，不是 stub。** 这套测试的价值有一半来自"真的能连上
PostgreSQL / Redis / Qdrant"，换成假实现等于在测另一个产品。

---

## 2. 后端全量测试

### 2.1 最近一次干净全量（版本三，提交见 §10）

```
cd backend && ../.venv/Scripts/python.exe -m pytest tests -q -p no:randomly --tb=line -rs
```

```
1537 passed, 5 skipped, 5 warnings in 887.50s (0:14:47)
```

**0 failed。** 命令与版本一完全相同（`--tb=line -rs`，无 `-x`，未跳过任何文件）。
比上一轮多 4 条 —— 新增的 `backend/tests/unit/test_local_net.py`，即 §6 那个系统代理
修复的测试。skipped 从 11 降到 5，因为**那 6 条真实模型 live 测试不再跳过** —— 它们的
Key 读取方式改为走应用自己的 resolver（见 §3）。

**这一次是用 `no_proxy` 直连跑的，原因值得记下来。** 本机注册表里配着系统代理
（`ProxyEnable=1`，`ProxyServer=127.0.0.1:7890`）而代理客户端当时没开，于是**出网完全
不通**。同一条命令在那种状态下是 **5 failed**，全部是依赖出网的测试。把出网恢复之后
它们全绿 —— **判断"是不是回归"之前，先确认出网是通的**：

```bash
# 出网通不通，两行就能看出来
python -c "import httpx; print(httpx.get('https://api.deepseek.com/', timeout=8).status_code)"
```

版本一（提交 `0f9468f`）的同一条命令是 **1489 passed, 0 failed, 11 skipped（10:19）**。
两次之间的差异 = 6 条 live 测试 + 34 条本轮新增测试 + 版本一致性测试的若干参数用例。

**第一次全量曾经跑出 `1 failed, 1488 passed`**，那唯一的失败只在全量里出现：

```
tests/unit/test_workspace_layout.py:34
assert WindowsPath('.../.runtime/test-runtime/workspace') == WindowsPath('.../.runtime/workspace')
```

根因：为隔离测试的 AI 配置把 `AI_WORKBENCH_DATA_DIR` 指到了 `.runtime/test-runtime`，
而这条断言把运行时目录**硬编码**成 `.runtime`。于是它测的不再是 docstring 里写的
"默认位置在产品内、不在桌面"，而变成了"目录必须叫 .runtime"。

**分组复跑没有发现它**，因为当时只重跑了受影响的三个文件。修法不是改数字让它变绿：
断言改为从 `runtime_root()` 推导，两边问同一个来源。

### 2.2 版本二新增测试

| 文件 | 数量 | 结果 |
|---|---|---|
| `tests/unit/test_artifact_checks.py` | 12 | passed |
| `tests/integration/test_run_outcome.py` | 8 | passed |
| `tests/unit/test_build_run_terminal_status.py` | 14 | passed |

### 2.3 版本二改动后的回归

| 范围 | 命令 | 结果 |
|---|---|---|
| 交付相关（4 个文件） | `pytest test_verification_separation test_deliverable_open test_release_freeze_ownership test_employee_run_binding` | **346 passed, 4 skipped**（3:43） |
| 构建执行器（3 个文件） | `pytest test_employee_run_binding test_run_settlement test_run_reconciliation` | 18 passed |
| 全部新增 | 见 §2.2 | 34 passed |

### 2.4 5 个 skip，逐条有原因

| 数量 | 位置 | 原因 |
|---|---|---|
| 4 | `tests/api/test_release_freeze_ownership.py:79` | `deliberately public` —— 有意跳过 |
| 1 | `tests/unit/test_employee_layer.py:75` | 空白岗位就是让用户从零定义，不要求预填合同 |

**版本一的时候这里还有 6 条**：`tests/integration/test_deepseek_live.py` 因为测试进程
只读环境变量、读不到 Key 而跳过。版本二把它们的 Key 读取方式改为走应用自己的
resolver，于是它们**真的运行了** —— 见 §3。

### 2.5 全量测试会真的调用模型

修好之后，`pytest tests` **包含 6 次真实模型调用**。这不是副作用，是有意的：
那 6 条测试存在的意义就是证明"真实的 Provider 能用"，把它们排除在全量之外等于
每次发布都少证明一件事。

代价：一次全量会消耗少量额度。若不想花，用 `--deselect` 或只跑分组 ——
但**不要**把它们的 skip 说成通过。

## 3. 真实模型测试

### 3.1 六条 live 测试

```
cd backend && ../.venv/Scripts/python.exe -m pytest tests/integration/test_deepseek_live.py -q -p no:randomly -rs
```

```
6 passed
```

**6 passed / 0 failed / 0 skipped。** 计量：**5 次真实模型调用，1275 tokens**
（从 API 返回的 `usage` 字段读出，不是从本地计数估算）。

修复前的状态是"6 skipped"，原因不是缺 Key —— Key 配在 `.runtime/ai-config.json`，
而测试只读环境变量，而环境变量在解析顺序里排在**第三层**，低于安装级配置文件。
选定的修法是**路径 A**：测试读应用自己的 `runtime_config.resolve()`。
路径 B（把 Key 抄进 `.env`）会让一个密钥有两个副本可互相不一致 —— 那正是缺陷的成因。

### 3.2 真实端到端验收

`desktop-native/verify_live_acceptance.py`：**10/11 通过**。
唯一未过项是脚本自己读错了字段名（应为 `payload["message"]["content"]`），
不是产品缺陷，已修。

`desktop-native/verify_employee_delivery.py`：真实员工 + 真实模型 + 真实文件，
文件生成、登记、带验证证据。

### 3.3 本轮（版本二）没有重跑真实模型

版本二的改动是投影层与验证层，不改变模型调用路径。数字取自
`LIVE_MODEL_TEST_REPORT.md`，未重复花费。

### 3.4 真实模型测试发现的最重要的一件事

`_persist_run` **从来没有执行过**。

提交 `591fa2e` 里我写的是 `run.started_at` —— `AIRun` 没有这一列（它属于 `ToolCall`）。
`AttributeError` 被 `try/except` 吞掉，整个函数跳过。**什么都没失败**：运行照样
`COMPLETED`（构建执行器读内存里的 controller），交付物照样登记。唯一痕迹是一行日志，
而结构化字段不渲染。

数据证据：改动后 `finished_at=None`，改动前 2026-10-11 那条有值。

**那个想让成功有结束时间的提交，正是拿掉结束时间的提交。** 已修，并补了缺失的断言
`test_persisting_a_result_writes_the_finish_time`。

---

## 4. 界面验收

| 项 | 命令 | 结果 |
|---|---|---|
| 投影层回归 | `desktop-native/verify_projection.py` | **108/108**（版本一为 58/58） |
| Shell 验收 | `desktop-native/verify_native_shell.py` | **31/31** |
| 桥接层实测 | `desktop-native/verify_bridge_employees.py` | 18/18 |
| 布局审计 | `tools/check_layout.py`（19 页 × 3 档分辨率） | **0 问题，0 QML 错误** |
| QML 页面加载 | `tools/check_qml_pages.py` | 20/20 |

### 4.1 投影层断言（108 条）的三组

| 组 | 数量 | 覆盖 |
|---|---|---|
| `mapping` | 69 | `theme.js` 的纯映射：合同取值与优先级、中文标签、交付词汇表、验证三态摘要、普通/专家模式导航 |
| 渲染 | 19 | 无头加载真实 `Main.qml`，读 `isVisible()` 与 `text`：员工合同 12 节、首页 `providers.llm` 绑定、导航栏按模式重渲染 |
| 交付卡片 | 20 | 注入与后端同形的数据，断言画出来的内容 |

**为什么断言渲染树而不是源码字符串**：上一轮出现过两次"测试全绿，用户看到错误" ——
首页把 `summary.providers.llm` 读成了 `summary.llm`（写错字段名不抛异常，只让首页
对着一个配好 Key 的用户说"未配置"），以及字体回退链缺失导致图标渲染成豆腐块。
两次都不是逻辑错误，是**投影错误**：数据对，画错了。

### 4.2 断言做了变异测试

一条从未失败过的断言不算证据。逐条注入真实缺陷验证：

| 注入 | 失败断言数 |
|---|---|
| `summary.providers.llm` 改回 `summary.llm`（上一轮真实 bug） | 3 |
| 删掉一个合同章节 | 4 |
| 合同永远不可见 | 3 |
| 所有产物按钮强制可点（忽略 `enabled`） | 2 |
| 产物类型直接渲染 `kind` 而不走 `kindLabel` | 1 |

**一次没有产生变化的变异什么也证明不了，而它看起来和"测试没用"一模一样** ——
第三次变异第一次没失败，但错的是变异本身（在组件内部加 `visible:false` 不生效，
因为实例化处的绑定会覆盖组件自己的值）。

### 4.3 新断言抓到的真实缺陷（版本二）

第一次跑交付卡片渲染组时失败：**文件已被删除的产物仍然渲染出 `0 B`**。
`file_state.size_bytes` 在文件不存在时是 0，而"0 B"会被读成"文件是空的" ——
另一件、更糟的事。已改为文件缺失时省略大小，并补了一条断言钉住它。

### 4.4 一条偶发失败，以及它暴露的写法问题

跑完干净克隆那次 15 分钟的全量测试之后，紧接着跑投影层，得到 **93/94**，
失败信息是"员工列表为空，无法验证投影" —— 十四条断言因此被跳过。

重复跑两次都是 108/108，所以它**是偶发，不是回归**。但它不是"机器慢"这么简单：
脚本里写的是 `_pump(4500)`，即"等 4.5 秒，数据库应该回来了"。**那是对数据库延迟的
一个猜测**，而猜测在被占满的机器上会错。

已改成 `_pump_until(predicate, timeout_ms)`：轮询直到条件成立或超时。
**这不是把断言放宽** —— 名册始终不来时，超时照样到期、检查照样失败；
变的只是"条件成立就不必再等"。首页那段同样的 4000 ms 也一并改了。

理由写进代码里了：**一个偶发失败的验收测试比一个慢的测试更糟**，
因为它教会读者"重跑一下"而不是"去看一眼"。

### 4.5 真实桌面截图 —— **已执行**（2026-10-14 12:2x）

新增 `tools/shot_real_pages.py`：在**真实显示器**上加载 `Main.qml`、逐页导航、
等桥接层的在途请求归零、抓窗口。7/7 页成功，1280×860 逻辑分辨率（设备像素 1600×1075），
存进 `screenshots/native/`，README 已引用。

**为什么非做不可。** 之前所有的界面验收都跑在 offscreen 平台上，而该平台在本机报告
*零个字体族* —— 它能证明布局，**不能证明字形**。这个项目已经因此漏过一次：
员工头像首字改成 emoji，offscreen 报渲染干净，真实窗口画的是豆腐块。
"抓图是这类改动唯一的验证手段"这句话在本仓库的 memory 里出现过两次。

**看到的东西**（都是人眼看的，不是断言）：

| 页面 | 结论 |
|---|---|
| 工作台 | 问候语、在岗员工数、输入框与示例、最近成果 5 条（含一条「待确认」）、左侧 13 项普通模式导航，全部正确 |
| 我的员工 | 6 个岗位模板卡片（工具数、默认格式）、6 名员工（角色、版本、工具数、使命、权限说明、岗位合同入口），头像圆标**渲染为字形而非豆腐块** |
| 交付物 | **三态验证摘要真的在屏上**：「2/2 项检查通过」与「0/1 项检查通过，1 项未通过」；固定宽度操作列（查看/预览/打开/继续修改/重新生成）对齐无错位 |
| 记忆 | 三节（待确认 / 生效记忆 / 模型此刻会被告知什么）与上下文预览都在 |
| 知识库 / 设置 / Agent | 渲染正常，无空白面板、无重叠 |

**两次我自己误判、随后撤回的"缺陷"**（记下来，因为差点变成假报告）：

1. 状态栏那句「AI 服务：deepseek-flash，可以调用云端模型。」放大 5 倍后看着像
   上半截被切掉。**不是**：读 QML 对象的真实属性 —— `lineCount = 1`、
   `height = implicitHeight = 15`、`clip = false`、`width 904 > implicitWidth 235`。
   没有任何裁切。11px 的中文字形在设备像素上只有 8 行，LANCZOS 放大 5 倍会把细笔画糊成
   看起来"断了"的样子。
2. 记忆页标题看着像"模型**此别**会被告知什么"。**不是**：源码是
   `MemoryPage.qml:329` 的「模型**此刻**会被告知什么」，我把「刻」看成了「别」。
3. Agent 页目标输入框的占位文字看着像被切掉上半截。**不是**：把那一块单独裁出来放大
   4 倍，字是完整的。

**三条都是同一个原因，值得单独写下来**：这些图是 1600×1075，而查看器会把它们缩到
一千出头再显示 —— 一行 13px 的中文缩完只剩约 9px，笔画糊在一起，看起来就像被裁了。
**判断这类截图必须先把可疑区域裁出来放大，再下结论。** 反过来说，这也意味着
"截图人工检查"这条流程本身要写清放大倍数，否则它会稳定地产生假缺陷 —— 而一个假缺陷
在报告里的破坏力和一个漏掉的缺陷一样大。

**一条留下的观察**（不是缺陷，是判断）：交付物页那条 `0/1 项检查通过，1 项未通过`
的行，文件其实已经存在了 —— 它的 `verification` 是**某次验证的快照**，而界面没显示
这个快照是什么时候取的。见 `RELEASE_NOTES.md` 已知问题第 10 条。

本环境为无头（`offscreen`），且该平台报告**零个字体族**，所以它**不能**用来判断
真实桌面的字体渲染 —— 上一轮的 emoji 豆腐块正是这么漏过去的。
投影断言覆盖了内容，不覆盖字形。发布说明中已写明。

---

## 5. 静态检查

```
.venv/Scripts/python.exe -m ruff check backend/app backend/tests desktop-native
```

```
All checks passed!
```

### 5.1 两件必须说清楚的事

**一、规则集覆盖的范围比命令看起来小。**

| 路径 | 用哪套规则 |
|---|---|
| `backend/**` | `backend/pyproject.toml`（`line-length = 100`，`E/W/F/I/B/UP/C4/SIM/RUF`） |
| `desktop-native/**` | **没有自己的配置**，走 ruff 的默认规则集（`E4/E7/E9/F`） |

把项目规则集套到 `desktop-native/` 上会报 **100 个问题**（30 E402、25 RUF100、
24 I001、9 RUF005、4 SIM105、2 SIM112，加 6 处语法）。**本轮没有修它们。**
理由：任务书 §4.4 说"项目已有的工具，不要临时换标准"，而现有标准就是上面这张表。
但这**是一个真实的质量缺口**，写在这里而不是让命令看起来覆盖得更多。

**二、ruff 版本会改变结论，而 `requirements-dev.txt` 是范围不是锁。**

| 环境 | ruff |
|---|---|
| 开发机 `.venv` | 0.16.10 |
| **干净克隆新建的 venv** | **0.17.0**（`ruff>=0.5,<1.0` 取到的最新） |

0.17.0 对当前工作区同样报 `All checks passed!`，两边一致 —— 但这是**验证过的巧合**，
不是保证。要它成为保证，需要把 `requirements-dev.txt` 钉到具体版本。

### 5.2 干净克隆发现的四个真实缺陷

§15 的干净克隆跑出来的，**没有一个是开发机上看得到的**：

1. **`desktop-native/verify_projection.py` 在 Python 3.11 上根本解析不了。**
   一处 f-string 用了嵌套转义引号（3.12 语法）。项目声明 `requires-python = ">=3.11"`、
   `target-version = "py311"`，ruff 报 6 处 `invalid-syntax`。已修（值先算出来再插值）。
2. **CI 的 ruff 命令会失败。** 我原先写的是
   `ruff check --config backend/pyproject.toml …`，还配了一句"这个 flag 是必须的"。
   恰恰相反：`per-file-ignores` 的 glob 相对**配置文件所在目录**解析，从仓库根传
   `--config` 会让 `tests/**` 不再匹配 —— CI 报 131 个错误，本地 0 个。
   已删除该 flag 并就地写明原因。
3. **`README` 让新用户跑不起来测试。** README 说：建 venv、装
   `backend/requirements-dev.txt`、跑 `pytest`。而 `tests/unit/test_ai_config_status.py`
   与 `tests/unit/test_native_bridge_session.py` 导入 `awbnative.bridge`，后者导入
   PySide6 —— **测试在收集阶段就死了**，一个都没跑，报
   `ModuleNotFoundError: No module named 'PySide6'`。
   修法：新增 `desktop-native/requirements.txt`（`PySide6-Essentials>=6.6,<7.0`），
   由 `backend/requirements-dev.txt` 以 `-r` 引入，一条命令覆盖测试与客户端。
4. **测试套件依赖三个"机器本地"的前提，README 一个都没写。** 干净克隆里
   `pytest tests` 的结果是 **29 failed / 1493 passed / 13 skipped**，
   29 条**逐条**追溯到下面三个之一：

| 数量 | 现象 | 根因 | 性质 |
|---|---|---|---|
| 21 | `embedding_error: 所有分块都未能生成向量` | 测试把 runtime 目录搬进克隆，Qdrant 的 key 没跟过来 → `401 Unauthorized` | **环境**，不是产品缺陷 |
| 4 | `SchemaOutOfDateError: 找不到 pg_dump` | schema guard 在前进迁移前要先备份，没有 `pg_dump` 就拒绝。**这个拒绝是对的** | **环境**，文档缺失 |
| 4 | `assert secrets.is_file()` —— "the fixture expects the project's own .env to exist" | `.env` 被 gitignore（这是对的），而测试断言它必须存在 —— 于是**失败**而不是跳过 | **测试缺陷**，已改代码 |

第 4 组是真正的缺陷，**改的是代码不是文档**：新增 `project_env_file` 夹具，在测试期间
创建该文件、结束后删除。跳过的写法会让"没有 `.env` 的机器"少掉四条真断言；改成在临时
目录里断言，测的就不再是实际存在的那条规则（`{PROJECT}\.env` 是**具体路径**不是模式）。
已存在的 `.env` 一律不动。

**四个都是"本地全绿、换台机器就红"的形态**，而 §15 要求的正是换台机器跑一遍。
第 3、4 个尤其说明问题：它们分别是新用户做的**第一件事**和跑测试的**第一个障碍**。

### 5.3 干净克隆的三组数字

| 场景 | 通过 | 失败 | 跳过 | 耗时 |
|---|---|---|---|---|
| 开发机 `.venv`（干净克隆那次对照用） | 1532 | 0 | 5 | 15:05 |
| 克隆 + 一条命令装依赖，**不给任何机器本地配置** | 1493 | **29** | 13 | 15:20 |
| 克隆 + 装依赖 + **按 README 新增的那一节补齐前提** | **1526** | **0** | 11 | 13:26 |

第三组验证的是"README 的步骤到底能不能照做"。补齐的三样是：`pg_dump` 在 `PATH` 上、
`.runtime/qdrant-api-key.txt` 就位、三个引擎在跑。

**数字对得上**：1532 + 5 = 1537；1526 + 11 = 1537。差的 6 条正是真实模型 live 测试 ——
干净克隆里没有 Key，它们按设计跳过，每条 skip 都带着原因。
**没有一条失败被藏进跳过。**

表里那两次说的是同一时刻的运行（所以总数相等）。**后来新增了 4 条
`test_local_net.py`**，所以最新一次开发机全量是 **1537 passed / 0 failed / 5 skipped**
（14:47，见 §2.1），比表中那一行多 4 条。

**关键区分**：一个什么都不装的干净克隆，**仍然能构建并运行应用本身** ——
干净克隆里 `desktop-native/main.py` 无头启动并持续运行 45 秒被超时终止，无 QML 错误，
且依赖是**一条命令**装完的。需要基础设施的是**集成测试套件**。这两件事不一样，
而上面三组数字是量出来的。

### 5.4 干净克隆里的真实模型端到端 —— **11/11 通过**

§15 第 8 步。在克隆里跑了 `verify_employee_delivery.py`，**Key 只从环境变量进来**
（`DEEPSEEK_API_KEY`），克隆里没有 `ai-config.json`；数据库也是克隆自己建的
（新建空库 → 10 个迁移）。

```
终态: COMPLETED（62 秒）   状态序列: EXECUTING → COMPLETED
新增交付物: 1 个（原有 0 个）
  report-local-first.md   1011 字节   verified=True
  开头: # 本地优先（local-first）软件为什么对个人用户重要
  验证证据: file_exists=pass, encoding=pass(UTF-8), content=pass
员工状态: idle（不是卡在「工作中」）
结果：11/11 通过
```

**这一条同时证明三件事**：干净克隆能真的跑完一次交付；**版本二做的按类型验证在真实
链路上生效**（证据里出现了 `encoding` 与 `content`，不再是只有 `file_exists`）；
退出后员工被正确释放回 `idle`。

### 5.5 `requirements` 是范围不是锁

| 环境 | ruff | PySide6 |
|---|---|---|
| 开发机 `.venv` | 0.16.10 | 6.12.0 |
| 干净克隆新建的 venv | **0.17.0** | 6.12.0 |

`ruff>=0.5,<1.0` 与 `PySide6-Essentials>=6.6,<7.0` 都是范围。这次两边结论一致，
但那是**验证过的巧合**，不是保证。要它成为保证，需要钉到具体版本。

## 6. 系统代理：本地服务被发给了代理（版本三发现并修复）

**症状。** 装完之后窗口顶部一直挂着「降级 / 部分功能不可用：向量检索」，
而 Qdrant 正在运行。

**根因。** Windows 把系统代理存在注册表里，`urllib.request.getproxies()`（httpx 读它）
返回 `ProxyServer` 却**不读 `ProxyOverride`** —— 那个字段正是其它所有 Windows 程序
用来跳过 `127.0.0.1` 的。于是 `http://127.0.0.1:6333` 被发给了代理：

| 代理状态 | 应用看到的 | 界面说的 |
|---|---|---|
| 在跑 | `HTTP 401` | 「Qdrant 在运行，但本次请求被拒绝（未配置 API key 或 key 不匹配）」 |
| 没跑 | `ConnectError` | 「Qdrant 不可达」 |

**两个答案都不是 Qdrant 给的。** Qdrant 两次都健康 —— 而第二种说法会让用户去启动一个
已经在运行的服务。

**不止是横幅。** 对同一个健康的 Qdrant：

```
AsyncQdrantClient.get_collections()  ->  ResponseHandlingException:
                                         All connection attempts failed
```

也就是**语义检索是死的**，产品静默退回了关键词检索。

**范围。** 本机留下的 **92 份包内自检报告，每一份**都报 Qdrant 被阻塞
（76 份是 401 形态，其余是连接形态）。不是偶发，不是竞态。

**为什么开发机看不见。** 开发机上代理装了但常常没开，失败只是换个形状 ——
从 401 变成 `ConnectError`，两者都长得像「服务没起来」。

**修法。** `app.core.local_net.exempt_loopback_from_proxy()`，在两个进程入口
（桌面端 `awbnative.bootstrap.ensure_ready` 与 API 的 `app.main.lifespan`）
里、**在任何客户端被构造之前**调用。它把 loopback 追加进 `no_proxy`，而 `no_proxy`
是 httpx 自己读的 —— 所以它同时覆盖就绪探测、向量库客户端，以及 `qdrant_client`
内部自建的那个 `httpx.AsyncClient`（后者只能靠透传参数才能设 `trust_env=False`）。
**只豁免 loopback**，出本机的流量照走代理。

**验证。** 对**组装后的 payload** 跑 `verify_native_package.py`：

| | 结果 |
|---|---|
| 修复前 | `qdrant: BLOCKED — Qdrant 不可达（ConnectError）`，**9/10** |
| 修复后 | `qdrant: PASS — http://127.0.0.1:6333`，**10/10** |

新增 4 条测试在 `backend/tests/unit/test_local_net.py`。行为测试起一个真实的 loopback
HTTP 服务、按 Windows 的方式设一个不可能工作的代理，然后断言请求真的到达 ——
去掉豁免它就以 `ConnectError` 失败。

**同一次还修了**：`verify_native_package.py` 第 6 项把**任何**名叫 `runtime` 的目录
都判成「打包了数据目录」，包括 `vendor/runtime` —— 那是三个引擎，是 payload 故意带的。
它要找的是用户数据目录 `.runtime`：在可执行文件旁边，不在 `vendor/` 里。
这个假阳性让自检**无法对真正发布出去的那个产物运行**。

## 7. 安全扫描

| 项 | 命令 | 结果 |
|---|---|---|
| 当前工作树 | `tools/scan_for_secrets.py` | **未发现真实凭据。** 85 处形状命中，逐条复核全部是**变量名赋值**（`api_key = …`、`token = …`、`password = …`），不是字面量。**第二遍（逐字比对本机凭据）在本机真的跑了，读到 3 项本机凭据（2 个 `api_key` + 1 个 `SECRET_KEY`），均未出现在被扫描的文件里** —— 见下面那条工具缺陷 |
| **全部 Git 历史** | `tools/scan_git_history_secrets.py` | **1877 个 blob / 63,561,072 字节全扫，结论"历史与当前树均未发现真实凭据"，无需轮换**（2026-10-14 复跑为 2003 个 blob / 66.7 MB，差异来自此后新增的提交，属正常漂移） |
| 历史形状命中 | 同上 | 17 处（`sk-…` 14、AWS 3），**逐条复核，全部是测试夹具**（`test_deepseek_provider.py` 的 `sk-0123…`、`verify_installer_static.py` 的 `AKIAAQA…`）—— 它们**必须**像真凭据，否则测不出"应该拒绝" |
| `.gitignore` 覆盖运行时文件 | 人工复核 | 是 |
| `.env.example` 只含空值/占位符 | 人工复核 | 是（`sk-` 字面量 0 处） |

两个扫描器**比较的是本机真实凭据的值**，不是"长得像不像"。当前树那个会打印
`本机真实凭据: 3 项（值不打印）` —— 它拿这三项去逐字比对，而不是打印出来。

**扫描器自身修掉过三个缺陷**，三个都是"工具说没问题，而它其实什么也没查"：

1. 第一版**扫了 0 个对象却报"干净"** —— `str()` 套在 `bytes` 返回值上得到字面量
   `b'...'`，按换行切分只得一行，40 位 id 过滤全灭。已加自检：少于 10 个对象时
   中止并返回非 0，不报"干净"。
2. 第二版报 **1406 处"真实凭据泄漏"**，全是假阳性 —— 宽松模式匹配到了 `.env` 的
   注释分隔线（60 个连字符），而源码注释里到处是同样的分隔线。已改为按"这是什么"
   取值（键名匹配 `*_KEY` / `*_SECRET` 的值），不按"它长什么样"。
3. **第三版把数据目录写死成构建机上的路径**（2026-10-14 在一台干净机器上发现）：
   `tools/scan_for_secrets.py` 里是 `pathlib.Path(r"C:\Users\user\AppData\Local\AI Workbench Data\.runtime")`。
   在任何别的机器上这个目录不存在，于是 `real_credentials()` 返回空字典，
   而输出是 `本机真实凭据: 0 项` —— **读起来和一个干净结果一模一样，而第二遍根本没跑。**
   已修成两部分：目录按应用自己的顺序解析（`--runtime-dir` > `AI_WORKBENCH_DATA_DIR`
   > `%LOCALAPPDATA%\AI Workbench Data\.runtime` > `<repo>/.runtime`），
   **并且在找不到时明说"第二遍本次没有跑、结论只来自形状匹配"**。
   后半句才是关键 —— 找得到目录只是让这种情况变少，说得出"我没查"才让它不再是假话。
   （同一次还修了两个失败路径：非 git 目录不再抛 traceback，未知参数给出用法。）

**一个什么都没测到的绿色结论，比没有结论更危险。**

---

## 8. CI

`.github/workflows/ci.yml` 已提供：`ruff` + `pytest`，PostgreSQL / Redis / Qdrant
作为 service container（与 `docker-compose.yml` 同版本）。

**状态：`NOT RUN`。** 本文件尚未在 GitHub 上执行过。命令与本地跑的完全一致，
但"本地通过"不等于"CI 通过"，所以 README 里**不放徽章**。

`.github/workflows/windows-installer.yml` 是**手动触发**的，因为它需要 Windows
runner、~485 MB 运行时和 NSIS。它**不会伪装成"已自动验证"**：CI 只能证明"能构建
出来"，不能证明"能用"。

---

## 9. 未执行项汇总

| 项 | 原因 |
|---|---|
| ~~真实桌面截图~~ | **已执行**：`tools/shot_real_pages.py`，真实显示器 7/7 页，见 §4.5 |
| GitHub Actions 实际执行 | 本机没有 GitHub runner；workflow 文件已提供但未验证 |
| macOS / Linux 验证 | 未在这些平台上测试过，因此不宣称支持 |
| 版本二之后重跑真实模型 E2E | 改动不涉及模型调用路径；避免重复花费 |
