# FIX-ROUND-18 — AgentRun 持久化 / Alembic 统一 / 保留策略

工作区：`C:\Users\user\Desktop\ai-workbench`
**本轮真实模型调用：0 次**（`ai_runs` 161 → 161）

---

## 一、结论

三项全部完成，每项都有真实验证：

| 项 | 状态 | 证据 |
|---|---|---|
| 一、AgentRun 历史持久化 | **PASS** | 6 条 pytest + `verify_agent_run_persistence.py` **17/17**（真起服务、真杀进程、真重启） |
| 二、Alembic 与 create_all 统一 | **PASS** | 6 条 pytest + `verify_migration.py` **19/19**（空库 / 旧库升级 / 重复启动 / 漂移拒绝）+ 真实库升级前后行数一致 |
| 三、Snapshot / Audit 保留策略 | **PASS**（机制）/ 默认零删除 | 14 条 pytest + 真实数据预览（默认策略 **0 候选**） |

回归：后端 **701 通过 / 0 失败 exit 0** · ruff 全过 · 前端 tsc 0 · lint 0 error · 127 通过 · build 成功。

**未做**：交付物重建（EXE / NSIS / ZIP）。本轮的改动集中在后端，但按纪律仍需重建后才算可交付 —— 见第六节。

---

## 二、第 1 项：AgentRun 历史持久化

### 问题（真实存在的）

`GET /agent/runs` 只读进程内的 `RunRegistry`。它的 docstring 自己写着
`"Runs currently held by this process"`。**进程一重启，注册表就是空的**，用户启动过的每一次运行都从端点消失，
而行数据安静地躺在 `agent_runs` 里（实测 111 行）。「我的运行去哪了」的答案是
「在数据库里，但没有任何东西读它」。

### 修改

| 文件 | 核心逻辑 |
|---|---|
| `backend/app/services/autonomous_service.py` | 新增 `run_history()`（按 `AgentRun.owner_id` 过滤、按 `AIRun.created_at` 倒序、分页 + 计数）、`history_row_for()`（按 run_id + owner 取单条）、`merge_live_and_history()`（以数据库为骨架、注册表为覆盖层，按 `run_id` 去重）、`_STATUS_TO_STATE`（行状态 → 合法 `RunState`） |
| `backend/app/api/v1/agent_runs.py` | `GET /runs` 改为合并实时 + 历史，支持 `limit` / `offset` / `status_filter` / `live_only`；`GET /runs/{id}` 改为 `_owned_run()` —— 有实时控制器就用它，否则回落数据库行，**两条路径都校验 owner** |
| `backend/app/main.py` | 新增 `_reconcile_interrupted_agent_runs()`：启动时把上一进程留下的 `RUNNING` 行收尾为 `FAILED` 并写明原因（**原来只收尾 BuildRun，AIRun/AgentRun 从不收尾**，重启后运行会永远停在 RUNNING） |

去重的方向是刻意的：**数据库是列表，注册表是覆盖层**。同一个运行两处都在（运行时在注册表、从创建那刻起在表里），
两边都列会让用户看到同一次运行两遍。反过来，**绝不因为注册表不记得某行就丢掉它** —— 那正是重启做的事。

### 验证

**pytest（6 条）** `backend/tests/api/test_agent_run_history.py`：
存了能读到 · 实时与历史**只出现一次**且 `live=true` · 跨账号列表与 by-id 都读不到（本人仍可读，证明拒绝的是归属不是路由）·
四种状态都映射成 UI 能渲染的 `RunState` · 分页不重叠且最新在前 · 状态过滤下推到数据库。

**真实重启（17/17）** `scripts/verify_agent_run_persistence.py` —— 起真 uvicorn（8011 端口、**独立测试库** `aiworkbench_test`），
建账号、写运行、读、**杀掉进程**、**再起进程**、再读：

