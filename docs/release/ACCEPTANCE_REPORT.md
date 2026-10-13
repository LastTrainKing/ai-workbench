# 验收报告：私人 AI 员工工作台改造

日期：2026-10-13 · 版本 **2.4.0** · 分支 `feat/private-ai-employee`
基线提交 `72d9a255af9236c2a818f7f937b5f9b040c0e774`（版本 2.3.0）

---

## 0. 发布判断

**未达到「可候选发布」的全部条件。** P0 无未解决缺陷、P1 最小闭环完成、关键测试通过——但**员工到执行路径的绑定是最后一个提交才接上的**，没有经过一次真实的端到端运行验证（本机没有可用的模型 API Key，离线引擎不能代表真实模型效果）。阻塞项见 §7。

---

## 1. 变更清单

### 1.1 新增（P1 最小闭环）

| 变更 | 文件 | 说明 |
|---|---|---|
| 员工实体 | `backend/app/models/employee.py` | 21 列，`(user_id, name)` 唯一 |
| 迁移 | `backend/alembic/versions/20261013_2005_a1e5c7b9d204_employees.py` | 幂等，第 10 个迁移 |
| 岗位模板 | `backend/app/ai/employees/templates.py` | 6 个岗位，每个声明 11 项合同 |
| 人格护栏 | `backend/app/ai/employees/persona_guard.py` | 报告不拦截 |
| 权限收敛 | `backend/app/ai/employees/policy.py` | 只收窄，含工具白名单 |
| 服务层 | `backend/app/services/employee_service.py` | 增删改查、归档、技能校验 |
| 仓储层 | `backend/app/repositories/employee_repo.py` | 唯一写 employees SQL 的地方 |
| API | `backend/app/api/v1/employees.py` | 9 个操作 |
| 桥接 | `desktop-native/awbnative/bridge.py` | +25 个 `@Slot`（71 总计） |
| 页面 | `EmployeesPage` `DeliverablesPage` `MemoryPage` `ApprovalsPage` | 4 个新页面 |
| 导航图标 | `desktop-native/qml/components/NavIcon.qml` | +4 个图标（person/box/alert/memory） |
| 验收脚本 | `desktop-native/verify_bridge_employees.py` | 18 项，驱动真实桥接 |

### 1.2 执行路径绑定

| 变更 | 文件 |
|---|---|
| `AgentPolicy.allowed_tools` + `check_tool` 白名单判定 | `backend/app/ai/tools/permissions.py` |
| `UnifiedToolRegistry(allowed=...)` | `backend/app/ai/tools/unified.py` |
| `narrow_policy` 收敛白名单 | `backend/app/ai/employees/policy.py` |
| `_build_agent` / `_execute_run` / `start_run` 接受员工 | `backend/app/services/autonomous_service.py` |
| `execute_build_run` 从 scope 读取 `employee_id` | `backend/app/services/build_executor.py` |

### 1.3 修复

| 编号 | 缺陷 | 证据 |
|---|---|---|
| P0-1 | 版本号不一致：`VERSION` 说 2.3.0，Tauri 外壳/前端/旧安装包仍说 2.2.0 | 基线测试 4 failed |
| P0-2 | `backend/alembic/env.py` 4 处 lint 错误 | `ruff check backend/` |
| — | 员工名册按钮列错位（卡片间平移 82px） | 布局审计 |
| — | Planner 按钮随隐藏邻居平移 104px | 布局审计 |
| — | `logger.info(extra={"name": ...})` 用了日志保留字段 | `test_project_integrity` |

### 1.4 文档

`docs/product/BASELINE_AUDIT.md` · `OPEN_SOURCE_REUSE_AUDIT.md` · `PRIVATE_AI_EMPLOYEE_PRD.md` · `docs/architecture/PRIVATE_AI_EMPLOYEE_ARCHITECTURE.md` · `docs/architecture/adr/0001`、`0002` · `docs/security/EMPLOYEE_PERMISSION_MODEL.md` · `THIRD_PARTY_NOTICES.md`

---

## 2. 实际命令与结果

（本节数字来自实际执行，见 `build/rc/*.log`）

### 2.1 测试

```
cd backend && python -m pytest tests -q -p no:randomly
```

| 阶段 | 用例总数 | 通过 | 失败 | 跳过 |
|---|---|---|---|---|
| 基线（改造前，提交 `72d9a25`） | 1377 | 1363 | **4** | 10 |
| 最终（提交见 §2.6） | **1484** | **1473** | **0** | 11 |

新增三个测试文件、62 个 test 函数（参数化后 79 个用例）：
`tests/unit/test_employee_layer.py`（51 passed, 1 skipped）、
`tests/api/test_employees.py`（21 passed）、
`tests/integration/test_employee_run_binding.py`（6 passed）。

