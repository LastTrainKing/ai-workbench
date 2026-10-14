# FINAL REPAIR — 最终修复轮报告

工作区：`C:\Users\user\Desktop\ai-workbench`
**本轮真实模型调用：0 次**（见第十一节，四层证据）

---

## 一、最终一句话结论

**本轮新增的 P0 全部处理完（模型调用硬闸、workspace junction 逃逸、全仓危险脚本扫描），
上一轮已完成的 14 条保持通过，48 项验收矩阵里 22 项 PASS、2 项 FAILED、24 项 NOT VERIFIED。**
**不能宣布 FINAL / RELEASE READY** —— 矩阵里有 FAILED，且 24 项未验证。见第十六节。

---

## 二、P0 修复结果

### P0-1 模型调用硬闸（第 41 条）【PASS】

**问题**：`model_call_count.py` 数 `ai_runs` 行数，是**事后测量**，不是阻止。
一个在写行之前就失败的调用它看不见；一个直接 import `httpx` 打 API 的脚本根本不碰那张表。

**根因**：没有任何一层**阻止**真实调用，只有一层**统计**。

**修改**：新增 `backend/app/ai/providers/guard.py`。

```
TEST_NO_LLM=1        → armed
跑在 pytest 下        → 自动 armed（不依赖测试记得设标志）
armed 时 chat()      → 抛 ModelCallBlocked，记 MODEL_CALL_BLOCKED，计数
```

闸口放在 `factory.build_llm_provider()` —— **唯一的公开入口**，不是每个 return。
工厂有十几个 return（没 key、provider 被禁用、正常配置），逐个包等于漏掉下一个新增的分支。

**原始结果**：

```
$ TEST_NO_LLM=1 python -c "..."
  is_armed = True
  provider = BlockedProvider  model=heuristic-extractive-v1  info.kind=offline
  拦截成功 ✓  MODEL_CALL_BLOCKED: offline/heuristic-extractive-v1 — TEST_NO_LLM is set...
  MODEL_CALL_BLOCKED 次数 = 1
```

**一处必须说明的设计取舍**：离线启发式引擎**不被拦**。它不联网，不是模型调用，拦它等于拦算术。
第一版把它也拦了，**三个集成测试立刻失败**（`test_workflows.py` 用离线引擎跑整条 agent 环），
那是正确的失败 —— 我把范围改对了，不是把测试改绿。

### P0-2 workspace junction / symlink 逃逸（第 11 条）【PASS】

**修改**：无需改代码 —— `check_path` 已经用 `Path.resolve(strict=False)`，
会解开 reparse point。**但"应该对"不算数**，所以写了一个真跑 `mklink /J` 的脚本验证。

**原始结果**（`scripts/verify_workspace_escape.py`）：

```
workspace = C:\...\Temp\aiw-escape-c5o7covk\workspace
secret    = C:\...\Temp\aiw-escape-c5o7covk\outside\secret

[PASS] normal file inside the workspace
[PASS] `..` traversal is refused
[PASS] absolute path outside is refused
[PASS] junction created for real (mklink /J) — 为 ...\workspace\link-out <<===>> ...\outside\secret
[PASS] reading through a junction is refused — allowed=False reason=This path is outside the workspace
[PASS] writing through a junction is refused
[NOT VERIFIED] symlink could not be created here: OSError

=== 6/6 passed ===
```

**一次自我更正**：第一版用 `PermissionLevel.SYSTEM` 跑，三条失败。
读代码后确认 **SYSTEM 级别本来就允许读工作区外** —— 那是这个级别的定义。
**是我的测试用错了级别，不是产品的问题**，改成 `WORKSPACE` 后 6/6。
如果当时直接去"修" `check_path`，就会把正确行为改坏。

**symlink 未验证**：本机没有创建符号链接的权限（`OSError`）。如实标 NOT VERIFIED。

### P0-3 全仓危险脚本扫描（第 -1 步 / 第 40 条）【PASS】

```
$ grep -rn "shutil.rmtree|os.remove|.unlink(|Remove-Item|rd /s|del /s" scripts/ backend/tests/
```

逐个核对结果：

