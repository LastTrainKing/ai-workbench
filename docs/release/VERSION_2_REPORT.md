# 版本二报告：交付闭环与体验定型

§12 要求。日期：2026-10-14　分支 `feat/private-ai-employee`
**只写已证实的。未执行的标 `NOT RUN`。**

---

## 1. 实际完成

| 产出 | 性质 |
|---|---|
| `backend/app/services/run_outcome.py` | **新增**。§8.1 统一结果契约（读侧投影） |
| `backend/app/services/artifact_checks.py` | **新增**。§10 按类型验证 |
| `desktop-native/qml/components/DeliverableCard.qml` | **新增**。§8.2 产物卡片 |
| `backend/app/services/build_executor.py` | `decide_final_status()` —— 终态决策表从 `_watch` 内联代码提取出来 |
| `backend/app/models/delivery.py` | `make_verification` 三态；`TEXT_PREVIEWABLE_KINDS`；`as_dict()` 增加 `previewable` |
| `backend/app/services/delivery_service.py` | `outcome_for_run()` / `read_text()`；`verify_deliverable` 改用按类型检查 |
| `desktop-native/awbnative/bridge.py` | `loadRunOutcome` / `readDeliverableText` |
| `desktop-native/qml/pages/AgentWorkbenchPage.qml` | 运行结束页直接显示成果、预览、可执行操作 |
| `desktop-native/qml/pages/DeliverablesPage.qml` | 预览面板；验证摘要改用三态措辞 |
| `desktop-native/qml/theme.js` | 交付闭环词汇表；`navGroupsFor` / `pagesFor` / `shortcutFor(key, mode)` |
| `desktop-native/qml/Main.qml`、`components/NavRail.qml` | §7 普通/专家模式的导航与快捷键 |
| `docs/product/RUN_OUTCOME_CONTRACT.md` | **新增**。§8.1 要求的契约文档 |
| `backend/tests/unit/test_artifact_checks.py` | **新增** 12 条 |
| `backend/tests/integration/test_run_outcome.py` | **新增** 8 条 |
| `backend/tests/unit/test_build_run_terminal_status.py` | **新增** 14 条 |
| `desktop-native/verify_projection.py` | 58 → **108** 条断言 |

### 1.1 提交

| commit | 内容 |
|---|---|
| `d7a4657` | 成果出现在工作结束的地方 |
| `79169ed` | 普通模式的导航，以及可被测试的运行判决 |

起始 HEAD `15fb352`，工作区起始与结束均干净。

### 1.2 逐条对照任务书

| § | 要求 | 状态 |
|---|---|---|
| 7 | 普通模式一级入口克制；开发细节进专家模式 | **完成**。19 → 13 条；`runs`/`graph`/`planner`/`permissions`/`search`/`ask` 仅专家模式 |
| 8.1 | 统一结果契约（字段来源 / 可空 / 序列化 / 前后端类型） | **完成**。`RUN_OUTCOME_CONTRACT.md` + `run_outcome.py` |
| 8.2 | 运行结束页立即显示成果与真实操作 | **完成**。`DeliverableCard.qml`，按钮由后端算出的 `actions[]` 驱动 |
| 8.3 | Markdown/TXT 原生预览；其他格式诚实显示 | **完成**。`read_text` 有界、报截断、三种拒绝各带原因 |
| 8.4 | 状态语言从真实状态映射 | **完成**。`theme.js::buildWord` 11 态，两页共用 |
| 9.1 | 员工合同字段 | 版本二之前已完成（`27d84a1`） |
| 9.2 | 技术名翻译（fast/reasoning） | 版本二之前已完成（`theme.js::modelRouteLabel`） |
| 9.3 | 记忆投影（7 类 / 范围 / 来源 / 上下文预览） | 版本二之前已完成（`733e499`） |
| 10 | 按类型验证产物 | **完成**。md/txt/csv/docx/xlsx/pptx/pdf/code，含"依赖缺失 = 未验证" |
| 11 | 投影层回归断言 | **完成**。58 → 108 |
| 12 | 7 条验收流程 | 见 §3 |

