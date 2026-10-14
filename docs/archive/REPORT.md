# 第五轮验收报告

> 工作区：`C:\Users\user\Desktop\ai-workbench`
> 本轮要证明的一句话：**它失败了，换了个方法，然后做对了。**

---

## 1. 一句话结论

**重试机制修好了，偏好语义成立了——但两者都只是在"把失败藏起来"的地方被修好的。**

重试死循环的根因确实是 `_recover` 的返回值被丢掉；偏好被字面执行的根因确实是硬约束没有被推导。但在追这两个根因的过程中，同一个形状的缺陷又被翻出**四处**（图表契约、记忆判定、watcher 崩溃、运行永久卡住），全部是"失败被转换成一个看起来合理的空值或空结果"。所以本轮的实质结论是：

- 两个指定根因：**已修复，且用真实运行验收通过**；
- 该形状的系统性复发：**又发现 4 处，其中 2 处（记忆学习回路、运行状态）此前一直是坏的，只是没人看得见**；
- Demo 1 后半段：**三条核心判据全中**（14 页 / 13 图 / COMPLETED），但两个附加判据失败，原因已定位；
- Demo 2a（Word）标题层级：**FAILED**，如实记录。

---

## 2. 重试死循环：根因与修复

### 2.1 根因（已确认）

`backend/app/ai/agent/autonomous.py:707`：

```python
await self._recover(step, observations)   # ← 返回值没有赋给任何东西
step.status = StepStatus.RUNNING
```

`_recover` 把根因分析 `return` 了出来，调用点丢掉了它。分析只进了事件流给人看，**没有回到模型的下一次尝试里**。于是"恢复"只是把同一件事再做一遍：同样的步骤描述、同样的工具、同样的参数、同样的上下文 → 同样的决定 → 同样的失败。第三次尝试在 2 步里烧掉 105 次工具调用，撞上全局 100 次上限。

**这不是上限调小了，是"重试"这个机制从来没有生效过。**

### 2.2 修复（四件事，缺一不可）

| # | 机制 | 位置 |
|---|---|---|
| 1 | `PlanStep.attempts` 记录每次尝试的工具、参数、签名、错误、分析 | `plan.py` |
| 2 | 把历史**注入下一次尝试的提示词** | `autonomous.py` · `render_attempt_history` |
| 3 | 连续两次相同「工具 + 参数」判定为**无效重试**，立即停止该步骤 | `autonomous.py` · `attempt_signature` |
| 4 | 单步预算封顶 `max(MAX_TOOLS_PER_STEP, max_tool_calls / 3)` | `autonomous.py` · `STEP_BUDGET_DIVISOR` |

外加：`_recover` 失败时不再编造占位文字（原来是 `（分析失败：TimeoutError）` 然后照样重试），改为明确上报并停止该步骤。

### 2.3 三个场景的实测结果（`scripts/verify_recovery.py`）

| 场景 | 断言 | 结果 |
|---|---|---|
| A 正常任务 | 不触发恢复、重试 0、终态 COMPLETED | **PASS** |
| B 必然失败的一步 | 第 2 次尝试与第 1 次**内容不同** | **PASS** |
| C 连续失败 | 达上限后**停止该步骤**，不烧穿全局预算 | **PASS** |

场景 B 的事件流原文：

```
第 1 次：['file_exists', 'file_metadata', 'directory_list']
第 2 次：['directory_list']                      ← 工具确实变了
```

场景 C 的实测数字：

```
停止原因：['第 step-2 步的第 2 次尝试与第 1 次使用了完全相同的工具和参数，判定为无效重试，停止该步骤',
          '第 step-4 步的第 4 次尝试与第 3 次使用了完全相同的工具和参数，判定为无效重试，停止该步骤']
该步骤实际花费：[6, 0, 4, 2, 4] 次工具调用      ← 全运行 33 次，上限 100
```

### 2.4 1.2 那三个问题

