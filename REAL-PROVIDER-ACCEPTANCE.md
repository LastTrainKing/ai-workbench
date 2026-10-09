# 真实 Provider 最小验收 — 执行记录

窗口：2026-10-09 23:47 → 2026-10-10 00:05（授权 120 分钟，未用尽）
模型：`deepseek-flash`（厂商在 400 响应中确认可选模型为 `deepseek-flash` / `deepseek-v4-pro`）
**费用：¥0.01**（余额 6.72 → 6.71 CNY）。未达 ¥3 暂停线，未接近 ¥5 上限。

判定词汇严格按本轮要求：`PASS_REAL` / `PASS_STUB` / `FAIL` / `NOT_RUN` / `BLOCKED`。

---

## 一、总表

| 项 | 内容 | 判定 | 证据 |
|---|---|---|---|
| A1 | Provider 配置入口与密钥存储路径 | `PASS_STUB` | `.runtime/ai-config/<owner>.json`（git-ignored）；API 只返回 `api_key_set` + 掩码 |
| A2 | 本地 stub 八类错误 | `PASS_STUB` | **引用第三轮结果**（`test_release_freeze_stub_provider.py` 9/9 + `test_release_freeze_run_record.py` 3/3），未重跑 |
| A3 | 超时 / 网络失败 / 空回答是不同错误类型 | `PASS_STUB` | 同上：`AITimeoutError` ≠ `AIProviderError` ≠ `AIResponseFormatError`，三类分别断言 |
| A4 | 输出 Token 上限 / 重试策略 / Agent 步数限制生效 | `PASS_STUB` | 见 §三 |
| B1 | 在原生 UI 设置页录入 Key，重启后仍在，只回掩码 | `PASS_REAL`（**零调用**） | 6/6，见 §二 |
| B2 | 一次短文本真实 RAG 问答 | `PASS_REAL` | `run_e3d7119e9b11408a863a`，见 §二 |
| B3 | 一次最小 Agent 工具调用 | `PASS_REAL` | `run_0aafffc211b144269722`，见 §二 |
| B4 | 短文档真实 RAG 接地，引用能解析到实际来源 | `PASS_REAL`（复用 B2 的 run，**未新增调用**） | 见 §二 |
| B5 | 真实 Embedding | `BLOCKED` | DeepSeek `/embeddings` → **404**。维度/存储/集合隔离部分 `PASS_STUB` |
| C1 | 真实调用失败后仍保留 `ai_runs`，未误报 SUCCEEDED，无重试风暴 | `PASS_REAL` | `run_ed8a03234fbc40f787d3`，见 §二 |
| C2 | 错误分类（超时/关闭端口/503/非 JSON/空回答/无效 JSON） | `PASS_STUB` | 引用第三轮，不花额度重复验证 |
| C3 | Key 是否泄露到日志/报告/Git/界面 | `PASS_STUB` | 8 个面全部 CLEAN，0 泄露，见 §四 |

---

## 二、真实调用逐项

### B1 — 原生 UI 设置页录入 Key（零成本）

通过 `ServiceBridge.saveAiConfig`（即 `SettingsPage.qml` 按"保存"走的那个槽），随后按页面加载的方式重读：

```
before : provider=deepseek model=deepseek-flash api_key_set=False
saved  : provider=deepseek model=deepseek-flash api_key_set=True hint=sk-****(len=35)
reload : provider=deepseek model=deepseek-flash api_key_set=True hint=sk-****(len=35)
```

6 项断言全过：provider 正确 · 模型是 flash 不是 pro · `api_key_set=True` · **响应体里没有 Key** · **掩码里没有 Key 主体** · 重读后仍在。

Key 只存在于 `.runtime/ai-config/feb70d6d-….json`（git-ignored）。录入时用的临时文件在 `finally` 里删除。

### B2 — 一次短文本真实 RAG 问答

| | |
|---|---|
| 输入 | 一份 4 行短文档，**离线** hash-ngram 向量化（384 维，免费）；问题 1 句；`top_k=3` |
| 运行 ID | `run_e3d7119e9b11408a863a` |
| provider / model | `deepseek` / `deepseek-flash` |
| 回答 | `本项目的封版版本号是 **2.2.0** [1]。` |
| 状态 | `SUCCEEDED` |
| Token | 输入 908 / 输出 92 / 合计 **1000** |
| 引用 | 检索 1 条，`used_citation_ids=[1]`，`grounded=true`，`coverage=1.0` |

四项断言全过：回答非空 · 状态 SUCCEEDED · Token 非零 · provider/model 正确。

### B3 — 一次最小 Agent 工具调用

