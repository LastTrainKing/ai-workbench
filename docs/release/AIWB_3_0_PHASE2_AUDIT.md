# AI Workbench 3.0 第二阶段审计

§2 要求的现状与差距核对。日期：2026-10-14

---

## 1. 真实状态（实际机器，非推断）

| 项 | 值 |
|---|---|
| 项目根目录 | `C:\Users\user\Desktop\ai-workbench`（**源码仓库**，不是 `%LOCALAPPDATA%\AI Workbench` 安装目录） |
| Git 分支 | `feat/private-ai-employee` |
| HEAD | `f94f5dac7052ef429b14065fc83b91aa31c4afe3` |
| 工作区 | **干净**（0 个未提交改动，用户无未保存工作会被覆盖） |
| 仓库 VERSION | `2.5.0` |
| 安装版 VERSION | `2.5.0` |
| 安装包构建时间 | 2026-10-14 00:37:56 |
| 源码最新提交时间 | 2026-10-14 00:54:28 |

### ⚠ 安装包落后于源码

安装包构建于 `00:37`，而源码最后提交于 `00:54` —— 上一轮的**岗位合同（§八）改动没有进安装包**。
§2 明确要求核对这一点；结论是**必须先重建**，否则"安装版通过"无从谈起。

### 数据来源与 Key 优先级（已核对）

`backend/app/ai/runtime_config.py` 的解析顺序（高到低）：

1. 账号级 `.runtime/ai-config/<owner>.json`
2. 安装级 `.runtime/ai-config.json` ← 当前 Key 在这里
3. 环境变量（`DEEPSEEK_API_KEY` / `AI_PROVIDER` / …）
4. `app.core.config.Settings` 内置默认值

**测试隔离已具备**：`backend/tests/conftest.py` 把 `AI_WORKBENCH_DATA_DIR` 指向
`<repo>/.runtime/test-runtime`（上一轮修的），测试不读开发者个人 `.runtime`。

---

## 2. 上一轮材料（已找到，不重写）

| 文件 | 状态 |
|---|---|
| `docs/product/DEEPSEEK_BOT_DESIGN_ANALYSIS.md` | ✓ 存在（DSB 逆向设计报告） |
| `docs/product/OPEN_SOURCE_REUSE_AUDIT.md` | ✓ 存在 |
| `docs/architecture/adr/0001-open-source-reuse.md` | ✓ 存在（路线 C：只移植设计） |
| `docs/product/3.0_GAP_ANALYSIS.md` | ✓ 存在（第一轮差距分析） |
| `docs/release/REPORT_3.0_ROUND1.md` | ✓ 存在（第一轮报告） |
| `docs/release/ACCEPTANCE_REPORT_2.5.0.md` | ✓ 存在 |

**关键改动确认存在于 HEAD**：`591fa2e`（运行语义）、`b8803fb`（首页）、`3fab2f5`（Agent 走员工）、
`27d84a1`（岗位合同 + 字体回退）、`f94f5da`（第一轮报告）。

---

## 3. 现有测试设施盘点（决定 P0 怎么做）

| 工具 | 覆盖 | 能否捕获"投影错误" |
|---|---|---|
| `backend/tests/`（pytest） | 后端逻辑 | ✗ 不覆盖 QML 渲染 |
| `build/rc/check_qml_pages.py` | 20 页能否加载 | **✗ 只查语法/加载** |
| `desktop-native/verify_native_shell.py` | 31 项导航/快捷键/可访问性 | △ 只查存在性，不查内容正确性 |
| `build/rc/check_layout.py` | 19 页 × 3 档对齐 | △ 查几何，不查内容 |
| `desktop-native/verify_bridge_employees.py` | 桥接层 18 项 | △ 查数据，不查投影 |

**结论：仓库里没有任何设施能回答"页面渲染的内容是否与数据一致"。** 这正是 §3 要解决的问题，
也是上一轮两次"测试全绿但用户看到错误"的结构性原因。

**没有 QML 单元测试框架**（无 `tst_*.qml`、无 `qmltestrunner`）。因此 P0 的投影测试要建在现有
Python + PySide6 无头渲染设施上，断言**渲染树**（真实文本、可见性、顺序），而不是文件字符串。

---

## 4. 差距清单（按 §3–§8）

| # | 差距 | 证据 | 目标文件 |
|---|---|---|---|
| P0-1 | 无投影层回归测试 | 见 §3 | 新增 `desktop-native/verify_projection.py` |
| P0-2 | 首页 provider 字段误读无测试保护 | `b8803fb` 修过 `summary.llm` → `providers.llm` | 同上 |
| P0-3 | 字体回退无测试 | `27d84a1` | 同上 |
| P1-1 | `knowledge_scope` 不可见 | 员工行有，合同 12 节里没有 | `EmployeeContract.qml` / `theme.js` |
| P1-2 | `memory_scope` 不可见 | 同上（实测 `{read:[global,user,project], write:[global]}`） | 同上 |
| P1-3 | `model_policy` 不可见 | 同上（实测 `{light:"fast", primary:"reasoning"}`） | 同上 |
| P1-4 | 记忆 7 类 `kind` 与 `scope` 不可见 | `MemoryPage.qml` 只列条目 | `MemoryPage.qml` / `theme.js` |
| P1-5 | 无 `TaskResult` 展示适配器 | 无此模块 | 新增读侧投影 |
| P1-6 | 交付物缺预览 | `DeliverablesPage` 有 4 个操作，无预览 | `DeliverablesPage.qml` |
| P1-7 | 验证层只有 2 个通用检查 | `verify_deliverable` 只出 `file_exists` + `readback` | `services/delivery_service.py` |

---

## 5. 本轮修改范围 / 不改范围

**改**：`desktop-native/qml/`（投影层）、`desktop-native/awbnative/bridge.py`（如需新读侧槽）、
新增验证工具、`services/delivery_service.py` 的验证器扩展（若时间允许）。

**不改**（§1 明确禁止）：
- Agent Runtime / RAG / Memory / 权限内核
- 数据库 schema（**本轮零迁移**）
- 不新增 API（除非审计证明现有接口无法完成闭环，且先说明理由）
- 不引入 WebView、不引入新第三方依赖
- 不把 `final_answer` 非空当作成功判据
- 不编造质量分、文件大小、验证成功

---

## 6. 诚实说明：本轮实际能完成的范围

任务书包含 P0（投影层测试）+ 5 项 P1 + 干净全量测试 + 真实 E2E + 重建安装包 + 4 份报告。
这是一轮很大的工作。本审计之后我将**按 §11 的顺序从 P0 开始**，并在最终报告里逐项标注
"完成 / 部分 / 未做"，**不以"时间不够"为由把未跑的项目写成通过**。