| 问题 | 答案 |
|---|---|
| 卡死的第一轮重试发生在第几步？ | 场景 B 是 `step-1`；场景 C 是 `step-2`。事件流里每条 `step` 事件都带 `attempt` 和 `step.id` |
| 第二次尝试和第一次有什么不同？ | 工具集合不同（见上）。`attempts[n].tools_used` 与 `attempts[n].same_as_previous` 记录了这件事，并随 `plan` 存进数据库 |
| 如果完全相同，为什么没有触发"无效重试"判定？ | 它现在**总会**触发。`same_as_previous=true` 即停止该步骤，场景 C 的三条停止原因里两条就是它 |

### 2.5 顺带修掉的同类缺陷

- **`_final_verify` 把 `file_exists` 回答"否"当成通过**——同一个 bug 已在 `_verify_step` 修过，最终闸门还留着。
- **有失败步骤的运行仍可能报 `COMPLETED`**——判定与工作互相矛盾。
- **推理模型的 token 预算**：`_recover` 用 600 token，推理模型思考就超了，provider 抛 `ai_reasoning_budget_exhausted`（**没有任何输出**，不是截断），"恢复"静默退化成"放弃"。实测：3000 会被用满并截断，6000 在 ~20s 内正常完成。`max_tokens` 是上限不是目标，给足不花钱。

---

## 3. 偏好语义：四个判据的实测

语义：`任务目标（硬约束） > 长期偏好（软约束） > 默认行为`。硬约束在代码里推导（`app/ai/agent/constraints.py`），不由模型决定。

| # | 输入 | 期望 | 实测 | 结果 |
|---|---|---|---|---|
| 1 | 15 分钟答辩 PPT + 偏好「页数更少」 | 10~14 页 + 取舍说明 | **14 页**，报告含完整取舍说明，写明"硬约束：15 分钟 → 10~14 页（下限来自任务要求，不是偏好）" | **PASS** |
| 2 | 同上 + 偏好「图表更多」 | `images` > 0 | **13 张图** | **PASS** |
| 3 | 3 分钟快速汇报 + 偏好「内容详尽」 | 服从时长 + 说明为什么没做详尽 | **5 页 / 4 图**；详尽被判为 `bounded`，报告写明"做不到详尽：时长只允许 3~5 页" | **PASS** |
| 4 | 偏好「用公司模板」（模板不存在） | 明确报告做不到 | 事件流与报告均出现：`偏好无法满足：「必须使用公司模板…」—— 做不到：工作区里没有任何模板文件` | **PASS** |

四条全部 PASS，证据：`.runtime/pref-check/final.txt` 与 `results.json`。

**判据 4 的实现方式**：模板是否存在、matplotlib 是否可用，都是能查的事实，不是提示词里的一句请求。查完的结论同时进入事件流（用户看得见）和最终报告（写进产出记录）。

**取舍说明由代码生成，不是要求模型写**。"它有没有解释为什么是 10 页"必须能从输出里回答，而模型恰好没提就会让诚实答案是"没有"。

**修掉的一处自相矛盾**：实测输出里曾同时出现"本次目标页数定为 14 页"和"因此取 10 页（区间下限）"——两个数字，都像答案。原因是每条 `bounded` 偏好各自报了自己的数，而解析结果已经定了另一个。现在页数只报一次，偏好只描述对它的约束。

---

## 4. Demo 1 两版对比（最重要的一张表）

目标（两版完全相同，未改写）：
> 用我导入的毕业设计资料，做一份 15 分钟的答辩 PPT，包含系统架构、数据库设计、AI 模块三部分。必须用 document_write 生成 .pptx 文件。

退回理由：**「图表太少，文字堆得太满，页数也偏多，控制在 12 页以内」**

| | 第一版 | 第二版 | 判据 | 结果 |
|---|---|---|---|---|
| 页数 | 11 | **14** | 10~14（15 分钟的硬约束） | **PASS** |
| 图表数 | 0 | **13** | > 0 | **PASS** |
| 文字量 | 2,048 | 1,902 | — | — |
| 文件大小 | 70,538 B | 385,716 B | — | — |
| 终态 | COMPLETED | **COMPLETED** | COMPLETED | **PASS** |
| 机器校验 | 通过 | **通过**（`verified=True`） | 登记且校验 | **PASS** |

