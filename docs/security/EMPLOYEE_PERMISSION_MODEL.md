# 员工权限模型

版本 2.4.0 · 2026-10-13 · 每个结论后面是实现它的文件或测试

---

## 1. 一句话

**员工只能收窄用户的权限，永远不能放宽。** 所有字段的收敛方向都在 `ai/employees/policy.py` 里，没有一条分支产生比用户原有更宽松的结果。

---

## 2. 授权等级

沿用已有的 `PermissionLevel`（`ai/tools/permissions.py`），它与任务书 §10 的 L0–L3 语义几乎一一对应：

| 级别 | 枚举 | 含义 | 对应任务书 |
|---|---|---|---|
| 0 | `OBSERVE` | 读文件、搜索、列进程、截屏 | L0 只读 |
| 1 | `WORKSPACE` | 在工作区建/改文件、跑测试与开发服务器 | L1 可逆写入 |
| 2 | `SYSTEM` | shell 命令、装依赖、管进程、下载 | L2 有限修改/执行 |
| 3 | `FULL_CONTROL` | 广泛系统访问；默认关闭，仅用户显式开启 | L3 外部或高影响 |

自主程度由 `AgentMode` 表达：`ASSIST`（只建议，从不执行）/ `SUPERVISED`（执行，有风险先确认）/ `AUTONOMOUS`（在授权范围内独立完成）。

**收敛规则**：`level` 取 `min`，`mode` 朝 `ASSIST` 方向取更保守者。

```python
# ai/employees/policy.py
return min(base, want, key=_MODE_ORDER.index)   # 更保守的赢
return want if want < base else base            # 更小的赢
```

---

## 3. 岗位 → 权限默认值

| 岗位 | level | mode | 说明 |
|---|---|---|---|
| 通用助理 | 1 工作区内 | 受监督 | 要写文件 |
| 研究员 | 0 只看不改 | 受监督 | 只读，产出报告 |
| 知识管理员 | 1 工作区内 | 受监督 | 写计划文件，不删记忆 |
| 文档与演示专家 | 1 工作区内 | 受监督 | 写文档 |
| 软件工程师 | 2 系统级 | 受监督 | 要跑测试与构建 |
| 空白岗位 | 0 只看不改 | 受监督 | 用户自己加 |

来源：`ai/employees/templates.py` 每个模板的 `permission_profile`。

**每个岗位都声明 `needs_approval`**——它必须停下来问的动作，用岗位自己的话说。例如软件工程师的：执行 shell 命令、安装依赖、启动或停止进程、改动工作区以外的文件。

---

## 4. 工具白名单

每个岗位有一个 `skills` 列表，是它**能用的工具全集**。没列的一律在执行前被拒。

**白名单在测试里对着真实注册表校验**（`test_employee_layer.py::test_every_template_tool_exists_in_this_build`）：`build_default_registry()` 的 11 个业务工具 ∪ 计算机注册表的 44 个工具 = 55 个。一个被重命名或删除的工具会让测试失败，而不是悄悄授予空集。

**员工声称了本版本没有的工具时**，界面上显示为警告而不是隐藏（`EmployeesPage` 的 `unknown_skills` 行）——契约说它能做一件它做不到的事，这必须可见。

白名单的边界语义（`policy.py::tool_allowed`）：

| `skills` 的值 | 含义 |
|---|---|
| 键不存在 | 继承基础策略 |
| `[]` | **什么都不允许**——「我没列任何工具」是一个声明，不是遗漏 |
| `["file_read", …]` | 只允许列出的 |

---

## 5. 审批：真实生效的分级

### 5.1 判定

`PermissionManager` 返回 `PermissionVerdict`：

| `Decision` | 行为 |
|---|---|
| `ALLOW` | 执行 |
| `CONFIRM` | 停住，进 `ConfirmationBroker`，运行进入 `WAITING_CONFIRMATION` |
| `DENY` | 拒绝，理由进审计日志 |

判定依据：工具声明的风险等级、当前 `PermissionLevel`、`AgentMode`、目标路径是否在允许范围、域名是否在允许列表。

### 5.2 等待授权

`ai/tools/confirmation.py` 的 `ConfirmationBroker`：