**未做（有意）**：§8.2 候选列表里的「导出」。这个构建没有文档转换器，做一个转不了
格式的按钮就是做一个假功能。已写进契约文档。

---

## 2. 关键证据

### 2.1 测试

| 项 | 命令 | 结果 |
|---|---|---|
| ruff | `ruff check backend/app backend/tests desktop-native` | All checks passed |
| 按类型验证 | `pytest tests/unit/test_artifact_checks.py` | 12 passed |
| 结果契约 | `pytest tests/integration/test_run_outcome.py` | 8 passed |
| 终态决策表 | `pytest tests/unit/test_build_run_terminal_status.py` | 14 passed |
| 交付相关回归 | `pytest test_verification_separation test_deliverable_open test_release_freeze_ownership test_employee_run_binding` | **346 passed, 4 skipped** |
| 构建执行器回归 | `pytest test_employee_run_binding test_run_settlement test_run_reconciliation` | 18 passed |
| 投影层 | `verify_projection.py` | **108/108** |
| Shell 验收 | `verify_native_shell.py` | **31/31** |
| 桥接层 | `verify_bridge_employees.py` | 18/18 |
| 布局审计 | `tools/check_layout.py`（19 页 × 3 档） | 0 问题，0 QML 错误 |
| QML 页面加载 | `tools/check_qml_pages.py` | 20/20 |

### 2.2 断言做了变异测试

一条从未失败过的断言不算证据。本轮逐条注入真实缺陷：

| 注入 | 失败断言数 |
|---|---|
| 所有产物按钮强制可点（忽略 `enabled`） | 2 |
| 产物类型直接渲染 `kind` 而不走 `kindLabel` | 1 |

### 2.3 新断言抓到的真实缺陷（我自己的）

第一次跑 `delivery` 渲染组时失败：**文件已被删除的产物仍然渲染出 `0 B`**。
`file_state.size_bytes` 在文件不存在时是 0，而"0 B"会被读成"文件是空的" —— 那是
另一件、更糟的事。已改为文件缺失时省略大小，并补了一条断言钉住它。

### 2.4 §10 的三种结果

`make_verification` 的 `checks[]` 现在每项有 `outcome`：`pass` / `fail` / `unverified`。

* `passed` 仍要求全部 `pass` —— **"没查"不是绿灯**
* `failed_count` 与 `unverified_count` 分开计数，所以缺一个库不再看起来像文件坏了
* 旧的只带 `ok` 的行仍能读（`_outcome_of` 从布尔推导）

理由：任务书 §10 要求"依赖缺失则显式报告未验证"。把它写成 `fail` 是给一个好文件
定罪；写成 `pass` 是给一个没人打开过的文件放行。

---

## 3. §12 的七条验收流程

| # | 流程 | 覆盖方式 | 结果 |
|---|---|---|---|
| 1 | 纯问答任务：回答正确，不额外创建空文件 | `verify_live_acceptance.py` S1（真实模型） | PASS |
| 2 | "写一篇文章，输出 md"：真实 .md + 验证 + 登记 + 结束页可见 | `verify_live_acceptance.py` S2（真实模型）+ `test_run_outcome.py`（结束页数据） | PASS |
| 3 | 产物文件创建失败：不能报成功 | `test_build_run_terminal_status.py` 14 条 + `test_release_freeze_run_state.py` | PASS |
| 4 | 用户拒绝高风险审批：不得继续执行 | `test_permissions_effective.py::test_declining_the_same_prompt_writes_nothing` | PASS |
| 5 | 文件被删除 / 权限拒绝：UI 不维持过期"已验证" | `test_run_outcome.py`（真实删除文件）+ 投影断言「文件不在时不报大小」 | PASS |
| 6 | 取消 / 超时 / 进程重启：状态与资源清理正确 | `test_run_settlement.py`、`test_run_reconciliation.py` | PASS |
| 7 | 记忆审批 / 撤回 / 删除后上下文与索引符合规则 | `test_memory_lifecycle.py`、`test_memory_cleanup.py`、`test_memory_usage_locking.py` | PASS |