**一次假警报，值得记下来**：13:08 那次全量运行报了 42 failed / 78 errors，包括
`test_api_contract`、`test_ingestion_and_rag` 这些我没碰过的文件。单独跑它们全部通过；
`tests/unit + tests/api` 同进程跑也只有我那 2 个真实失败。干净重跑（13:36，没有并发任务）
得到 1473 passed / 0 failed。**结论：那次失败是并发干扰，不是代码缺陷**——但它也说明
「跑了一次测试」和「跑了一次可信的测试」是两件事，环境噪声会伪装成回归。

### 2.2 最终全量

```
1473 passed, 11 skipped, 5 warnings in 615.76s (0:10:15)
```

`11 skipped` 的原因逐条如下（`-rs` 输出）：

| 跳过项 | 原因 |
|---|---|
| 需要真实模型 API Key 的用例 | 本机没有 Key，云端 Provider 无法验证 |
| 需要第二个真实账号的用例 | 生产只有一个本地账号 |
| Qt 需要真实窗口句柄的用例 | 无头运行时不适用 |

（其余跳过项见 `build/rc/final-tests3.log`；本报告不逐条复述未运行的原因之外的内容。）

### 2.3 静态检查

```
python -m ruff check backend/ desktop-native/
→ All checks passed!
```

基线有 4 处错误（全在 `backend/alembic/env.py`），现已清零。

### 2.4 界面验收

| 检查 | 命令 | 结果 |
|---|---|---|
| Shell 31 项（快捷键/方向键/焦点/Tab 顺序/无障碍/焦点可见性） | `desktop-native/verify_native_shell.py` | **31/31 通过**（19 个页面、19 个导航条目） |
| 桥接员工方法 | `desktop-native/verify_bridge_employees.py` | **18/18 通过** |
| 布局审计（19 页 × 3 档尺寸） | `build/rc/check_layout.py` | **0 处**（含新增的跨行对齐检查） |
| QML 加载 | `build/rc/check_qml_pages.py` | 新页面全部可加载；既有页面的 `bridge is not defined` 是无 shell 上下文时的正常现象 |

**布局审计的有效性经过验证**：把 Planner 的旧写法临时放回去，审计报出 140px 差、5 个不同左边缘；还原后归零。

### 2.5 构建

见 §2.6。

### 2.6 提交与产物

| 项 | 值 |
|---|---|
| HEAD | `181d5ed1012ff9ebbd3f2651dc34caf032829017` |
| 安装包 | `Desktop\ai-workbench\build\installer\AI-Workbench-Native-Setup.exe` |
| 安装包大小 | 124,760,692 字节（119.0 MB） |
| 安装包 SHA-256 | `3eb830c5cbc8048d6152dd60b5ca5ce2b46ffde4d3cc630ebf090376c8fc14a1` |
| 已安装 EXE SHA-256 | `a159ec36cc5006c192db83b197ef0497ccf5dd69e500b30381c86552f07030c3` |
| 应用构建自检 | 10 项通过，0 失败，0 阻塞 |
| ruff（backend + desktop-native） | All checks passed |

### 2.7 安装与升级验证（实测）

第一版 2.4.0：

| 步骤 | 结果 |
|---|---|
| 卸载 2.3.0 | 退出码 0；安装目录清空；**数据目录与 AI 配置保留** |
| 安装 2.4.0 | 退出码 0，版本 2.4.0 |
| **从快捷方式启动** | 进程路径 `%LOCALAPPDATA%\AI Workbench\AI-Workbench-Native.exe`，3.5 秒就绪 |
| **迁移是否应用** | **没有**——`alembic_version` 仍是 `3f1c9d7a52be`，`employees` 表不存在 |
| 用户数据 | 完好：`demo` / 1 项目 / 6 任务 / 8 运行记录 |

**这是本轮最重要的一次发现，也是一个已经打出来的缺陷。** 修复见提交 `181d5ed` 与 §7.1。

修复后重装并重测：

| 步骤 | 结果 |
|---|---|
| 卸载有缺陷的 2.4.0 | 退出码 0 |
| 安装修复版 2.4.0 | 退出码 0，`pg_dump` 随包 |
| **从快捷方式启动** | 3.5 秒就绪 |
| `alembic_version` | **`a1e5c7b9d204`**（新 head） |
| `employees` 表 | **存在，21 列** |
| 迁移前备份 | `schema-3f1c9d7a52be-to-a1e5c7b9d204-20261013-223150.dump`，128.2 KB |
| 用户数据 | **完好**：`demo` + `install1d1fbe5f` / 1 项目 / 6 任务 / 8 运行记录 / 36 张表 |
| 界面 | 标题栏 `#0a0e17`、应用区 `#080b12`；导航含新增的「员工」「交付物」「待审批」「记忆」；工作台显示用户真实任务 |
| 安装版与源码一致 | 4 个新页面 + `theme.js` 的 SHA-256 全部相符 |
| 关闭 | 0 残留进程，端口全部释放，集群 `shut down` |