三条核心判据（页数服从硬约束 / 图表偏好生效 / 运行状态诚实）**全部通过**。

### 4.1 两个附加判据失败，原因已定位

**（a）页数不降反升：11 → 14。** 我的附加断言"页数不比第一版更多"失败。原因不是机制坏了，而是**另一条偏好把它顶上去了**：库里存在一条"用户在验收报告类交付物时要求内容完整：报告至少应包含背景和结论两部分，内容过简会被退回"。解析结果是 `wants_detailed` 把目标页数设为区间上限 14，报告里明确写了"这是 15 分钟约束下的最大值，因为有一条偏好要求内容更完整；再长就讲不完了"。

这暴露出一个**真实的语义缺口**：退回理由里的"**控制在 12 页以内**"是一个明确的数字指令，而系统只把它吸收成了"页数更少"这个方向性偏好，没有把"12 页"提取成约束。所以第二版落在了 12 页以上。**这是本轮的已知限制，不是通过项。**

**（b）报告里的取舍说明读不到。** 附加断言"报告里有取舍说明"失败，原因是 `GET /api-runs/{id}` 用**展示 id**（`run_ab12…`）查找，而 `BuildRun.ai_run_id` 存的是**主键 UUID**，于是返回 `404 AI run not found`。已修（`by_run_id` 现在两种 id 都收），修复后同一接口可读到 `取舍说明`。

**证据**：`.runtime/demo1-second-half.txt`、`.runtime/demo1-second-half.json`、`samples/demo-output/`（两版样本）。

---

## 5. Demo 2 / Demo 3 完整记录

### 5.1 Demo 2a —— Word

| 检查 | 实测 | 结果 |
|---|---|---|
| 产出 .docx | `系统设计说明书.docx` 45,520 B | **PASS** |
| 大小 > 30 KB | 45,520 B | **PASS** |
| 页数 ≥ 5 | **8 页**（LibreOffice 转 PDF 后数出来的真实页数，不是估算） | **PASS** |
| 标题层级正确 | 全部是 `H1`（第四章…第六章…），**只有一个层级** | **FAILED** |
| 终态 COMPLETED | COMPLETED | **PASS** |
| 机器校验通过 | `verified=True` | **PASS** |

**FAILED 的原因**：`level` 是 section 的字段，而规划器从未被告知该填什么，于是每一章都成了 `H1`。已补上规划器的显式要求（章 level=1 / 节 level=2 / 至少两个层级）。**重跑时出现了另一个问题**：这一次 Agent 绕开 `document_write`，用 `python_exec` 自己写了一个 python-docx 脚本生成文件——审计日志里没有 `document_write` 行，于是运行 COMPLETED 却没有登记任何产出物。运行状态是诚实的（没有假装产出），但**"必须用 document_write"这条硬要求并没有被强制**，这是一个新的已知限制。

### 5.2 Demo 2b —— Excel

| 检查 | 实测 | 结果 |
|---|---|---|
| 产出 .xlsx | `ai_workbench_tool_inventory.xlsx` 6,881 B | **PASS** |
| 真实公式 | `工具清单!B10 = =SUM(B2:B9)`（openpyxl 读到以 `=` 开头） | **PASS** |
| ≥ 1 张图表 | 1 个原生 Excel 图表 | **PASS** |
| 数据行 | 10 行 × 3 列 | **PASS** |
| 终态 COMPLETED | COMPLETED | **PASS** |
| 机器校验通过 | `verified=True` | **PASS** |

### 5.3 Demo 3 —— Agent 自主修复（隔离目录）

隔离项目 `.runtime/demo3/stats-project`：两个真实 bug（`average([])` 除零、偶数长度 `median` 取错中位），4 个测试，2 个失败。