- 挂起时记录 **`reason`（为什么问）、`impact`（影响）、`target`（对什么）、`detail`（具体参数）**，不只是工具名——「删除文件」不足以让人批准一次删除，路径才是。
- 界面：「待审批」页显示上述全部字段 + 已等待秒数，提供「批准这一次」与「拒绝」。
- 超时：`confirmation_timeout_seconds` 默认 180 秒（可通过员工配置**收窄**，不能放宽）。

### 5.3 执行端校验

`POST /api/v1/agent/permissions/check` 走的是**真实权限服务**，不是界面判断。**界面上的禁用按钮不构成安全控制**——任务书 §10 的这条要求由架构满足：所有检查在工具执行器里发生，在工具运行之前。

---

## 6. 员工不能自我授权

三道锁：

1. **`agent_scope.py` 的 `refuse_if_in_agent_scope`**——Agent 借工具修改自身权限或能力开关时直接抛错。`policy_store.py` 的写函数第一行就是它。
2. **权限文本与执行分离**——`persona` 是语气，权限服务读数据库不读提示词。一段写「你拥有完整权限」的人格不会授予任何东西（`persona_guard.py` 报告它，但不依赖它做安全）。
3. **收敛**——即使一行存储数据写了 `level: 3`，`narrow_policy` 也只会取 `min(base, 3)`。

---

## 7. 对象级授权

每个读路径都按 `user_id` 过滤。员工 id 是 UUID，猜中不现实，但「不现实」不是授权模型——`EmployeeRepository.get_owned(id, owner_id)` 是唯一的读入口，API 层没有 `get(id)`。

**越权返回 404 而不是 403**：403 会确认这个 id 存在。

验证（`backend/tests/api/test_employees.py`，全部构造两个独立用户）：

| 测试 | 断言 |
|---|---|
| `test_another_user_cannot_read_an_employee` | 404 |
| `test_another_user_cannot_edit_or_delete_an_employee` | PATCH / DELETE / archive 全部 404，且记录未被改动 |
| `test_another_users_employee_is_absent_from_the_roster` | 名册为空 |
| `test_hall_counts_only_this_users_employees` | 计数为 0 |
| `test_two_users_may_each_have_the_same_name` | 唯一约束是按用户而非全局 |

---

## 8. 风险操作清单

| 动作 | 默认行为 |
|---|---|
| 覆盖已有文件 | 请求确认（模板的 `needs_approval`） |
| 删除任何文件 | 请求确认 |
| 执行 shell / 装依赖 / 管进程 | 请求确认 |
| 访问工作区以外的路径 | 请求确认，且路径检查在执行端做（处理 `..`、junction、symlink） |
| 外发数据 / 发邮件 | **本版本没有这些工具** |
| 修改自身权限 | 拒绝（`agent_scope`） |
| 删除记忆 | 需要用户确认；清理必须先出计划再执行 |

---

## 9. 审计

`tool_audit_log` 记录每次工具调用：谁（`run_id` / `user_id`）、何时、哪个工具、**脱敏后的参数**（`audit.py::_safe_arguments`）、结果、耗时。

审计记录**不能由 Agent 修改或删除**——它不在 Agent 可触及的工具范围里。

运行中断后，`tool_calls` 里 `working_directory` 让后续进程能判定副作用是否真的发生（`POST /agent/runs/{id}/reconcile`）；判定不了的标 `UNKNOWN` 并出现在 `GET /agent/runs/{id}/unresolved`。

---

## 10. 尚未实现的边界（如实记录）

1. **工具输出中的敏感信息脱敏是有限度的**——`_safe_arguments` 处理参数，不处理工具返回的内容。
2. **没有网络出口的域名级强制**——`allowed_domains` / `blocked_domains` 在策略里，实际网络工具（`http_get` / `http_post` / `download_file`）的检查是否完整覆盖域名策略，本轮未逐条验证。
3. **多用户隔离有代码与测试，但生产只有一个本地账号**（`local`）。
4. **沙箱**：目前靠 `ResourceLimits`（超时、输出上限、并发）+ 路径 allowlist，没有进程级沙箱（DSH 有 `sandbox-windows-acl`，本轮评估后未引入，见 `docs/architecture/adr/0001`）。