```
[PASS] DATABASE_URL resolves to the test database — 127.0.0.1:5432/aiworkbench_test
[PASS] the run is in the list · [PASS] it is not marked live · [PASS] its goal survived the round trip
[PASS] the old process is gone · [PASS] a new process is listening (empty registry)
[PASS] **the run is still in the history** — status=200 matches=1
[PASS] the entry is identical to before the restart
[PASS] it is not claimed to be live — the registry is empty in a new process
[PASS] the stored run is readable by id
[PASS] the other account's list does not contain it · [PASS] cannot read it by id — status=422
[PASS] the run row was removed · [PASS] the accounts were removed
=== 17/17 passed ===
```

脚本在写任何数据前先断言 `DATABASE_URL` 指向测试库，否则拒绝运行。

### 一处自我更正

`test_every_by_id_endpoint_goes_through_the_ownership_check`（既有测试）红了 —— 它断言每个 by-id 端点都调
`_owned_controller(`。我改了 `get_run_state` 的路径。**没有放宽断言**：抽了 `_owned_run()`，
并把测试**加强**为同时覆盖两个归属助手、且断言两者都真的比较 `owner_id`。
新路径（读数据库行）比原路径的表面积更大，所以断言是变强了不是变弱了。

---

## 三、第 2 项：Alembic 与 create_all 统一

### 问题（真实存在的，且比 brief 描述的更具体）

实测（在只由迁移构建的库上）：

```
模型定义表 33, alembic 建表 29
★ 模型有、迁移没有: ['conversations', 'installed_skills', 'messages', 'tool_audit_log']
```

**四张表只由 `create_all` 建过，任何迁移里都没有它们**，而 `alembic_version` 却报告自己已在 head。
「版本号说已是最新」和「结构就是迁移描述的那样」已经不是同一句话了。

### 修改

| 文件 | 核心逻辑 |
|---|---|
| `backend/alembic/versions/20261009_1547_81cce5214260_*.py` | 补齐这四张表。**幂等**：表不存在才建；已存在则校验列是否齐全，缺列直接报错并点名表与列。`downgrade` 在表里有数据时**拒绝执行** —— 删掉用户的会话/技能/审计不是回滚 |
| `backend/app/core/schema_guard.py` | 新增。空库 → 跑 `alembic upgrade head` 建（不是 `create_all`）；已有库 → 校验 `alembic_version == head`，不一致抛 `SchemaOutOfDateError`，**给出两个版本号和可执行命令，绝不 stamp**；有表但无 `alembic_version` → 明确拒绝，不假设它是最新 |
| `backend/app/main.py` | `_bootstrap_schema` 里的 `create_all` 换成 `ensure_schema` |

### 数据安全（真实库）

真实库有 users=44 / ai_runs=161 / agent_runs=111 / tool_audit_log=3021 / context_snapshots=784。

1. **先备份**：`pg_dump -Fc` → `build/backups/aiworkbench-20261009-154819.dump`（1,234,748 B）。
2. **验证备份可读**：`pg_restore --list` → 290 TOC 条目、34 张表带数据。
3. **记录升级前行数**，执行 `alembic upgrade head`，**记录升级后行数**：

```
OK  users 44→44 · projects 3→3 · tasks 256→256 · memory_entries 16→16 · installed_skills 6→6
OK  knowledge_bases 2→2 · ai_runs 161→161 · agent_runs 111→111 · tool_audit_log 3021→3021
OK  deliverables 57→57 · conversations 0→0 · messages 0→0 · context_snapshots 784→784
OK  document_chunks 104→104 · documents 7→7
结论: PASS —— 所有表行数完全一致
```

4. 启动日志确证走了新守卫：`schema_verified | revision=81cce5214260 tables=33` / `schema_ready | outcome=verified`。

### 验证

**pytest（6 条）** `backend/tests/unit/test_schema_guard.py`：只有一个 head · 空库由迁移构建（不是 `create_all`）·
版本不一致被拒且**没有触发升级** · 有表无版本被拒且提示不 stamp · 只有 `alembic_version` 的库算空库 · 真实测试库 verify 通过。

