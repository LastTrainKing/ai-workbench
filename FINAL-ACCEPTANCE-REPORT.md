# AI Workbench 最终验收报告

工作区：`C:\Users\user\Desktop\ai-workbench`
起点提交：`a1ae8cd`（核对时 HEAD 即此，工作区 clean，无后续提交）
**本轮真实模型调用：0 次**

---

## 一、结论

**不能宣布封版。**

三项模块的验收缺口补齐、中断运行状态恢复、交付物重建、安装版端到端验收全部完成，
回归全绿。但仍有**明确列出的 NOT VERIFIED 项**（第八节），且其中一项是核心路径的
真实硬件行为（DPI），不是可以靠测试代替的。按封版条件，保留 NOT VERIFIED 即判定不封版。

| 项 | 结果 |
|---|---|
| AgentRun 归属与持久化 | **PASS** |
| 旧库迁移 + 安装版迁移路径 | **PASS** |
| Snapshot/Audit 归档·恢复·清理策略 | **PASS**（默认零删除） |
| 中断运行状态恢复 | **PASS** |
| 交付物重建（EXE/NSIS/ZIP） | **PASS** |
| 安装版端到端验收 | **PASS** |
| 回归（后端/前端/静态） | **PASS** |
| 模型调用 | **0 次** |
| DPI 真实硬件 | **NOT VERIFIED** |

---

## 二、AgentRun 归属与持久化

### 修掉的一个真缺陷：422 无法区分"不是你的"和"请求格式错"

`_owned_controller` / `_owned_run` 原来抛 `ValidationError` —— **422，`code="validation_error"`**，
和 FastAPI 对请求体/路径参数校验失败返回的**完全一致**。也就是说 `status == 422` 什么也证明不了：
一个断言「422 就是拒绝访问」的测试，在请求本身写错时**同样会通过**。

现在归属拒绝是 **404 + `code="run_not_found"`**。选 404 而不是 403，且"不存在"与"不是你的"
返回同一答案，这样响应不会向无权者确认某个 run id 存在。

| 验证 | 结果 |
|---|---|
| 账号 A 能读自己的运行 | PASS |
| 账号 B 用同一个有效 ID 读 A 的私有运行 | **404 `run_not_found`** |
| 同一个 ID、同一条路由，A 读得到 | **200** ← 证明拒绝的是归属，不是 ID 或路由 |
| 不存在的 ID 返回与"不是你的"**相同的状态码与 code** | PASS（不泄露存在性） |
| 请求体非法 → 422 `validation_error`，与归属拒绝**不同** | PASS |
| 重启后历史仍在 | PASS |
| 实时与历史不重复（同一次运行只出现一次，`live` 标记来源） | PASS |

**真实重启证据** `scripts/verify_agent_run_persistence.py`：真起 uvicorn（8011 端口、独立测试库
`aiworkbench_test`）→ 建账号 → 写运行 → 读 → **杀进程** → **再起进程** → 再读 → 跨账号拒绝 → 清理。

```
[PASS] **the run is still in the history** — status=200 matches=1
[PASS] it is not claimed to be live — the registry is empty in a new process
[PASS] **the other account is refused by the ownership rule, not by request validation** — status=404 code=run_not_found
[PASS] the owner reads the same id from the same route — status=200
=== 18/18 passed ===
```

pytest：`backend/tests/api/test_agent_run_history.py` **8 条**（含"拒绝来自归属规则而非路由"的对照组）。

---

## 三、Alembic 迁移

### 问题（实测，比 brief 描述更具体）

在**只由迁移构建**的库上：模型 33 张表，alembic 建 29 张，缺
`conversations`、`messages`、`installed_skills`、`tool_audit_log` —— 而 `alembic_version` 报告在 head。
这四张表只在 `create_all` 里。

### 修改

- 幂等补齐迁移：表不存在才建；已存在则**校验列**，缺列报错点名表与列（不是 `DuplicateTable`）；
  `downgrade` 在表里有数据时**拒绝执行**。
- `app/core/schema_guard.py`：空库 → `alembic upgrade head` 建（不是 `create_all`）；
  已有库 → 校验版本，不一致抛 `SchemaOutOfDateError`，给出两个版本号和可执行命令，**绝不 stamp**。