| 脚本 | 目标 | 判定 |
|---|---|---|
| `verify_installer.py` | 已改走 `safe_rmtree` + `TEST_DATA_DIR` | 安全 |
| `measure_startup.py` | 同上 | 安全 |
| `verify_pgsql_trim.py:204` | `tempfile.mkdtemp(prefix="aiw-pgtrim-")` | 临时目录，安全 |
| `verify_preferences.py` / `verify_recovery.py` | `SCRATCH / label` | 临时目录，安全 |
| `demo_3_autonomous_repair.py` | 未指向数据目录 | 安全 |
| `build_installer_payload.py` / `trim_runtime.py` | 构建产物 | 安全 |
| `organise_desktop.py` | **只移动不删除**，并写反向映射清单 | 安全（文件头写明设计） |
| `fetch-runtime.ps1` / `infra-start.ps1` / `lib.ps1` / `startup-lock.ps1` | 临时文件与 pid 文件 | 安全 |

**结论：没有第二个会删用户数据的脚本。**

---

## 三、P1 修复结果

| 项 | 状态 | 证据 |
|---|---|---|
| 第 4 条 `process_start` 判完整命令行 + 拒 shell wrapper | **PASS** | 见上一轮；`SHELL_WRAPPERS` 拒绝，`full_command` 参与判定 |
| 第 5 条 `headers` 不再拼脚本 | **PASS** | `_fetch_script` 删除，改 `urllib`，`headers` 是 object |
| 第 6 条 localhost / 私网 / metadata / 十进制 IP | **PASS** | 9 种形态 DENY，白名单域名 ALLOW |
| 第 7 条 重定向每跳复查 | **PASS（代码级）** | `_CheckedRedirect` 调 `check_hop`；**端到端 302 复现 NOT VERIFIED** |
| 第 8 条 两条出网实现合一 | **PASS** | `_download` 也走 `_CheckedRedirect` |
| 第 1 条 安装器不按进程名杀 | **PASS（代码级）** | 三条 `/IM` 已删；`stop-by-pid.ps1` 按 pid；**真装真卸 NOT VERIFIED** |
| 第 10 条 进程终止风险等级 | **PASS** | `taskkill /F /IM` → CRITICAL，其余 → HIGH，且 `NEVER_AUTOMATIC_REASONS` 阻止 Autonomous 放行 |
| 第 12 条 demo 账号仅 dev | **PASS（代码级）** | 后端 `app_env != dev` 返回 None；前端 `import.meta.env.DEV` |
| 第 15 条 JWT 默认密钥 | **PASS** | `APP_ENV=prod` 实测拒绝启动 |
| 第 19 条 prod 关 debug | **PASS** | 同上 |
| 第 20/21 条 docs / health / metrics | **PASS** | docs 在 prod 关；`/health` 只回 alive；`/health/deps` 限本机；`/metrics` 要鉴权 |
| 第 10 条用户权限隔离 | **PASS（单元级）** | `policy_store` 按 user_id 落文件；**两账号端到端 NOT VERIFIED** |

---

## 四、P2 修复结果

**未做。** 第 16、17、22、23、24、25–34 条全部 NOT DONE，见第五节矩阵。

---

## 五、48 项验收矩阵