| 检查 | 实测 | 结果 |
|---|---|---|
| 修复前 pytest 失败 | 退出码 1 | **PASS** |
| 失败的正是预期的两个用例 | `test_average_of_empty_is_zero` / `test_median_of_even_length` | **PASS** |
| 修复后 pytest 退出码 0 | 0 | **PASS** |
| 测试文件未被改动 | `sha256` 一致（`eab93346…`） | **PASS** |
| 源码确实被改了 | `sha256` 由 `4f1b4e61…` → `c844613c…` | **PASS** |
| `git diff --stat` 只改了 `src/` | `src/stats.py \| 9 ++++++++-`，1 file changed | **PASS** |
| 原失败用例现在通过 | 0 失败 | **PASS** |

7/7 PASS。结束后隔离目录已清理。

**一处如实记录**：Agent 在项目里留下了一个额外的未跟踪文件 `fix_report.py`（它给自己写的修复说明）。它不属于"改动"，但确实是残留——Agent 可以在工作目录里留下文件，这一条没有被约束。

---

## 6. 验收矩阵

### 恢复机制

| 项 | 状态 | 证据 |
|---|---|---|
| 场景 A 正常任务不触发恢复 | **PASS** | `.runtime/recovery-check/run3.txt` |
| 场景 B 失败后第 2 次尝试**内容不同** | **PASS** | 同上，工具集合不同 |
| 场景 C 达上限后**停止该步骤**，不烧穿全局预算 | **PASS** | 同上，全运行 33 次调用 |
| 能回答 1.2 的三个问题 | **PASS** | 见 §2.4 |

### 偏好语义

| 项 | 状态 | 证据 |
|---|---|---|
| 判据 1：15 分钟 + 页数更少 → 10~14 页 + 取舍说明 | **PASS** | `.runtime/pref-check/final.txt` |
| 判据 2：图表更多 → `images` > 0 | **PASS** | 同上（13 张） |
| 判据 3：3 分钟 + 详尽 → 服从时长 + 说明 | **PASS** | 同上（5 页 / 4 图） |
| 判据 4：无法满足的偏好 → 明确报告 | **PASS** | 同上 |

### Demo

| 项 | 状态 | 证据 |
|---|---|---|
| Demo 1 前半段（不回归） | **PASS** | `samples/demo-output/答辩PPT-第一版-11页.pptx` 70,538 B / 11 页 |
| **Demo 1 后半段：三条核心判据全中** | **PASS** | §4 对比表 |
| Demo 1 附加判据：页数不升 | **FAILED** | 11 → 14，"12 页以内"未被提取为约束 |
| Demo 1 附加判据：报告含取舍说明 | **PASS（修复后）** | `by_run_id` 修复后同接口可读到 |
| Demo 2 Word：> 30 KB / ≥ 5 页 | **PASS** | 45,520 B / 真实 8 页 |
| Demo 2 Word：标题层级正确 | **FAILED** | 全部 `H1`，只有一个层级 |
| Demo 2 Excel：真公式 + ≥ 1 图表 | **PASS** | `=SUM(B2:B9)` + 1 图表 |
| Demo 3：自主修复 + 独立核验 | **PASS** | 7/7，`git diff` 只改 `src/` |
| 产出物与运行状态一致（无 FAILED 却挂 verified） | **PASS** | 新增 `PARTIAL`；未完成运行的产出物带失败的 `run_state` 检查 |

### 其余

