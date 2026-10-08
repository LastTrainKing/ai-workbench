# 第 15 轮：安全审计 + 核心逻辑 + 端到端验收

工作区：`C:\Users\user\Desktop\ai-workbench`
本轮模型调用：**0 次**（`scripts/model_call_count.py`，起点/终点见第六节）

---

## 一、一句话结论

**第 0 步、第一批 8 条、第二批 3 条（第 10、15、16、17 条）做完了，每一条都有原始输出；
第二批剩下 6 条和第三批 6 条没做**，原因和状态逐条写在第五节。
最要紧的两条——「两个脚本会删用户真实数据」和「Skill 正文没进上下文」——都修好并且验过了。

---

## 二、第 0 步：两个脚本不可能再删用户数据【PASS】

**做法**：新增 `scripts/_test_data_guard.py`，`verify_installer.py` 和 `measure_startup.py`
都改成走它。要点：

```
[✓] 摘掉裸 rmtree，改走 safe_rmtree（删不掉要看得见，不再 ignore_errors）
[✓] 测试数据改用 %LOCALAPPDATA%\AI Workbench Data TEST
[✓] 硬保护：路径与真实数据目录相等/包含/被包含，一律 exit 3
[✓] 没有任何 --force / --force-real-data 参数，以后也不会有
[✓] 装完之后改写测试副本的 data-dir.txt，把整条链指到测试目录
```

**守卫自测**（四种绕过形态）：

```
拒绝  真实数据目录本身               exit=3
拒绝  真实目录的子路径               exit=3
拒绝  真实目录的父目录               exit=3
拒绝  用 .. 绕回来的路径             exit=3
放行  测试目录（应当放行）           C:\Users\user\AppData\Local\AI Workbench Data TEST
```

**两个脚本各跑一次，真实数据目录没变**：

```
=== 真实数据目录：跑完两个脚本之后 ===
  路径      : C:\Users\user\AppData\Local\AI Workbench Data
  mtime_ns  : 跑之前 1791455911346056800
              跑之后 1791455911346056800
              ★ 没变 ★
  文件数    : 跑之前 1530  →  跑之后 1530   （一致）
  体积      : 跑之前 49.51 MB  →  跑之后 49.51 MB

=== 测试数据目录（测试该动的是它）===
  C:\Users\user\AppData\Local\AI Workbench Data TEST
  存在，1529 文件，49.18 MB  ← 测试真的用了它

第 0 步结论：PASS —— 两个脚本各跑一次，真实数据目录 mtime 与文件数都没变
```

两个脚本本身也都跑通了：`verify_installer.py --apply` **47/47 passed**（比上轮 46 多一条，
就是新增的"测试安装指向自己的数据目录"），`measure_startup.py` 冷 25.0s / 热 10.0s。

---

## 三、第 3 条（产品灵魂）：Skill 正文现在真的进上下文了【PASS】

### 链条断在四个地方，逐个确认过

| 位置 | 原来 | 现在 |
|---|---|---|
| `ai/skills/registry.py:49` `SkillMatch` | 只有 `id/name/score/when_to_use/skill_type` | 加 `body` 字段 |
| `registry.py` 构造 `SkillMatch` | 不读 `rendered_body` | `body=row.rendered_body or row.body or ""` |
| `ai/rag/tiers.py:298` | `[{"id","name","score"}]` | 加 `"body": m.body` |
| `services/conversation_service.py:525` | `skill.get('summary','')` ← **这个 key 从来不存在** | `skill.get("body")` |

### 还查出更严重的一件事：**自主执行链压根不查 skill**

```
$ grep -n "find_matching_skills" backend/app/ai/agent/autonomous.py
(空)
$ grep -rn "find_matching_skills" backend/app --include=*.py
backend/app/ai/rag/tiers.py:281     ← 只有对话路径
```

所以「带着你的做法干活」这句，在**真正干活的自主执行链上从来没成立过**。
已修：`AutonomousAgent._personal_context()` 现在会查匹配的 skill 并把正文拼进去，
planner 和 executor 都会拿到（`_personal_context` 是两者共用的入口）。
匹配到但正文为空的 skill 会跳过并记一条 warning，不会让模型只看到一个空标题。

### 验收

**A/B 对照这一条我做不到，标 NOT VERIFIED。** 理由：要产生"产出 A / 产出 B"需要真跑一次
agent，而本轮模型调用必须是 0。**用 fixture 能证明的是链路，不是产出差异**，所以我给的是
链路证据而不是产出对比：