| | |
|---|---|
| 目标 | `请使用 project_list 工具列出当前所有项目，然后告诉我一共有几个。` |
| 上限 | `max_steps=2`、`AGENT_MAX_TOOL_CALLS=4`、`AGENT_MAX_RUNTIME_SECONDS=120` |
| 运行 ID | `run_0aafffc211b144269722` |
| 工具 | `project_list` — **只读业务工具**，未触碰本机文件或进程 |
| 工具结果 | `status=SUCCEEDED`，`result.ok=true`，摘要 `共 3 个项目` |
| 状态 | `SUCCEEDED`，`stopped_reason=final_answer`，实际 1 步 |
| Token | 输入 4520 / 输出 295 / 合计 **4815** |

七项断言全过，其中关键三项：**工具真的执行了**（`tool_calls` 表里有 SUCCEEDED 行）· **结果被 Agent 观察到**（`ok=true` 的 observation 回传给了下一轮）· **步数上限被遵守**。

最终回答里列出了 3 个真实项目的名称和 ID —— 来自工具，不是模型编的。

> 该 run 共 2 次补全（一次请求工具、一次给最终答复）。这是**一个测试项**内部的 Agent 循环，不是重复执行测试。

### B4 — 真实 RAG 接地验证

**没有新增调用**：B2 那次 run 已经产生了一条接地回答，本项是把它查实。

```
answer              : '本项目的封版版本号是 **2.2.0** [1]。'
ai_run_sources rows : 1
  source doc='release-notes.md' chunk_id=045ad7ea-… score=0.8766
  chunk text: '发布说明\n本项目的封版版本号是 2.2.0。\n封版使用的模型是 deepseek-flash。…'
  the chunk contains the answer's fact ('2.2.0'): True
used_citation_ids   : [1]
```

四项断言全过，关键是第二项：**被引用的那个 chunk 的正文里确实含回答陈述的事实**。

这正是"不得仅凭存在引用 ID 就认定接地成功"要求的那一步 —— 引用 ID `[1]` 不只是存在，它解析到了一条真实存在、内容对得上的 chunk。

### C1 — 真实调用失败后是否保留记录

用一个厂商不提供的模型名诱发真实 400：

| | |
|---|---|
| 运行 ID | `run_ed8a03234fbc40f787d3` |
| 抛出 | `AIProviderError: AI provider returned an unexpected HTTP 400.` |
| **`ai_runs` 行** | **1 条，`status=FAILED`，`error_code=ai_provider_error`** |
| 耗时 | 2.1 秒 |
| Token | 0 / 0（400 在生成前返回，无费用） |
| 事后 | 模型已恢复为 `deepseek-flash` |

六项断言全过。厂商的报错原文顺带确认了可用模型名：`The supported API model names are deepseek-flash, deepseek-v4-pro`。

**这一项在第三轮修复前会得到 0 条记录** —— 那时 `session_scope` 的回滚会连同失败记录一起擦掉。现在真实路径也留痕了。

---

## 三、A4：成本上限是否真的生效

| 机制 | 配置 | 是否验证 |
|---|---|---|
| 输出 Token 上限 | `AI_MAX_TOKENS=200`（本轮强制） | 实测 B2 输出 92、B3 输出 295（跨 2 次补全，单次均 < 200） |
| 重试策略 | `AI_MAX_RETRIES=1`（本轮强制）→ `max_attempts=1` | C1 实测 2.1 秒返回，无退避等待 |
| 不可重试的 4xx | `_RETRYABLE_STATUS = {408,409,425,429,500,502,503,504,522,524}` | 静态确认 400/401/404 **不在其中**，即使开启重试也不会风暴 |
| Agent 步数 | `max_steps=2`，执行器内 `min(max_steps, 16)` 夹取 | B3 实际 1 步 |
| Agent 工具调用数 | `AGENT_MAX_TOOL_CALLS=4` | B3 实际 1 次 |
| Agent 运行时长 | `AGENT_MAX_RUNTIME_SECONDS=120` | B3 数秒内结束 |

**这些上限是本轮为测试强制设置的，不是程序默认值。** 程序默认 `AI_MAX_TOKENS=8192`、`AI_MAX_RETRIES=3`、`agent_max_tool_calls=100`、`agent_max_runtime_seconds=1800`。我没有验证"默认值也会自动省钱"——它不会，它只是有界。

---

## 四、C3：Key 泄露扫描

| 面 | 结果 |
|---|---|
| Git 已跟踪文件 | CLEAN |
| Git 全部历史（`git log -S`） | CLEAN |
| `.runtime/logs` | CLEAN |
| `.runtime` 根目录文件 | CLEAN |
| 本轮脚本与日志（`build/release-freeze`） | CLEAN |
| `backend/` 源码 | CLEAN |
| `desktop-native/` 源码 | CLEAN |
| 报告文件与 `.env` | CLEAN |