| 项 | 状态 | 证据 |
|---|---|---|
| 工作区卫生干净 + `deliverables/` 约定建立 | **PASS** | `.runtime/workspace/{deliverables,notes,sources,.tmp}` |
| 任务页关联知识浏览器实测 | **PASS** | `node scripts/verify-knowledge-and-memory-ui.mjs` 5/5 |
| 记忆页来源追溯可跳转 | **PASS** | 同上 8/8（13/13 总计） |
| 冷启动 10 步 + 真实数字 | **PASS** | `.runtime/cold-start.txt` / `cold-start.json`，**36/36** |
| 桌面断言 12/12 | **PASS** | `verify_shortcut.py` 7/7 + 桌面整理后 13 个顶层条目全部是用户的 |
| 后端 / 前端 / typecheck / lint / build | **PASS** | 后端 411+ 测试、前端 114 测试、`tsc` 干净、`eslint` 0 error、`vite build` 通过 |
| 旧路由全部可用 | **PASS** | 后端全量测试通过 |
| 文档同步（含新创新点章节） | **PASS** | `ARCHITECTURE.md` / `AGENT.md` / `DELIVERABLE.md` / `MEMORY.md` / `WORKSPACE.md` + `docs/thesis/11-创新点.md` |
| 简历 + 面试 20 题 | **PASS** | `docs/resume.md` 第五轮章节 + `docs/interview.md` 补 1~8 |
| EXE / 安装包 / ZIP + SHA256 + commit | 见 §9 | — |
| Secret Scan = 0，Git clean | 见 §9 | — |

---

## 7. 冷启动 + 二次启动真实数字

（由 `scripts/verify_cold_start.py --apply` 实测，记录见 `.runtime/cold-start.json`）

**第一次跑：34/38。** 那 4 项"失败"是同一件事——第 5 步故意跑了一次 BuildRun，所以
`build_runs_total` 和 `deliverables_total` 本来就应该变。拿它们做"不变"断言，测的是测试自己，
不是产品。

**脚本修好之后重跑：36/36 全通过**（`.runtime/cold-start.txt`，退出码 0）。

| 步骤 | 断言 | 实测 | 结果 |
|---|---|---|---|
| 1 全停 | PG / Redis / Qdrant / API / 5173 端口全关 | 5 个端口全部关闭，`ai-workbench-desktop.exe` 已退出 | **PASS** |
| 2 双击快捷方式 | 快捷方式存在 | `AI Workbench.lnk` | **PASS** |
| 3 冷启动断言 | 原生窗口 / health 200 / 无新 msedge / 5173 未监听 | 桌面进程存在；`/health` 200；msedge 进程集合未增加；**5173 未监听** | **PASS** |
| 4 首页数据 | 真实数字 | 项目 **2** / 资料 **7** / 任务 **252** / 记忆 **7** / BuildRun **38** / 产出物 **17**（已验证 17、被退回 6、待验收 11） | **PASS** |
| 5 跑一次 BuildRun | 到达终态 | **COMPLETED** | **PASS** |
| 6 关窗 | PG/Redis/Qdrant/API 全退、无残留 | 4 个端口全部关闭；无 desktop 残留；无 API 残留（8000 端口 0 监听） | **PASS** |
| 7 再次双击 | 正常启动 | 已启动 | **PASS** |
| 8 数据仍在 | 第 5 步不可能改的数据逐项不变 | 项目 2→2、资料 7→7、任务 252→252、记忆 7→7、被退回 6→6、已验收 0→0 **全部不变** | **PASS** |
| 8 持久化的正证 | 第 5 步创建的运行与产出物在重启后仍在 | 运行 **38→39**、产出物 **17→18** | **PASS** |
| 9 无重复进程 | 无重复、无端口冲突 | desktop 进程 1 个、API 监听 1 个、5173 仍未监听 | **PASS** |
| 10 桌面断言 | 12 条不许回退 | 快捷方式独立断言 **7 PASS / 0 FAIL** | **PASS** |

**一处如实记录**：交付构建后 `git status` 并非 clean——Agent 在项目根目录留下了 `chart1.png … chart4.png`、`chart_data.json`。已归档到 `archive/agent-assets/`，之后 `git status` 干净。这与 §8 限制 3 是同一类问题。

---

## 8. 已知限制（如实）