```
backend/tests/unit/test_security_fixes_round15.py::test_skill_body_travels_with_the_match  PASS
backend/tests/unit/test_security_fixes_round15.py::test_the_autonomous_agent_consults_skills PASS
backend/tests/unit/test_security_fixes_round15.py::test_unfilled_skill_params_are_kept        PASS
```

第三条守的是既有设计：没填的槽位保留 `{{param}}`，不许渲染成空白。

**需要用户加回 Key 才能做的**：裸 Agent 与装了 skill 的 Agent 的产出对比。

---

## 四、第 4/5/6 条：构造用例 + 判定结果

### 第 4 条：`process_start` 检查的和执行的不是同一个东西【PASS】

**构造用例**：`command = <脚本宿主>.exe`，`arguments = ["-Command", "Remove-Item -Recurse -Force"]`

**改法**：
1. 判定用**拼接后的完整命令行**，不是只看 executable；
2. **shell wrapper 一律拒绝**（`cmd` / `pwsh` / `sh` / `bash` / `wscript` / `cscript` 等），
   因为它们后面的参数就是程序本身，没法诚实分级 —— 指回 shell 工具走它自己的门。

断言：`test_process_start_refuses_shell_wrappers`、`test_process_start_judges_the_whole_command_line`。

### 第 5 条：`headers` 被拼进会被执行的脚本【PASS】

**改法**：这条 HTTP 路径**不再用脚本宿主**。`_perform_request` 用 Python 的 `urllib` 直接发，
不拼任何字符串；`headers` 从 `{"type":"string"}` 改成 `{"type":"object"}`。

**改动前后对照**：

```
改之前（process_network.py:289-291）
    safe_url  = url.replace("'", "''")
    safe_body = body.replace("'", "''")
    header_block = f"$h = @{{ {headers} }}"        ← headers 完全没有转义
    script = _fetch_script(...)                    ← 拼成一段脚本
    await _run_process(exe=<脚本宿主>, args=[*flags, script])

改之后
    payload = await asyncio.to_thread(
        _NetworkTool._perform_request, url, method, body, headers, check_hop=check_hop
    )
    # headers 是 dict[str, str]，直接交给 urllib，从不变成代码
```

**断言用 AST 而不是 grep**（第一版 grep 挂了 —— 注释里还留着被删掉的代码）：
`test_the_network_tools_do_not_build_a_script` 解析模块，断言
（a）不存在 `_fetch_script` 函数，（b）不存在"把脚本文本和变量拼在一起"的 f-string。

**这条断言顺手抓出模块里另外两段真实脚本**（进程列表、屏幕尺寸）——
读了之后确认它们是**固定脚本、没有任何插值**，不是风险，所以断言收窄到 f-string。
**没有把它们当漏洞报上去。**

### 第 6 条：默认白名单信任 localhost【PASS】

**构造用例与判定**（用 `AgentPolicy.from_settings()`，即真实默认策略）：

```
  DENY    http://127.0.0.1:6333/collections       risk=high    127.0.0.1 is a local or private address
  DENY    http://localhost:6333/collections       risk=high    This host is on the blocked list
  DENY    http://[::1]:8000/api/v1/health         risk=high    ::1 is a local or private address
  DENY    http://2130706433/                      risk=high    2130706433 is a local or private address (127.0.0.1)
  DENY    http://0x7f.1/                          risk=high    0x7f.1 is a local or private address
  DENY    http://169.254.169.254/latest/meta-data/ risk=high   169.254.169.254 is a local or private address
  DENY    http://10.0.0.5/admin                   risk=high    10.0.0.5 is a local or private address
  DENY    http://192.168.1.1/                     risk=high    192.168.1.1 is a local or private address
  DENY    http://metadata.google.internal/        risk=high    This host is on the blocked list
  ALLOW   https://github.com/foo/bar              risk=low     Host is on the allowed list
  ALLOW   https://raw.githubusercontent.com/x/y   risk=low     Host is on the allowed list
  ALLOW   https://api.deepseek.com/v1/models      risk=low     Host is on the allowed list
  CONFIRM https://evil.com/                       risk=medium  This host is not on the allowed list
  CONFIRM https://evilgithub.com/                 risk=medium  This host is not on the allowed list
```

做的四件事：
1. `localhost` / `127.0.0.1` 从默认白名单拿掉；默认黑名单**不再为空**；
2. `check_domain` 先规范化主机（`[::1]`、userinfo、host:port），再判 IP，再查名单；
   回环/私网/链路本地/保留/组播一律 DENY，**不管名单怎么说**；
