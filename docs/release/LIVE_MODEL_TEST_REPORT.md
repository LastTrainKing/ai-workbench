# 真实模型测试报告

需求变更后执行：那 6 条 live 测试要跑起来，不再禁止模型调用。
日期：2026-10-14　基线 HEAD `0f9468f` → 本轮 HEAD `606bda0`

---

## 1. Key 的权威来源：**runtime config，测试改为读它**（路径 A）

### 结论

**权威来源是 `.runtime/ai-config.json`**（账号级 `.runtime/ai-config/<owner>.json` 优先），
即用户在界面上配置的那一处。解析顺序：

```
账号文件 → 安装文件 → 环境变量 → 内置默认
```

### 为什么选 A 而不是 B

原来的 `_api_key()` 读 `settings.deepseek_api_key` 或环境变量 —— **环境变量是第三层**。
于是界面上配好的 Key 对测试不可见：6 条 live 测试在一台产品能正常工作的机器上跳过。

**B（把 Key 抄进 `.env`）会让一个密钥有两个副本，而它们可以不一致** —— 那正是这次问题的
成因本身。A 让测试调应用自己的 resolver，不只是"少一个副本"，还让测试用上产品真正会用的
provider / model / endpoint / timeout，而不是手搭一个近似物。

顺带修掉一句**过时的文档**：模块 docstring 原写"Key 由应用自己从环境/`.env` 读取"，
描述的是一个应用已经不再使用的解析顺序。

### 实现

`resolve()` 与 `load()` 增加可选 `runtime_root` 参数，**默认行为不变**（12 个既有调用方
不受影响，63 条相关测试通过）。live 模块显式传仓库真实根目录，因为 `conftest.py` 把测试套件
指向一个一次性 runtime，正是为了别的测试读不到开发者的 Key —— 而**这一个模块是例外**，
它的全部意义就是"真实服务 + 真实凭据"。读那个一次性目录，就是它们永远跳过的原因。

---

## 2. 6 条 live 测试：**6 passed，0 skipped**

命令：`pytest backend/tests/integration/test_deepseek_live.py -v`

| # | 测试 | 结果 |
|---|---|---|
| 1 | `test_real_chat_completion` | **PASSED** |
| 2 | `test_real_model_listing` | **PASSED** |
| 3 | `test_real_json_mode_succeeds_without_the_word_json` | **PASSED** |
| 4 | `test_real_tool_calling_round_trip` | **PASSED** |
| 5 | `test_resolution_honours_the_configured_provider` | **PASSED** |
| 6 | `test_the_raw_key_is_never_serialised` | **PASSED** |

```
tests/integration/test_deepseek_live.py::test_real_chat_completion PASSED
tests/integration/test_deepseek_live.py::test_real_model_listing PASSED
tests/integration/test_deepseek_live.py::test_real_json_mode_succeeds_without_the_word_json PASSED
tests/integration/test_deepseek_live.py::test_real_tool_calling_round_trip PASSED
tests/integration/test_deepseek_live.py::test_resolution_honours_the_configured_provider PASSED
tests/integration/test_deepseek_live.py::test_the_raw_key_is_never_serialised PASSED
[live DeepSeek spend] calls=5 prompt_tokens=808 completion_tokens=467 total_tokens=1275
6 passed in 10.31s
```

第 5 条同时改了断言：它原先 monkeypatch `settings` 来绕开套件的 `AI_PROVIDER=offline`。
现在**真解析**，并断言 Key 的 `source` 是配置文件而不是环境 —— 那正是旧 `_api_key()`
看不见的那一层。

---

## 3. 两条 PDF fixture：**都不是问题，前提本身过时**

| 用户给的位置 | 实际情况 |
|---|---|
| `test_ingestion_and_rag.py:68` "sample PDF fixture not generated" | guard **存在**，但**不触发**。`test_pdf_ingestion` **PASSED** |
| `test_security_and_files.py:248` 同上 | 文件在 `tests/unit/`；guard 存在（属于 `test_real_sample_pdf_parses`），**不触发**，该测试 **PASSED**（断言 3 页 + 正文含 "CAP Theorem"） |

**证据**：

- `samples/distributed-systems.pdf` **已被 git 跟踪，且已在 HEAD 里**（3199 字节，10-03）
- `samples/` 下共 11 个文件被跟踪，未被 `.gitignore` 忽略
- 生成脚本 `scripts/make_sample_pdf.py` 存在且已提交，其 docstring 说明是手写 PDF 以避免
  为一个测试夹具引入 PDF 写入依赖
- **两次干净全量日志里，`sample PDF` 出现 0 次**（11 条 skip 中没有它们）

**结论：既不是"有意不生成"，也不是"生成脚本漏了"—— 夹具已提交，guard 是防御性的**
（万一有人删掉 `samples/` 就跳过而不是报错），在正常检出里从不触发。

---

## 4. 本轮模型调用次数（真实数字）