**真实场景（19/19）** `scripts/verify_migration.py`，全部在独立 scratch 库上：

```
[PASS] ensure_schema built it from the migrations — created
[PASS] **every model table exists after a fresh migration** — 33 tables
[PASS] the four previously-drifting tables are present
[PASS] the previous head really is missing those four tables
[PASS] a row was written before the upgrade — users=1
[PASS] **an out-of-date database is refused at startup** — SchemaOutOfDateError
[PASS] the refusal did not modify the data
[PASS] `alembic upgrade head` succeeds
[PASS] **the four tables were created by the upgrade**
[PASS] **the row written before the upgrade is still there** — users=1
[PASS] a second start reports verified · [PASS] a third start too
[PASS] the migration refuses to run — exit=1
[PASS] **it names the table and the missing column**
[PASS] it did not stamp the version anyway — 90407e6e4bd7
=== 19/19 passed ===
```

**两处自我更正**（都记在脚本注释里）：
1. 第一版把「旧库升级」写成调 `ensure_schema` —— 它**正确地拒绝了**。升级是运维动作（`alembic upgrade head`），
   应用只负责校验。脚本改成先断言拒绝、再走文档路径升级。
2. 第一版用 `asyncio.run(_ensure())` 测漂移，但版本校验先于迁移执行，列检查根本没跑到。改为直接跑
   `alembic upgrade head` 触发迁移自身的检查。
3. 脚本里 `dispose_engine()` 原来没放在 `finally`，`ensure_schema` 抛异常时引擎留在已关闭的事件循环上，
   下一次调用报 `Event loop is closed` —— 一个看起来像迁移问题的测试问题。

---

## 四、第 3 项：Snapshot / Audit 保留策略

### 现状（真实数据）

```
快照 1269 个（其中 512 个 run_id = "test-run"，是测试残留；644 个 run_id 为空）
tool_audit_log 3021 行
最早的快照 2026-10-05，最晚 2026-10-09
```

### 设计：决定与执行分开

| 文件 | 核心逻辑 |
|---|---|
| `backend/app/ai/agent/retention.py` | `RetentionPolicy`（可配置，**默认 `enabled=False`**）；`plan_snapshot_cleanup` / `plan_audit_cleanup` **只做判断，不删任何东西**；`apply_*` 先归档、**读回校验**、再删；`policy_from_settings()` |
| `backend/app/ai/agent/snapshot.py` | 新增 `pinned` 字段（manifest 持久化）+ `set_pinned()` —— 用户说「留着」的唯一表达方式 |
| `backend/app/core/config.py` | 6 个 `retention_*` 配置项，全部可改，全部惰性 |
| `scripts/retention_report.py` | 预览 CLI；`--apply` 需要 `--confirm`，且策略未启用时拒绝执行 |

**永不成为候选**：
- `pinned`（用户明确保留，任何阈值都不覆盖）
- 所属运行仍在进行（注册表 + 数据库 `RUNNING`）
- **该运行最新的快照** —— 回滚用的是它，删了运行就不可回滚，哪怕「保留了一个版本」
- 在任一上限之内（默认**同时超出数量与时间**才算候选）
- 时间无法解析（读不懂的日期是保留的理由，不是删除的理由）

### 验证

**pytest（14 条）** `backend/tests/unit/test_retention.py`：pinned 不被选 · 每个 run 最新快照不被选 ·
进行中的 run 受保护（对照：已结束 run 的旧快照可回收）· 任一上限内即保留 · 只有同时超限才成候选 ·
时间无法解析则保留 · 关闭 `require_both_limits` 会回收更多 · **规划不动存储** · 计划报告可回收量 ·
apply 先归档后删除 · **归档失败则不删任何东西** · 空计划明确报告 · 审计中活跃 run 的行受保护 ·
审计归档失败同样不删。

**真实数据预览（只读）**：