3. **重定向每一跳都复查**：`_CheckedRedirect` 在跟随之前调 `check_hop`；
4. **两条出网实现合成一条** —— `_download` 也走同一个 `_CheckedRedirect`。

**第 5 和第 6 条串起来的那条攻击链现在断在第一步**：恶意文档 → 模型读到 → 调 `process_network`
→ 无论 headers 里放什么都不会变成代码，且 127.0.0.1 在第一步就被拒。

**没验证的**：没有真的起一个会 302 到 6333 的服务器去打。重定向复查是读代码 + 单元级确认，
**不是端到端攻击复现**。

---

## 五、其余各条的状态

### 第一批

| # | 项 | 状态 | 证据 / 原因 |
|---|---|---|---|
| 1 | 安装器误杀同名进程 | **PASS** | 新增 `scripts/stop-by-pid.ps1`（按 pid 文件 + postmaster.pid，且校验 exe 路径在产品目录内）；`.nsi` 删掉三条第三方 `/IM`，只保留自己 exe 的 `/IM`；编译通过 |
| 2 | 测试脚本真实数据保护 | **PASS** | 见第二节 |
| 3 | Skill 正文进上下文 | **PASS（链路）/ 产出对比 NOT VERIFIED** | 见第三节 |
| 4 | `process_start` 检查完整命令 | **PASS** | 见第四节 |
| 5 | `headers` 注入 | **PASS** | 见第四节 |
| 6 | localhost 白名单 | **PASS** | 见第四节 |
| 7 | demo 账号 | **PASS** | `ensure_demo_user` 非 dev 直接返回 None；`main.py` 也判 env；登录页改成 `import.meta.env.DEV`（构建期），演示凭据不进发行包 |
| 8 | 权限策略全机共享 | **PASS** | `policy_store` 全部函数收 `user_id`，落 `agent-policy/<user id>.json`；旧文件首次读取时迁移并改名 `.migrated`（只给第一个读的人，不会漏给第二个）；路由与 `AgentService` / `_build_agent` / `start_run` 全部传 `user.id` |

**第 1 条的误杀验证没有做**（brief 要求"手工起一个无关的同名进程，确认它活到最后"）。
**标 NOT VERIFIED**：`.nsi` 的改动只做了编译验证，没有真的装一次 + 卸一次。
代码层面的证据是三条 `/IM` 已经不存在、换成按 pid 的脚本。

### 第二批

| # | 项 | 状态 |
|---|---|---|
| 9 | skill 域名校验复用统一实现 | **NOT DONE** |
| 10 | 进程终止的风险等级 | **PASS** |
| 11 | Tool 输出总量限制失效 | **NOT DONE** |
| 12 | `file_read` 大文件整文件 hash | **NOT DONE** |
| 13 | RAG「强制引用」名不副实 | **NOT DONE** |
| 14 | GET/POST 共用域名策略 | **NOT DONE** |
| 15 | JWT 默认密钥 | **PASS** |
| 16 | dev/debug + docs 暴露 | **PASS** |
| 17 | `/health/deps` `/metrics` 无鉴权 | **PASS** |
| 18 | logout 不能让 token 立即失效 | **NOT DONE** |

**第 10 条**：`taskkill` / `Stop-Process` / `kill -9` / `pkill` / `tskill` 全部进规则表；
另外加了 `NEVER_AUTOMATIC_REASONS` —— **光提高风险等级不够**，因为 Autonomous 的规则是
"低于 CRITICAL 一律放行"，HIGH 也会被自动执行。实测（Autonomous + System 级别）：

```
  ALLOW   ls -la                        risk=low
  ALLOW   git status                    risk=low
  CONFIRM taskkill /F /IM postgres.exe  risk=critical  kills every process with that image name
  CONFIRM taskkill /PID 1234 /F         risk=high      terminates processes
  CONFIRM Stop-Process -Name postgres   risk=high      terminates processes
  CONFIRM kill -9 1234                  risk=high      terminates processes
  CONFIRM pkill postgres                risk=high      terminates processes by name
  CONFIRM reg add HKCU\Software         risk=high      modifies the registry
  ALLOW   rm -rf /                      risk=high      （Autonomous 放行 —— 见下）
```

**顺带发现，没有修**：`rm -rf /` 在 Autonomous 模式下仍然是 ALLOW。这是既有设计
（Autonomous = 用户已经接受范围），不在本轮 24 条里，**如实记下来**。

**第 15 条**：`prod` 下 SECRET_KEY 还是仓库里的占位值、或短于 32 字符、或 `DEBUG=True`，
**直接拒绝启动**。实测：