| 来源 | 调用次数 | tokens | 怎么量的 |
|---|---|---|---|
| 6 条 live provider 测试 | **5** | **1,275**（prompt 808 + completion 467） | 响应自带的 `usage` 字段，测试内累加 |

### 一条必须说清的量测缺陷

**`scripts/model_call_count.py` 对这 6 条报 0。** 它数 `ai_runs` 表，而这 6 条**直接调
provider**、不经过应用的 run recorder，所以不留行。**那个 0 是错的答案，不是"没有花费"。**
所以模块自己数响应里的 `usage`。

同一个脚本对端到端验收**也报不准**，原因见 §5。

### 端到端验收（§12 场景）

| 场景 | 结果 |
|---|---|
| S1 纯问答 | **2/2 PASS** —— 121 字真实回答，`outcome=answer`，不产生交付物 |
| S2 写文章输出 md | **6/6 PASS** —— 797 字节真实文件，验证证据 `file_exists 797 bytes` / `readback 273 chars`，登记为已校验 |
| S3 产物被删除 | **3/3 PASS** —— 重新验证 `passed=false`、`file_exists: not found`，登记不再声称已校验，随后恢复文件 |

**S1 第一次是 FAIL，而失败在我自己的脚本里**：`Turn.as_dict()` 把回复放在
`message.content`，我读的是顶层 `content`。**写错字段名不抛异常，只产生一句假话** ——
和这一整轮在治的是同一个病，区别是这次假话是测试自己说的。修正后 S1 通过。

---

## 5. 最重要的发现：一个我引入的、什么都不报的回归

**`_persist_run` 从来没有成功执行过。**

我在 `591fa2e` 给它加结束时间时读了 `run.started_at` —— **`AIRun` 没有这一列**（`started_at`
属于 `ToolCall`）。`AttributeError` 被调用方的 `try/except` 吞掉，于是**整个函数被跳过**：
没有 `status`、没有 `response_text`、没有 `finished_at`、没有 `agent_run.scratchpad`。

**什么都没失败** —— 运行照样报 COMPLETED（构建执行器读的是内存里的 controller，不是这一行），
交付物照样登记并验证（那些发生在 agent 内部）。唯一痕迹是一行日志，而它的结构化字段不渲染，
所以只看到消息、看不到异常。

**数据证据**：

```
status=SUCCEEDED    finished_at=None                tokens=0     ← 我的改动之后
status=SUCCEEDED    finished_at=None                tokens=0     ← 同上
status=SUCCEEDED    finished_at=2026-10-11 09:25:46 tokens=2000  ← 改动之前
```

**所以那个"让成功有结束时间"的提交，正是把结束时间拿掉的提交。**

**修复**：改用 `result.elapsed_seconds`（agent 自己量的墙钟）。真实运行验证：

```
status: SUCCEEDED
finished_at: 2026-10-13 18:12:45.545377+00:00
latency_ms: 74900          （80 秒的墙钟，吻合）
response_text 长度: 509     （之前为空）
```

**并补上那条缺失的断言**（缺失本身就是缺陷）：`test_persisting_a_result_writes_the_finish_time`
用桩结果调 `_persist_run`，断言行回来时有结束时间、耗时、回答和状态。将来的
`AttributeError` 会挂一条测试，而不是打一行没人读的日志。

### 仍然开着的一条

`ai_runs.total_tokens` 对自主运行**从不写入** —— `RunResult` 不携带 token，`_persist_run`
没有东西可写。后果是 `model_call_count.py`（求和这一列）**系统性低估**：一次做了十几次模型
调用的 agent 运行，它报 0。**这需要把 usage 从 provider 穿过 agent 循环**，是独立的一项。

---

## 6. 本轮真实花费汇总

| 项 | 次数 | tokens |
|---|---|---|
| 6 条 live provider 测试 | 5 | 1,275 |
| 端到端验收 S1（1 次对话） | 1 | 未记录（见 §5 缺陷） |
| 端到端验收 S2（2 次 agent 运行） | 各 1 行 run | 未记录（同上） |
| S1 复跑 1 次 + S2 复跑 1 次（验证修复） | — | 未记录 |

**可以精确报出的只有 1,275 tokens**；端到端部分的 token 数**产品没有记录**，我不编。
按 5 次调用约 1.3k tokens 的规模，本轮全部真实调用量在**数万 tokens 量级**，
远低于 500 万预算 —— 具体数字要等 §5 那条缺陷修好才能从库里数出来。

---

## 7. 改动文件

| 文件 | 改动 |
|---|---|
| `backend/app/ai/runtime_config.py` | `resolve()` / `load()` 增加可选 `runtime_root`（默认行为不变） |
| `backend/tests/integration/test_deepseek_live.py` | 读应用自己的 resolver；用真实 runtime；自计 `usage` |
| `backend/app/services/autonomous_service.py` | **修复 `_persist_run`** |
| `backend/tests/integration/test_run_settlement.py` | **新增回归断言** |
| `desktop-native/verify_live_acceptance.py` | **新增**端到端验收（3 场景，每个只跑一次） |