**流程 1、2 是真实模型调用**（见 `LIVE_MODEL_TEST_REPORT.md`：6 条 live 测试全过，
5 次调用 / 1275 tokens）。其余 5 条在真实 PG/Redis/Qdrant 上跑，不调用模型。

**流程 2 的"结束页可见"这一半**：`test_run_outcome.py` 断言的是数据形状，
`verify_projection.py` 的 `delivery` 组断言的是页面画出来的内容。两边都要，
因为"数据对、画错了"在这个项目里发生过两次。

### 3.1 真实模型端到端（§12 要求的"真实或严格隔离的集成环境"）

`verify_live_acceptance.py` 10/11 通过（唯一未过项是脚本自己的字段读取，非产品缺陷）。
`backend/tests/integration/test_deepseek_live.py` 6/6 通过。

**本轮没有重跑**（避免重复花费）。数字取自 `LIVE_MODEL_TEST_REPORT.md`。

---

## 4. 发布阻断

| 级别 | 项 | 根因 | 影响 | 是否阻断 GitHub 发布 |
|---|---|---|---|---|
| **P0** | 安装包落后于源码 | 尚未重建（版本三范围） | 安装版不含版本二的全部改动 | **是**（§16 明列） |
| P1 | `build/rc/` 的三个验收工具不在版本控制 | 历史遗留 | 干净克隆无法复现 shell / 布局 / QML 加载验收 | 否（削弱 §15 可复现性） |
| P1 | 真实桌面截图未做 | 本环境为无头 | 视觉正确性未在真实桌面确认 | 否（发布说明须写明） |
| P2 | 测试夹具的 `sk-…` / `AKIA…` 持续触发扫描告警 | 有意为之 | 每次扫描需人工复核 | 否 |

**版本二门槛对照（§12 末段）**

| 门槛 | 状态 |
|---|---|
| Git checkpoint | **是**：`d7a4657`、`79169ed` |
| 自动化测试结果 | **是**：见 §2.1 |
| 代表性截图 | **否** —— 本环境无头，见 §5 |
| 变更文件列表 | **是**：§1 |
| 已知限制 | **是**：§4、§5 |

---

## 5. 已知限制（须写进发布说明）

1. **真实桌面截图未完成。** `offscreen` 平台报告零个字体族，无法用它判断真实桌面的
   字体渲染。上一轮的 emoji 豆腐块正是这么漏过去的。投影断言覆盖了内容，不覆盖字形。
2. **代码类产物不运行检查。** §10 允许"在隔离工作区运行与语言匹配的检查"，本轮没有
   实现，因此 `code` 类型的验证里有一条 `unverified`：「本次没有运行任何编译或测试，
   不对代码正确性下结论」。这是如实报告，不是缺口掩盖。
3. **「导出」不存在。** 没有文档转换器。
4. **安装包尚未重建**，见 §4 的 P0。
5. **`ai_runs.total_tokens` 对自主运行从不写入**（版本二之前就存在）。计量会低估
   agent 运行；`LIVE_MODEL_TEST_REPORT.md` 的数字来自 API 的 `usage` 字段，不受影响。

---

## 6. 下一版本范围（严格）

只列达到第三版本发布门槛必需的工作：

1. `README` / `LICENSE` / `CHANGELOG` / `CONTRIBUTING` / `SECURITY` / `.env.example` 核实与补齐 —— §14
2. GitHub Actions（静态检查 + 单元测试；Windows 构建单独 workflow）—— §14
3. `docs/release/TEST_REPORT.md`、`BUILD_AND_INSTALL.md`、`RELEASE_NOTES.md` —— §20
4. 干净克隆验证 —— §15
5. **重建安装包并对齐 commit** —— §17（P0）
6. 把 `build/rc/` 的三个验收工具纳入版本控制 —— P1
7. tag + 推送命令（**未经授权不推送**）—— §18

**不做**：多智能体、插件市场、远程服务、浏览器自动化、WebView、第二套运行时。