```
=== retention policy ===  enabled: false · 数量 200 / 30 天 · 审计 50000 / 180 天 · 同时超出才算候选
受保护的运行（进行中）: 12
--- snapshot ---  总数 1269 · 候选 0 · 受保护 1269 · 可回收 0.00 MB
   1171  在时间范围内
     86  该运行最新的快照，是唯一可回滚的版本；在时间范围内
     12  所属运行仍在进行
--- audit ---     总数 3021 · 候选 0 · 受保护 3021
只生成预览，未做任何改动。
```

**默认策略下候选为 0** —— 最老的快照才 4 天。这正是 brief 要的保守上线：已有数据全部保留。

收紧阈值以证明规则有牙齿（**仍然只预览**）：

```
RETENTION_SNAPSHOT_MAX_COUNT=100 RETENTION_SNAPSHOT_MAX_AGE_DAYS=1 …
--- snapshot --- 候选 632 · 可回收 1.30 MB
   受保护原因：539 在时间范围内 / 69 最新快照（唯一可回滚）/ 17 两者 / 12 运行中
--- audit ---    候选 2021 · 受保护 1000
```

两道闸也验证过：`--apply` 不带 `--confirm` → 不改动；`--apply --confirm` 但 `enabled=false` → 拒绝执行。

### 一处自我更正

归档失败只捕获了 `OSError`。非法路径的 `mkdir` 抛的是 **`ValueError`** —— 那会让归档失败逃逸成 traceback，
而不是一条 `failed` 记录，也就是「删除发生了、但它的记录没人看到」。已放宽为 `(OSError, ValueError)`，
并加了一条断言专门盯这个。

---

## 五、模型调用：0 次

| 证据 | 值 |
|---|---|
| `ai_runs` 起始 / 结束 | 161 / **161** |
| `model_call_count.py` | **0 次调用，PASS** |
| Provider 硬闸 | `TEST_NO_LLM=1`，本轮所有验证脚本都显式设置 |
| 网络层真实模型请求 | **否** —— 所有测试用 fixture / Scripted Provider / 直接写行，不发消息、不跑 planner |

第 1 项的持久化测试**刻意不走** `POST /agent/runs`：那会调用 planner。测试直接写 `AIRun` + `AgentRun` 行 ——
端点读的就是这些行。

---

## 六、回归

```
后端 pytest    701 通过 / 0 失败，exit 0
后端 ruff      All checks passed（app/ 与 tests/）
前端 tsc       0 error
前端 lint      0 error / 7 warning（全部是既有的 react-refresh）
前端单测       127 通过 / 10 跳过
前端 build     成功（24.2s）
```

新增测试 **26 条**（6 AgentRun 历史 + 6 schema 守卫 + 14 保留策略）。

### 交付物：**未重建**

`AI-Workbench-Setup.exe` 仍是上一轮的产物，**不对应当前源码**。本轮改动全在后端
（`backend/app`、`backend/alembic`、`scripts/`），重建约 6 分钟。**没有重建就不算可交付**。

---

## 七、仍然 NOT VERIFIED

- **第 1 项的失败/取消状态端到端**：状态映射有单测（RUNNING/SUCCEEDED/FAILED/CANCELLED），
  但**没有**真跑一次失败的 agent 运行再重启去看它。要真跑就必须让 planner 工作。
- **第 2 项在安装版上的行为**：验证都在开发栈与 scratch 库上。安装版自带 Python 与自己的集群，
  首次安装时 `vendor/runtime/python` 能否执行 `alembic` 未实测。
- **第 3 项的自动清理**：**故意没启用**。归档 → 删除的完整链路只有隔离测试（FakeManager / FakeSession），
  没有在真实 1269 个快照上执行过一次 `--apply`。这是保守上线要求的，不是遗漏。
- **审计归档的可恢复性**：归档写成 JSONL 并读回校验，但**没有验证过从归档恢复回数据库**。
- 上一轮遗留：安装器端到端误杀复验 · 冷启动持久化（测试进程连不进安装版集群）· 第 35 条 DPI · 第 39 条绝对路径解析。