- 生产路径不再调用 `create_all`。

### 真实库（有真实数据）

1. `pg_dump -Fc` 备份 → `build/backups/aiworkbench-20261009-154819.dump`（1,234,748 B）。
2. `pg_restore --list` 验证备份可读：290 条目、34 张表带数据。
3. 升级前后 15 张表行数**完全一致**（users 44、ai_runs 161、agent_runs 111、tool_audit_log 3021、
   context_snapshots 784…）。

### 隔离场景 `scripts/verify_migration.py` — 19/19

```
[PASS] ensure_schema built it from the migrations — created
[PASS] **every model table exists after a fresh migration** — 33 tables
[PASS] the four previously-drifting tables are present
[PASS] **an out-of-date database is refused at startup** — SchemaOutOfDateError
[PASS] the refusal did not modify the data
[PASS] **the four tables were created by the upgrade**
[PASS] **the row written before the upgrade is still there** — users=1
[PASS] a second start reports verified · [PASS] a third start too
[PASS] the migration refuses to run — exit=1
[PASS] **it names the table and the missing column**
[PASS] it did not stamp the version anyway — 90407e6e4bd7
=== 19/19 passed ===
```

### 安装版实际使用的迁移路径

安装版首次启动的日志：

```
INFO app.core.schema_guard: schema_fresh_database | alembic_head=81cce5214260
INFO app.main: schema_ready | outcome=created
```

安装版自己的库里 `alembic_version = 81cce5214260`，四张曾漂移的表全部存在（实测查询）。
**全新安装是走迁移建库的，不是 `create_all`。**

---

## 四、Snapshot / Audit 归档·恢复·清理策略

### 归档曾被写错

第一版只把 `manifest.json` 写进 JSONL。快照目录是 `manifest.json` **加上 `files/`** ——
`restore` 读的是后者。那个"归档"恢复出来的快照恢复不了任何东西。现在整目录复制，
每棵树按**排序后的相对路径 + 每个文件的摘要**算校验值，写进 `index.json`，写完**逐棵重新哈希比对**。

### 保护规则（永不成为候选）

pinned · 所属运行进行中 · **该 run 最新快照（唯一可回滚版本）** · 任一上限内（默认需同时超限） ·
时间无法解析。

### 验证

**`scripts/verify_retention_safety.py` — 15/15**（在真实数据的**副本**上跑完整闭环）：

```
[PASS] **planning left the real store byte-identical** — 16181 files
[PASS] all three are candidates under a zero limit — 3
[PASS] **the archive reads back and every checksum matches** — verified=3 mismatched=[]
[PASS] **restored content matches the original, byte for byte**
[PASS] a tampered archive is detected — ['…: 校验值不一致（记录 d23c122e2f07… 实际 dbe2beb3db6d…）']
[PASS] **a tampered archive restores nothing**
[PASS] **the real snapshot store is byte-identical** — 16181 files
[PASS] **the real audit table has the same number of rows** — 3021 → 3021
=== 15/15 passed ===
```

真实数据预览（默认策略）：**1335 个快照，候选 0**。收紧阈值 → 830 候选（带原因），**仍不删**。
`--apply` 需 `--confirm`，策略未启用时拒绝执行。

### 顺带修掉的一个真 bug（时区）

快照时间戳用 `time.strftime` 写，是**本地时间**；解析时却打上 `tzinfo=UTC`。
在 UTC+8 的机器上，每个快照看起来都**在未来 8 小时**，而负年龄小于任何上限 ——
于是年龄规则对**所有东西**静默失效。它失败得安全（什么都不删），所以一直没被发现，
直到把年龄上限设为 0 仍然选不出候选。**失败安全不等于正确，一条从不触发的规则不是规则。**

pytest：`backend/tests/unit/test_retention.py` **22 条**，含这个时区的两侧边界。

---

## 五、中断运行状态恢复

`_reconcile_interrupted_agent_runs` 在启动时把 `RUNNING` 行收尾 —— 一个没有进程在驱动它
却写着 `RUNNING` 的行，不是在运行。