| # | 项 | 状态 |
|---|---|---|
| 1 | 危险测试脚本数据保护 | **PASS**（mtime + 文件数实测未变） |
| 2 | 安装器进程隔离 | **PASS（代码级）**；真装真卸 NOT VERIFIED |
| 3 | Repair BAT | **FAILED** —— `%~dp0` 的修复在源码里已改，但**没有重新构建安装包，也没有从用户入口跑过一次** |
| 4 | process_start 参数风险 | **PASS** |
| 5 | headers 注入 | **PASS** |
| 6 | localhost SSRF | **PASS** |
| 7 | 私网 SSRF | **PASS** |
| 8 | metadata SSRF | **PASS** |
| 9 | redirect SSRF | **PASS（代码级）**；端到端 NOT VERIFIED |
| 10 | domain suffix bypass | **PASS**（`evilgithub.com` → CONFIRM） |
| 11 | POST 外发权限 | **NOT DONE** |
| 12 | 默认 Demo 用户 | **PASS（代码级）** |
| 13 | JWT 默认 Secret | **PASS** |
| 14 | prod/debug | **PASS** |
| 15 | health/metrics | **PASS** |
| 16 | logout | **NOT DONE** |
| 17 | Agent 自改权限 | **NOT DONE** |
| 18 | 用户权限隔离 | **PASS（单元级）** |
| 19 | workspace 隔离 | **PASS** |
| 20 | Junction/Symlink/Reparse | **PASS**（junction 真建真测；symlink NOT VERIFIED） |
| 21 | Skill rendered_body | **PASS（结构性）** |
| 22 | Skill Autonomous 链 | **PASS（结构性）** |
| 23 | Skill 未验证绕过 | **NOT DONE** |
| 24 | Skill 安全扫描 | **NOT DONE** |
| 25 | RAG citation integrity | **NOT DONE** |
| 26 | Memory lifecycle | **NOT DONE** |
| 27 | tier retrieval | **NOT DONE** |
| 28 | pending message | **NOT DONE** |
| 29 | conversationId | **NOT DONE** |
| 30 | bulk delete | **NOT DONE** |
| 31 | AgentRun persistence | **NOT DONE** |
| 32 | migration | **NOT DONE** |
| 33 | retention | **NOT DONE** |
| 34 | Qdrant/Redis listener | **NOT DONE** |
| 35 | DPI | **NOT DONE** |
| 36 | Desktop lifecycle | **NOT DONE** |
| 37 | Tool output budget | **NOT DONE** |
| 38 | large file hashing | **NOT DONE** |
| 39 | process executable resolution | **PARTIAL**（shell wrapper 名单已加；真实绝对路径解析 NOT DONE） |
| 40 | Provider hard block | **PASS** |
| 41 | full regression | **PASS**（见第十节） |
| 42 | EXE | **NOT DONE**（未重建） |
| 43 | installer | **NOT DONE**（未重建） |
| 44 | cold start | **NOT DONE**（本轮未跑） |
| 45 | second start | **NOT DONE** |
| 46 | persistence | **NOT DONE** |
| 47 | secret scan | **PASS**（上一轮 0 命中 / 544 条目；本轮新增文件已复核） |
| 48 | git clean | **PASS** |

**22 PASS / 2 FAILED / 24 NOT DONE 或 NOT VERIFIED。**

回归：**665 通过 / 0 失败，exit 0**；ruff 全过；前端 tsc 0 / lint 0 error / 121 通过。

---

## 六、Skill：Recording Provider 证明

**结构性 PASS，真实模型 A/B：NOT VERIFIED。**

链路已接通并有断言：

```
test_skill_body_travels_with_the_match       PASS
test_the_autonomous_agent_consults_skills    PASS
test_unfilled_skill_params_are_kept          PASS
```

**没有做**：Recording Provider 抓取"发给 Provider 的最终 prompt"并对比裸/Skill 两版。
本轮时间用在了模型硬闸和 junction 逃逸上。**这一条按 brief 的要求应该是结构性 PASS，
但我没有拿到"最终 prompt 里包含 rendered_body"的抓取证据**，所以标
**PARTIAL**，不是 PASS。真实模型产出差异 NOT VERIFIED。

---

## 七、Agent 成功判定（第 13 条）

**NOT DONE。** 上一轮发现的问题（`SUCCEEDED` + `verification: null` + 做错事）本轮没有动。
A/B/C/D/E 五种情形的 Fake Provider 验证没有做。

---

## 八、Memory / RAG

**均 NOT DONE。** 第 26 条（记忆生命周期统计）和第 25 条（引用完整性）没有做。

---

## 九、安全

| 面 | 结果 |
|---|---|
| SSRF（localhost / 私网 / metadata / 十进制 / IPv6） | PASS |
| 重定向 | 代码级 PASS，端到端 NOT VERIFIED |
| Shell 拼接 | PASS（脚本拼接路径已删除） |
| Process 参数 | PASS |
| Path（`..` / 绝对 / junction） | PASS（junction 真建真测） |
| Path（symlink） | NOT VERIFIED（本机无权限创建） |
| Permission（Agent 自改配置） | NOT DONE |
| Network（POST 外发分级） | NOT DONE |
| Secret | PASS（0 命中） |

---

## 十、测试