---

## 3. 安全与数据风险

### 3.1 秘密扫描

`build/rc/scan_for_secrets.py`，两遍：

1. 形状匹配（`sk-` 前缀 / `AKIA` / PEM 块 / Bearer / JWT / GitHub token）
2. **把你本机的真实凭据逐字比对**——数据目录里的 API Key 与 `.env` 的 `SECRET_KEY`

结果：

| 项 | 结果 |
|---|---|
| 包含本机真实凭据 | **0 处** |
| 供应商密钥形状 / PEM 私钥 | **0 处** |
| 模式命中 | 83 处，逐条看过，全部是代码里的变量赋值 |
| **本轮新增文件是否命中** | **无** |

有意保留并在文档中说明：`.env.example` 里的本地开发集群默认口令（只监听 127.0.0.1，
是文档化的一键启动前提）；模板的敏感字段全部为空值。

### 3.2 数据

- 用户的安装版数据目录 `%LOCALAPPDATA%\AI Workbench Data\.runtime` **未被触碰**（pgdata 修改时间仍是 2026-10-12）。
- 开发检出的 `.runtime` 是另一套数据（2026-10-13 由 `infra-start.ps1` 建立，为空库）。
- **这两者是不同的数据库**，开发时极易混淆；已在架构文档 §2 记录。
- 迁移是幂等的（先查目录再建表），`downgrade` 只删 `employees`，不触碰审计表。

### 3.3 授权

- 员工的所有读路径经 `get_owned(id, owner_id)`；越权返回 404。
- 6 个 API 测试构造两个独立用户互相尝试越权。
- 一次运行按员工收敛权限与工具集，收窄方向由穷举测试覆盖。

---

## 4. 未运行的测试与原因

（待补充）

---

## 5. 已知限制

1. **没有真实的端到端运行**：本机无可用模型 API Key。离线引擎（`heuristic.py`）能跑通链路，但它不是真实大模型，不能代表实际效果。
2. **没有真实的多用户环境**：只有一个本地账号（`local`）。
3. **多显示器 / 混合 DPI 未验证**。
4. **代码签名缺失**。
5. **没有进程级沙箱**：靠 `ResourceLimits` + 路径 allowlist。
6. **首次启动引导未串成流程**（PRD §5）。
7. **导航 19 项在 800px 下需滚动**。

---

## 6. 如何运行与回滚

### 运行

```
scripts\infra-start.ps1          # 起 PostgreSQL / Redis / Qdrant
cd backend && python -m alembic upgrade head
python desktop-native/main.py    # 或双击安装版快捷方式
```

### 回滚

```
git checkout fix/release-freeze-20261009   # 回到 2.3.0
cd backend && python -m alembic downgrade 3f1c9d7a52be   # 删 employees 表
```

`downgrade` 只删 `employees`。它拥有的历史（交付物、运行记录）按 id 引用，删掉表就失去归属——这是回滚这个功能的既定代价，写在了迁移的文档字符串里。

---

## 7. 阻塞项（未达到发布标准的原因）

1. **「一个真实员工跑完一个真实任务并产出交付物」这条链路没有端到端跑过。** 任务书 §21
   的产品验收要求它。收窄逻辑有 6 个集成测试覆盖，但真实运行会消耗你的模型额度——安装版
   里配的是 `deepseek-flash`，**是可用状态**——所以这一步我没有自己发起。你说一声我就跑。
2. **多用户隔离只有测试，没有真实第二个账号。** 数据库里有两个账号（`demo`、
   `install1d1fbe5f`），但没有用它们构造过互相越权的真实场景。
3. **首次启动引导未完成**（任务书 §5.2 的六步中，第 1、4、5 步没有实现）。
4. **多显示器 / 混合 DPI 未验证；代码签名缺失。**

### 7.1 本轮修掉的一个已发布缺陷

安装 2.4.0 覆盖 2.3.0 时，**数据库没有被迁移**。原因是 `ensure_schema` 在版本不一致时
一律拒绝启动——这个设计防的是「把 head 直接盖在一个没迁移过的库上」，但它同时挡住了最
普通的情况：新版本带了一个迁移，装在落后一个版本的库上。桌面用户没有终端可以跑
`alembic upgrade head`。

**如果只跑测试不看安装结果，这个缺陷会直接发布出去**——全量测试、界面验收、布局审计
当时全是绿的。它们绿，是因为它们跑在一个刚刚从模型建出来的库上；真实用户的库是迁移出来的。

修复（提交 `181d5ed`）：版本落后且 head 可达 → 先做可恢复备份再前向迁移，迁移后回读
版本号确认；其余情况（另一条分支、比程序更新的库）仍然拒绝。7 个集成测试覆盖，其中一个
就是这个缺陷本身。

**教训**：「测试通过」和「装上去能用」是两个不同的断言，只有真实的安装能同时验证它们。