1. **退回理由里的数字约束没有被提取。** "控制在 12 页以内"只被吸收成"页数更少"这个方向性偏好，第二版因此落在 14 页。硬约束目前只从**目标文本**推导时长，不从**反馈文本**推导数量。
2. **"必须用 document_write"没有被强制。** Demo 2a 重跑时 Agent 用 `python_exec` 自己写 python-docx 脚本生成了 .docx，绕过了 `document_write`；运行因此没有登记产出物（状态诚实，但要求未被约束）。
3. **Agent 可以在工作目录里留下文件。** Demo 3 里留下了一个未跟踪的 `fix_report.py`。
4. **单个 Agent 运行很慢。** `deepseek-flash` 是推理模型，一次调用 ~20s；一个 6 步计划要 10~20 分钟。并发 3 个运行时单次调用更慢。
5. **`ai_runs` 与 `BuildRun` 之间存在两套 id**（展示 id / 主键 UUID）。已让接口两者都接受，但两套 id 并存本身是负担。
6. **无浏览器控制**（只有 `screenshot` / `screen_read` 观察）、**无鼠标键盘控制**、**无 OCR**（扫描版 PDF 报错）。
7. **Embedding 仍是离线哈希**，不是语义模型；检索质量相应受限。
8. **Ollama / OpenAI 兼容端点未对真实服务实测**；**Docker 路径未验证**。
9. **安装包仍需项目目录存在**（不自包含）。

---
## 9. 交付物

全部在本轮重建。

| 交付物 | 路径 | 体积 | SHA256 |
|---|---|---|---|
| 桌面 EXE | `desktop/src-tauri/target/release/ai-workbench-desktop.exe` | 3,556,352 B | `46100d21074a0143e4b24796e01a57c20c974fff78205c7ad880db22b33aa2f5` |
| NSIS 安装包 | `desktop/src-tauri/target/release/bundle/nsis/AI Workbench_2.0.0_x64-setup.exe` | 1,123,407 B | `a01ece4c9a0e0190a3fab3383309c4867be1d6f3c4d64d31dbab02a86af09254` |
| 源码 ZIP | `archive/releases/AI-Workbench-2.1.0-final.zip` | 7,249,747 B / **435 个文件** | `dd7d4f301d1512a0d343ccb27b39618dbf84ad3f335f75c3bc90793796ef0293` |

构建过程：

```
前端生产构建  npm run build（tsc --noEmit && vite build）→ 通过
Tauri 构建    npm run tauri build → 编译 1m50s → EXE + NSIS 安装包
源码打包      python scripts/build_delivery_zip.py
              以 git ls-files 为清单：435 个跟踪文件全部纳入，排除策略命中 0
              归档自检：[PASS] no credentials, no runtime artefacts, all required entries present
```

**Secret Scan**：对 435 个被跟踪文件做模式扫描，命中 **4 处**，全部是测试夹具里的占位串
（`sk-0123456789…`、`sk-aaaaaaaa…`、`sk-abcdefgh…`，位于
`backend/tests/unit/test_deepseek_provider.py`、`test_project_integrity.py`、`scripts/test_memory_loop.py`）。
项目自带的归档校验器（它知道真实凭据的值）报告 **0 处真实凭据**。

**Git**：`git status` clean。

**第一次 Tauri 构建失败**，原因如实记录：`failed to remove ai-workbench-desktop.exe — 拒绝访问`，
因为冷启动第 7 步拉起的桌面进程还占着文件。结束该进程后重建成功。

---

## 10. 仍然 NOT VERIFIED 的项

| 项 | 为什么 |
|---|---|
| Ollama 真实端点 | 本机没有运行 Ollama，只验证了不可达时的降级路径 |
| OpenAI 兼容端点 | 同上 |
| Docker 部署路径 | 未运行过 |
| 安装包在「没有项目目录」的机器上安装 | 需要一台干净机器 |
| Embedding 的语义检索质量 | 离线哈希嵌入，没有可对比的基准 |
| 第二版 PPT 的「内容质量」 | 机器只能证明它是 14 页、有 13 张图、能打开；内容好不好只有人能判断 |
| Demo 2a 标题层级修复后的重跑 | 规划器已补上 level 要求，但补上之后的运行没有完成验收（该次运行绕开了 `document_write`） |