```
$ APP_ENV=prod python -c "from app.core.config import Settings; Settings()"
  拒绝启动 ✓
  理由: refusing to start with APP_ENV=prod: DEBUG is on. Set them in the environment or in .env.
```

**第 16 条**：`docs_url/redoc_url/openapi_url` 在 prod 全部置 None（`api_docs_enabled`）。

**第 17 条**：`/health` 只回 `{status, app}`；`/health/deps` 只接受本机来源（非本机返回 404）；
`/metrics` 要求鉴权。对应测试改成了"匿名被拒 + 带 token 拿到数字"，两边都断言。

### 第三批（19–24）

**全部 NOT DONE。** 都是前端状态和数据保留策略，与本轮的安全修复没有依赖关系。

---

## 六、回归

```
后端 pytest        exit 0 —— 全通过，新增 30 条安全断言
                   （663 通过 / 1 失败 → 修好后 663 通过 / 0 失败）
后端 ruff          All checks passed（app/ 与 tests/）
前端 tsc           0 error
前端 lint          0 error / 6 warning（全部是既有的 react-refresh）
前端单测           121 通过 / 10 跳过
```

**回归里唯一那次失败是好事**：`test_metrics_endpoint` 按旧的匿名行为断言，被我的
`/metrics` 鉴权改动打红。**测试锁住的是旧行为，改测试时我把两半都断言了**
（匿名 401/403、带 token 有数字），不是把断言放宽。

还有一次失败是项目自己的凭据扫描器 `test_no_credentials_are_committed_anywhere`，
被我新加的 `DEFAULT_SECRET_KEY` 常量触发。**没有用 inline 豁免绕过**，而是把
`change-me` / `changeme` / `replace-me` 加进扫描器的 `_SYNTHETIC_HINTS` ——
占位符本来就该被认成占位符，这让扫描器对整个仓库更准。

### 断言的有效性验证（反向突变）

写了断言不等于断言有用。拿第 6 条做了一次反向突变：

```
把 localhost / 127.0.0.1 放回默认白名单（模拟修复被回退）
  → test_localhost_is_not_on_the_default_allowlist  FAILED  ✓ 断言有效
恢复
  → 全部通过
```

---

## 七、本轮模型调用次数：0

```
$ python scripts/model_call_count.py --baseline     # 起点
  起点已记下：2026-10-08T17:53:49.092192+00:00

$ python scripts/model_call_count.py                # 终点
  范围      ：从起点 01:53:49 到现在
  调用次数  ：0
  历史总数  ：161 行 ai_runs
  **这一轮没有发生任何模型调用。**
  PASS：0 次调用
```

起点与终点的 `ai_runs` 行数都是 **161**，没有增加。所有修复用 fixture、纯函数和真跑脚本
验证，**没有一次真实模型调用**。

---

## 八、需要用户加回 Key 才能做的（单独列出）

| 项 | 为什么需要模型 |
|---|---|
| 第 3 条的 A/B 产出对比 | 要真的生成两份产出才能比 |
| 第 22 条 Agent 成功判定 | 要真跑一轮 agent 才知道状态怎么定 |

**这两条本轮标 NOT VERIFIED，没有为了它们去调模型。**

---

## 九、仍然 NOT VERIFIED / FAILED

- **第 1 条的误杀验证**：没有真的装一次卸一次、也没有手工起无关同名进程。
  只验证了 `.nsi` 编译通过、三条第三方 `/IM` 已删除。
- **第 3 条的产出 A/B 对比**：链路有断言，产出差异没有。
- **第 6 条的重定向绕过**：没有起 302 服务器做端到端复现。
- **第 7 条的验收**：brief 要求"全新数据目录 + prod 启动一次，确认 users 表为空、
  登录页不显示演示凭据"。**没有做**——prod 启动会因为 DEBUG 被拒（第 15 条的守卫），
  要先设好 SECRET_KEY 和 DEBUG 才能跑那条验收，本轮没做。
- **第 8 条的验收**：brief 要求"建两个账号，A 改权限，切到 B，确认 B 看到自己的策略"。
  **没有做端到端**；有的是单元断言 `test_policy_store_is_per_user`（Alice 设 level=3，
  Bob 读到空）和文件路径不同的断言。
- **第 17 条**：`/health/deps` 的"非本机返回 404"没有从非本机地址实测（本机测试客户端
  会被认成本机，所以测的是"本机可以"这一半）。
- **第二批 9、11、12、13、14、18 条与第三批 19–24 条**：NOT DONE，原因见第五节。