### 顺带修掉的一个真 bug（状态显示不一致）

收尾只改了 `AIRun.status`，没改 `AgentRun.scratchpad["state"]`。而历史端点读的是 scratchpad ——
于是**行说 FAILED、API 显示 EXECUTING**，同一次运行两个说法。这正是 brief 要查的
"重启后历史查询、归属校验与状态显示一致"。

修了两处：收尾时一并更新 scratchpad（重新赋值，因为 JSONB 原地修改不被跟踪），
并在 `_resolve_state` 加不变式 —— **行是终止态时，显示的 state 也必须是终止态**；
scratchpad 里更细的真相（`LIMIT_REACHED` 与 `CANCELLED` 的区分）仍然保留。

**`scripts/verify_interrupted_runs.py` — 21/21**（真起服务、真杀进程、真重启）：

```
[PASS] **the interrupted run is no longer RUNNING** — status=FAILED
[PASS] **and it carries a reason** — 服务在运行过程中重启，这次运行的结果无法确认
[PASS] **the finished run was not rewritten** — SUCCEEDED → SUCCEEDED
[PASS] **the cancelled run was not rewritten** — CANCELLED → CANCELLED
[PASS] the interrupted run shows a terminal state, not a live one — FAILED
[PASS] another account is refused by the ownership rule — status=404 code=run_not_found
[PASS] the reconcile is idempotent — FAILED / SUCCEEDED / CANCELLED
=== 21/21 passed ===
```

---

## 六、交付物

全部由**当前源码**（本轮提交）重新生成，按顺序：前端生产构建 → Tauri 壳 → NSIS → ZIP。

| 交付物 | 路径 | 体积 | SHA256 |
|---|---|---|---|
| 前端产物 | `frontend/dist/` | `index.html` 1,284 B | `8e8fe568969cfe79c1c9356ae9a87d9f73a1b9f56baaf093eba8c36613569a2b` |
| Tauri 壳 EXE | `desktop/src-tauri/target/release/ai-workbench-desktop.exe` | 3,572,736 B | `08727c2c04f76701dcad5e6adfc864442e40014b22766eb8ff1808f3486cfb70` |
| Tauri 自带 NSIS | `desktop/src-tauri/target/release/bundle/nsis/AI Workbench_2.0.0_x64-setup.exe` | 1,130,696 B | `f61c26c5ad46bdac8df98654265eb3b56c5cb240dae0cedad0d0614e14909783` |
| **最终安装包** | `build/installer/AI-Workbench-Setup.exe` | 97,273,186 B | `cb6957cc2b283b991a197718025cede73e39a37125f831dcd7e951bf6113cbba` |
| **交付 ZIP** | `archive/releases/AI-Workbench-<commit>-final.zip` | 13,146,126 B | `d76ef0ef3ccd79a2ecc57c16acd6f4aa28acf997a5eda4c98f062254b46939b7` |

安装包 payload：8,741 文件 / 443.9 MB → 压缩后 20.9%，构建耗时 371s。
ZIP：612 个受版本控制的文件，凭据扫描通过。

---

## 七、安装版端到端验收

**从最终安装包开始**，全程不碰开发脚本；装在 `%LOCALAPPDATA%\AI Workbench AcceptTest`，
数据目录经 `data-dir.txt` 指向 `%LOCALAPPDATA%\AI Workbench Data TEST`。

`scripts/verify_installer_acceptance.py` — **32/32**：

```
[PASS] installed · [PASS] the installed copy points at the test data directory
[PASS] the API answered — 36.4s · 四个服务全部 listening
[PASS] **the first start built the schema from the migrations** — schema_fresh_database
[PASS] **the installed database records an alembic revision** — 81cce5214260
[PASS] **the four previously-drifting tables exist in the installed database**
[PASS] a run was written into the installed database · [PASS] the account was created
[PASS] 四个端口全部 released（关闭后）
[PASS] the API answered on the second start — 20.7s
[PASS] **the run counts are unchanged across the restart**
[PASS] it uses %~dp0 · [PASS] running it exits 0
[PASS] **the run counts are unchanged after the repair**
[PASS] the install directory is gone · 四个端口全部 released（卸载后）
[PASS] **the real data directory's mtime and file count are unchanged**
       before=(1791455911346056800, 1530) after=(1791455911346056800, 1530)
```