---

## 附一：本轮修掉的缺陷清单（全部来自真实运行，不是设想）

| # | 缺陷 | 怎么发现的 |
|---|---|---|
| 1 | `_recover` 的返回值被丢掉 → 重试死循环 | 交接文档定位 |
| 2 | `_final_verify` 把 `file_exists` 回答「否」当成通过 | 读同一形状的历史修复时发现 |
| 3 | 有失败步骤的运行仍报 `COMPLETED` | 写场景 C 断言时发现 |
| 4 | 推理模型 token 预算太小 → 恢复静默变成放弃 | 场景 C 的 `AIError` |
| 5 | 图表 `series` 契约只接受私有形状 → 每张图都失败 | 把「失败返回 None」改成抛异常之后 |
| 6 | 记忆判定 500 token → 学习回路其实是关着的 | 复现判定结果 |
| 7 | 并发运行在记忆计数上互相阻塞 4 分 40 秒 | `pg_stat_activity` |
| 8 | 被重启打断的运行永远停在 `EXECUTING` | 一次运行 plan 6/6 却非终态 |
| 9 | watcher 在 `rollback()` 后读过期对象 → `MissingGreenlet` → 运行被搁死 | `faulthandler` 抓栈 |
| 10 | `ai_run_id` 与 `ai-runs` 接口用两套 id → 报告读不到 | Demo 1 的附加断言 |
| 11 | Word 标题全在 `H1` | Demo 2a 的标题层级断言 |
| 12 | 工作区默认落在用户桌面 | 交接文档 + 桌面清单 |
| 13 | 仓库级 `.mjs` 脚本 import `playwright-core` 失败 | 实际运行浏览器脚本 |

其中 **#5 #6 #9 是同一种形状**：失败被转换成一个看起来合理的空值或空结果（`None`、`DROP`、被吞掉的异常）。加上此前几轮的 5 个，这个形状在本项目里已经出现 **8 次**。

---

## 附二：一处结论修正——我推断错了

上一版报告在这里写过一整节，断言"一次 Agent 运行自己往正式仓库提交了代码"（提交 `bac3a7d`），
并把作者身份不同当成了证据。**这个结论是错的，本节改为如实记录我是怎么错的。**

事实（`git log -1 --format="author: %an <%ae>%ncommitter: %cn <%ce>"`）：

```
bac3a7d   author:    AI Workbench <noreply@local>
          committer: AI Workbench <noreply@local>
ab97b27   author:    AI Workbench <dev@aiworkbench.dev>     ← 本会话的提交
ff8efba   author:    AI Workbench <dev@aiworkbench.dev>     ← 本会话的提交
```

**我当时的推理是**：本仓库配置的提交身份是 `dev@aiworkbench.dev`，`bac3a7d` 用的是
`noreply@local`，所以它是 Agent 用另一个身份提交的。

**错在哪**：`<noreply@local>` 不是 Agent 的身份，是**另一个进程用 `-c user.email=noreply@local`
显式指定**的身份。作者不同只说明"不是同一次提交命令"，不说明"是谁提交的"。我从一个真实观察到
的差异，跳到了一个没有证据支持的结论——这正是本轮任务书工程铁律第 8 条点名的那件事：

> **不要用推断代替证据。** 写结论前先问：我有日志 / 输出 / 文件能证明吗？

我当时手里有 `git log`，但我**没有**去查这个进程是谁、也没有查 Agent 的审计日志里有没有
`git_commit` 调用。查一下就能证伪：如果真是 Agent 提交的，`tool_audit_log` 里会有一条
`git_commit` 记录。

**这一节的教训比那一节的结论更重要**：同一个形状（把观察到的差异当成因果证据）在这一轮
和上一轮各出现一次。本报告其余结论之所以都带着命令输出，就是为了不重犯它。

（`bac3a7d` 本身没有危害：它给 `REPORT.md` 补的交付物章节内容准确，且已被 `ab97b27` 覆盖。）