**TOTAL LEAKS: 0。** Key 只存在于 `.runtime/ai-config/feb70d6d-e8f5-48a8-9a3d-b8452404e6ee.json`（git-ignored）。扫描只用了 Key 的前 10 位，报告里不出现完整值。

界面侧：B1 已断言 API 只返回 `api_key_set` 与 `sk-****(len=35)` 掩码，响应体不含 Key。

---

## 五、费用明细

| 项 | 请求数 | 输入 | 输出 | 费用 |
|---|---|---|---|---|
| B1 录入 Key | 0 | — | — | ¥0.00 |
| B2 RAG | 1 | 908 | 92 | ≈ ¥0.003 |
| B3 Agent | 2（一次 run 的循环） | 4520 | 295 | ≈ ¥0.007 |
| B4 接地验证 | 0 | — | — | ¥0.00 |
| B5 Embedding | 1（404） | — | — | ¥0.00 |
| C1 真实失败 | 1（400） | 0 | 0 | ¥0.00 |
| 余额查询 | 3 | — | — | ¥0.00 |
| **合计** | **4 次计费请求** | **5428** | **387** | **余额 6.72 → 6.71，实测 ¥0.01** |

未触发 ¥3 暂停线，未接近 ¥5 上限。全程串行，无并发、无批量、无长文档。

### 过程中两次"看起来跑了其实没跑"（都零成本）

1. **第一次 B2**：provider 解析为 `offline` —— 脚本没有设置请求上下文，`resolve()` 落到安装级配置，那里没有 Key。**0 次请求**，回答来自离线引擎。
2. **第二次 B2**：仍是 `offline` —— 安装级配置里有一条更早（10-08）留下的 `enabled_providers: {deepseek: false}`，它比账号级配置优先级高。用设置页自己的开关为本账号打开后，工厂才构建出真实 provider。**0 次请求**。

这两次都是**账号隔离机制在正常工作**，是我的脚本写错了，不是产品缺陷。也正因为如此，B2 的真实调用只发生了一次。

---

## 六、剩余风险与未验证项

| # | 项 | 状态 | 说明 |
|---|---|---|---|
| 1 | 真实 Embedding | **BLOCKED** | DeepSeek 无 `/embeddings` 接口（404）。要验证需换一个提供 embedding 的厂商或本地模型 |
| 2 | 厂商特定的 401 / 429 语义 | `NOT_RUN` | 未用无效凭据或压测去诱发，避免浪费额度与触发限流 |
| 3 | 真实流式输出 | `NOT_RUN` | 本轮只验证了非流式补全 |
| 4 | 真实长上下文 / 大文档 | `NOT_RUN` | 本轮刻意限制规模 |
| 5 | 多轮对话下的真实上下文管理 | `NOT_RUN` | 只做了单轮 |
| 6 | 真实模型下的循环/步数上限 | `PASS_STUB` | 真实模型能否复现循环未验证；stub 已证明机制本身有效 |
| 7 | 计费精度的可观测性 | 观察 | 余额只有 2 位小数，¥0.01 是能观测到的上限；更细的账要靠厂商后台 |
| 8 | 未发生重试 | **已确认** | C1 耗时 2.1 秒且 `max_attempts=1`，无退避等待 |

### 未验证功能（沿用前几轮，本轮未变）

原生 UI 仍有 21 页未移植；多显示器 DPI、Docker、安装包签名仍为 `BLOCKED`；RF-01（原生 UI）仍是唯一封版阻断项。

---

## 七、Key 撤销状态

**未撤销。** 本轮结束时 Key 仍有效（余额查询返回 200，`is_available: true`）。

它当前存放在 `.runtime/ai-config/feb70d6d-e8f5-48a8-9a3d-b8452404e6ee.json`，该路径被 `.gitignore` 覆盖，未进入任何提交。**请到 platform.deepseek.com 撤销它**，然后运行下面这条命令清掉本地残留：

```
.venv\Scripts\python.exe -m app.cli init-env          # 不需要，仅示意
# 实际请执行：
.venv\Scripts\python.exe -c "import sys; sys.path.insert(0,'backend'); from app.ai.runtime_config import clear; clear('feb70d6d-e8f5-48a8-9a3d-b8452404e6ee')"
```

我无法代为撤销 —— 撤销发生在厂商侧，需要你的账号权限。**如果你不撤销，这个 Key 会一直有效。**

---

## 八、本轮没有修改任何产品代码

只新增了 `build/release-freeze/` 下的四个验收脚本（该目录被 git-ignored）。产品代码、测试、安装包均未改动，`git status` 干净，2.2.0 归档件未被覆盖。

唯一的配置变更：为本账号打开了 `enabled_providers.deepseek`，并写入了临时 Key —— 两者都在 git-ignored 的账号配置文件里。