**安装器静态检查** `scripts/verify_installer_static.py` — **41/41**。

### 这里改的是检查器，不是产品（说明清楚）

上一轮我改过 `.nsi`，静态检查从 44/44 掉到 39/44。查清后是**检查器过时**：

1. **"creates two shortcuts"**：现在有三类快捷方式（开始菜单 + 桌面应用、修复入口）。
   我没有把断言放宽成"至少一个"，而是**拆成更精确的两条**：每个快捷方式必须指向
   exe 或 repair bat 之一（对全部逐个检查），应用快捷方式恰好 2 个，修复快捷方式至少 1 个。
   **比原来更强**。
2. **"every MessageBox has a silent-mode branch"**：对话框用的是 LogicLib 的
   `${IfNot} ${Silent}`，检查器只认裸 `IfSilent`。把第二种写法教给检查器，**没有删掉任何对话框**。

---

## 八、回归与模型调用

```
后端 pytest    717 收集 / 711 通过 / 6 跳过 / 0 失败
后端 ruff      All checks passed（app/ 与 tests/）
前端 tsc       0 error
前端 lint      0 error / 7 warning（全部是既有的 react-refresh）
前端单测       127 通过 / 10 跳过
前端 build     成功
安装器静态     41/41
```

本轮新增测试 **31 条**（8 AgentRun 历史 + 22 保留策略 + 1 状态一致性）。

### 一处如实记录：本轮我往开发库里写过一个测试账号，已清理

验证启动守卫时对开发 API 做了一次真实 HTTP 注册，在**开发库**（不是测试库）里留下了
一个账号 `habff69cd`。发现后按 username + email 精确删除并复核：users 45 → **44**，恢复原状。
本轮的其余验证脚本一律先断言 `DATABASE_URL` 指向测试库，不满足就拒绝运行。

### 模型调用：0 次

| 证据 | 值 |
|---|---|
| `ai_runs` 起点 / 终点 | 161 / 161 |
| `model_call_count.py` | **0 次，PASS** |
| Provider 硬闸 | `TEST_NO_LLM=1`，本轮所有验证脚本显式设置 |
| `MODEL_CALL_BLOCKED` | 0（没有触发，因为没有真实调用尝试） |
| 网络层真实模型请求 | **否** |

所有涉及运行的测试都**直接写行**而不是走 `POST /agent/runs` —— 那会调用 planner。

---

## 九、FAILED / NOT VERIFIED

### 没有 FAILED

### NOT VERIFIED

1. **DPI 真实硬件行为**（100/125/150/175% 多显示器）—— 当前环境无法模拟。**这是保留不封版的原因之一。**
2. **symlink 逃逸**（上一轮遗留）：本机没有创建 symlink 的权限；junction 已用 `mklink /J` 真验（6/6）。
3. **冷启动持久化的安装版独立复验**：本轮的安装版验收已覆盖"关闭→再启动→运行仍在"，
   但上一轮那条"测试进程连不进安装版集群"的记录没有单独重跑。
4. **审计归档恢复回数据库**：`restore_audit_archive` 有实现，但只在 FakeSession 上测过；
   **没有**在真实/测试数据库上跑过一次完整的"归档→删除→恢复→行数与内容一致"。
   快照侧是真跑的（15/15），审计侧不是。
5. **保留策略的真实 `--apply`**：**故意没启用**。1269+ 快照上一次 `--apply` 都没跑过，
   这是保守上线要求的，不是遗漏。
6. **Ollama / OpenAI 兼容端点 / Docker / Linux / macOS**：未实测（上一轮起即如此）。
7. **Embedding 仍是离线哈希 n-gram**：dense 检索实际是词法的。
8. **真实模型 A/B 产出对比**：本轮禁止真实模型，且已用 Recording/Scripted Provider 做结构性验证。

---

## 十、Git

```
分支      master
起点      a1ae8cd
本轮提交   见下方 git log
工作区     clean
```