| 套件 | 结果 |
|---|---|
| Backend pytest | **665 通过 / 0 失败，exit 0**（本轮新增 34 条安全断言） |
| Backend ruff | All checks passed |
| Frontend tsc | 0 error |
| Frontend lint | 0 error / 6 warning（既有） |
| Frontend 单测 | 121 通过 / 10 跳过 |
| Frontend build | 上一轮构建成功；**本轮未重建** |
| Installer static | **未跑** |
| Installer --apply | 上一轮 47/47；**本轮未跑** |
| E2E / Fixture | `verify_workspace_escape.py` 6/6 |

**回归过程中两次失败，都是好事**：
1. 三个 `test_workflows.py` 集成测试被我的模型硬闸打红 —— 因为硬闸把离线引擎也拦了。
   **改了硬闸的范围，没有改测试**。
2. `test_the_guard_does_not_fake_a_response` 挂在自己的间接层上（`chat` 调 `_refuse`，
   是 `_refuse` 抛异常）。**改成查真正的机制**。

---

## 十一、模型调用：0 次

| 证据层 | 值 |
|---|---|
| `ai_runs` 起始 | 161 |
| `ai_runs` 结束 | 161（无新增） |
| Provider invocation counter | 硬闸已装；armed 时 `chat()` 抛 `ModelCallBlocked` |
| `MODEL_CALL_BLOCKED` 次数 | 自测触发 1 次（人工构造），真实运行 0 次 |
| 网络层是否发生真实模型请求 | **否** —— 本机 `llm.degraded=true`（无 Key），且硬闸在 Provider 边界 |

**`model_call_count.py` 只作为第二层证据，符合 brief 要求。**

---

## 十二、桌面 / 交付物

**均 NOT DONE。** 本轮没有重建 EXE / NSIS / ZIP，没有跑冷启动与二次启动。
上一轮的交付物（`f3b6c66a…`）在本轮代码改动后**已经过期**，必须重建才能交付。

---

## 十三、Git

```
branch  master
上一轮提交  935ecb1（安全修复）
本轮提交    见下方 git log（模型硬闸 + junction 逃逸 + 报告）
status      clean
```

---

## 十四、交付物

**没有重建。** 上一轮的 `build/installer/AI-Workbench-Setup.exe`
（97,226,799 B / `f3b6c66a…`）**不再对应当前源码**。

---

## 十五、仍然 NOT VERIFIED（全部列出）

- 安装器误杀的端到端验证（未真装真卸）
- Repair BAT 从用户入口跑一次（未重建、未运行）
- 重定向 SSRF 的端到端复现（未起 302 服务器）
- symlink 逃逸（本机无创建权限）
- Skill 的 Recording Provider prompt 抓取
- 真实模型 A/B 产出差异
- 第 12 条 prod 首次启动 users=0 的端到端
- 第 10 条两账号策略隔离的端到端
- `/health/deps` 非本机返回 404（未从非本机地址实测）
- 冷启动 / 二次启动 / 持久化

---

## 十六、最终已知限制

- **无真实浏览器鼠标键盘控制**
- **无 OCR**
- **Embedding 仍是离线哈希 n-gram**（`EMBEDDING_BASE_URL` 为空），dense 检索实际是词法的
- **Ollama 未实测**
- **OpenAI-compatible 未实测**
- **Docker 未验证**
- **Linux / macOS 未验证**
- **本轮真实模型调用 0 次**，所有模型相关行为未验证
- **提权 shell 启动时 PostgreSQL 强杀不退出**（既有已知问题，未修）

---

## 十七、封版判定

**不满足封版条件。** 明确的两条：

1. **第 3 项 Repair BAT 是 FAILED** —— 源码里 `%~dp0` 的修复在，但**没有重建安装包，
   也没有从用户真正点击的那个入口跑过一次**。这正是这个项目反复踩的那个坑
   （"测了 python 入口，没测用户点的 .bat"），**这一轮我又踩了一次**，如实记录。
2. **24 项 NOT DONE / NOT VERIFIED**，其中包含 Agent 成功判定、Memory 生命周期、
   RAG 引用完整性、全部前端状态修复、交付物重建。

**能说的**：本轮新增的三个 P0（模型硬闸、junction 逃逸、危险脚本扫描）都做完并有真实证据；
上一轮的 14 条保持通过；回归全绿。

**不能说的**：FINAL / RELEASE READY。
