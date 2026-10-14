# 封版问题总表 — AI Workbench

> 来源：`AI-Workbench-全量修复执行令.md`（23 节）＋ `FINAL-ACCEPTANCE-REPORT.md` ＋ `CODE-REVIEW.md`
> ＋ `PROGRESS.md` ＋ `FIX-ROUND-17/18.md` ＋ 本轮对真实源码的逐条复核。
> 合并重复项；每条给出严重度、根因、涉及文件、判定方法、当前状态。
> 分类：**缺陷**（代码错）／**测试缺口**（代码可能对但没证据）／**权衡**（有意的取舍）／**改进**（非阻塞）。
> 复核基线：分支 `fix/release-freeze-20261009`，起点 commit `682ed8d`（代码构建提交 `de4dae1`）。
> 复核方式：读真实源码 + 已有测试，不以旧报告结论为准。

## 严重度定义

| 级别 | 含义 |
|---|---|
| P0 | 阻断发布：安全/数据/账号隔离/产品形态硬要求 |
| P1 | 高：功能正确性、可恢复性、跨用户影响 |
| P2 | 中：安装/配置/文档一致性 |
| P3 | 低：可读性、非阻塞改进 |

---

## 一、封版阻断项（P0）

| ID | 问题 | 根因 | 涉及文件 | 判定方法 | 状态 |
|---|---|---|---|---|---|
| **RF-01** | 桌面端仍是 Tauri + WebView2 + React，不是"不使用内嵌网页的原生 UI" | 架构从未迁移；启动 Python 后由 WebView2 显示页面 | `desktop/src-tauri/**`、`frontend/**` | 运行 EXE，查进程/依赖中是否有 WebView2 承载 UI | **OPEN — 封版阻断项**（见 `KNOWN_ISSUES.md` KI-01；估算 9–15 周） |
| **RF-02** | 首次安装把 `.env.example` 原样复制成 `.env`：`APP_ENV=dev`、`DEBUG=true`、占位 `SECRET_KEY`；无每实例随机密钥 | 安装器只有 `CopyFiles`，无生成逻辑 | `installer/ai-workbench.nsi:320-324`、`scripts/build_installer_payload.py:48`、`.env.example:11` | 全新安装后读 `.env` | **已修** |
| **RF-03** | 生产密钥校验只比对唯一精确值，`change-me/example/replace-me/placeholder` 等占位符可通过 | 校验只 `== DEFAULT_SECRET_KEY` + 长度 | `backend/app/core/config.py:301-317` | 逐个占位值起 `APP_ENV=prod` | **已修** |
| **RF-04** | 密码先 UTF-8 编码再截断 72 字节 → 多字节密码静默截断，可落到字符中间 | bcrypt 72 字节硬限制被直接套用 | `backend/app/core/security.py:21-34` | 两个仅第 72 字节后不同的长密码能否互相认证 | **已修** |
| **RF-15** | 开发 `.env` 第 48 行注释里带一个真实 DeepSeek Key（`sk-` 前缀） | 脱敏规则漏掉注释行 | `.env:48`（git-ignored，未入库） | 扫描 `#*API_KEY=value` | **已处置（见 §备注）** |

## 二、高（P1）

| ID | 问题 | 根因 | 涉及文件 | 判定方法 | 状态 |
|---|---|---|---|---|---|
| **RF-05** | 登出撤销在 Redis 不可用时得到 `None` 并解释为"未撤销" → 已登出 Token 重新有效 | 二态检查 + fail-open | `backend/app/core/security.py:104-118`、`core/cache.py` | 停 Redis 后用已登出 Token 请求 | **已修** |
| **RF-06** | `max_steps`/`loop_detected`/`empty_response` 只要生成了说明文字就把运行标成 `SUCCEEDED` | `bool(final_answer)` 当成功条件；`recorder.finish` 无条件 `SUCCEEDED` | `backend/app/ai/agent/executor.py:391-430` | 触发各停止原因，查 `ai_runs`/`agent_runs`/API/UI | **已修** |
| **RF-07** | `requeue_stale` 对过期 `RUNNING` 无条件改回 `QUEUED`，不查 `attempts >= max_attempts` → 无限重试 | 恢复路径漏掉次数上限 | `backend/app/repositories/ai_repo.py:268-280` | 令 job 达到 max_attempts 后再重启 | **已修** |
| **RF-08** | `skills/discovery._authorise` 在 `permissions is None` 时直接放行；`POST /skills/discover` 调用链不传权限 → 该路径域名闸为空 | 缺省放行 + 路由未注入策略 | `backend/app/ai/skills/discovery.py:137-146`、`api/v1/skills.py` | 断点/单测：`permissions=None` 时是否发起请求 | **已修** |
| **RF-09** | AI Provider 配置写到共享 `.runtime/ai-config.json`，任一账号可改所有账号的下一次调用 | 无 `owner_id` 维度 | `backend/app/ai/runtime_config.py:38,96-134`、`api/v1/ai_config.py` | 账号 A 改配置后账号 B 的行为/Key 状态 | **已修** |
| **RF-10** | Qdrant 生成配置含 `enable_cors: true`；compose 路径不注入 `QDRANT_API_KEY` | 默认开 CORS、空认证 | `scripts/infra-start.ps1:451`、`docker-compose.yml:60-70` | 读生成的 `config.yaml`；无 key 请求集合 | **已修** |
| **RF-11** | RAG 只要正文出现一个合法编号就把整段回答 `grounded=True`，未证明逐条事实有据 | `grounded = bool(used_ids)` | `backend/app/ai/rag/pipeline.py:241-252` | 一个引用对应多条论断时是否仍判 grounded | **已修** |
| **RF-12** | 安装/冷启动验证脚本按映像名 `taskkill /IM "AI Workbench.exe"` 杀进程，可能结束用户正在用的实例 | 未记录/只记录部分 PID | `scripts/verify_installer*.py`、`verify_relocation.py`、`verify_cold_start_final.py`（共 10 处） | 静态审计 + 隔离环境运行 | **已修** |
| **RF-17** | 启动把全部 `RUNNING` 的 Agent Run 一律标 `FAILED`，无法区分"执行中死亡"与"产物已生成、状态未写" | 无 heartbeat/checkpoint | `backend/app/main.py:146-212` | 四种崩溃时间线 | **已修** |

## 三、中（P2）

| ID | 问题 | 涉及文件 | 状态 |
|---|---|---|---|
| **RF-13** | 版本号不一致：Tauri `2.0.0`／NSIS `2.1.0`／backend `1.0.0`／frontend `1.0.0`／CHANGELOG `1.1.0` | `tauri.conf.json`、`Cargo.toml`、`installer/*.nsi`、`config.py`、`CHANGELOG.md` | **已修** |
| **RF-14** | compose 把 PostgreSQL/Redis/Qdrant/API/前端以 `${PORT}:PORT` 发布，默认绑所有网卡；PG 默认口令 `aiworkbench`、Redis 无认证 | `docker-compose.yml` | **已修（静态）**；实跑见 KI-05 |
| **RF-16** | 文档夸大/过时：README `citation-enforced`；`docs/api.md:51` 称"首次启动播种 demo/demo1234"（实为仅 dev） | `README.md:167`、`docs/api.md:51`、`docs/deployment.md:152` | **已修** |
| **RF-18** | pytest 默认 `-q` 重复，汇总可读性差 | `backend/pyproject.toml` | **已修** |

## 四、测试缺口 / 权衡 / 外部阻断（不改代码，如实标注）

| ID | 事项 | 类型 | 说明 |
|---|---|---|---|
| **RF-19** | 多显示器 DPI（100/125/150/175%）、混合 DPI、热插拔 | **BLOCKED** | 需真实多屏/可改 DPI 的受控 VM |
| **RF-20** | Docker compose 实跑（启动/健康/登录/处理/停止） | **BLOCKED** | 本机无 Docker，registry 不可达；仅能静态修 |
| **RF-21** | 安装包 Authenticode 签名 | **BLOCKED** | 无受信任代码签名证书 |
| **RF-22** | 符号链接/junction 逃逸（真实 NTFS + 提权） | 测试缺口 | 已有 WORKSPACE 级验证；SYSTEM 级未验 |
| **RF-23** | Embedding 离线 hash n-gram | **权衡** | 无 Key 时的降级模式，须在 UI/文档标明，不得宣传为语义向量 |
| **RF-24** | Skill Scanner 为规则列表 | **权衡** | 非完备恶意代码分类器；不得宣称能识别所有恶意 Skill |
| **RF-25** | 真实模型调用 | **约束** | 本轮 0 次真实调用；集成测试需显式 opt-in |

---

## 五、执行顺序与完成时间

| 批次 | 内容 | 对应 ID |
|---|---|---|
| B1 身份与安全 | 密码哈希、令牌撤销、生产密钥校验、`.env` 泄漏处置 | RF-04/05/03/15 |
| B2 状态与队列 | Agent 终态语义、Job 重试上限、启动恢复语义 | RF-06/07/17 |
| B3 网络边界 | Skill 发现权限闸、Qdrant CORS/Key、compose loopback | RF-08/10/14 |
| B4 多租户与检索 | Provider 按 owner 隔离、RAG 引用、跨用户所有权测试 | RF-09/11/14(路由) |
| B5 安装与交付 | 安装器随机密钥、版本统一、验证脚本进程安全、文档订正 | RF-02/12/13/16/18 |
| B6 回归与报告 | 全量回归、隔离安装验收、报告与 SHA256 | 全部 |

---

## 六、本轮结果

| 结果 | 数量 | 说明 |
|---|---|---|
| 已修（含回归测试） | 15 | RF-02…RF-14、RF-16、RF-17、RF-18 |
| 已处置（非代码） | 1 | RF-15 `.env` 密钥移除；撤销状态未确认 |
| 封版阻断（未修） | 1 | RF-01 原生桌面 UI —— 架构级重写，独立工作项 |
| BLOCKED（外部条件） | 4 | RF-19 DPI、RF-20 Docker、RF-21 签名证书、RF-22 SYSTEM 级 symlink |

证据与命令见 `FULL_REMEDIATION_REPORT.md`；逐项判定见 `ACCEPTANCE_MATRIX.md`；
未修项与取舍见 `KNOWN_ISSUES.md`。

## 七、备注

- **RF-15**：`.env` 中该 Key 已被移除（值清空，注释保留）。**是否已撤销无法在本机确认**（本轮禁止任何真实模型调用）。结论记为"**已从工作副本移除，撤销状态未确认**"。该文件 git-ignored，未进入任何 Git 历史或交付包。
- **RF-01** 是唯一无法在本轮完成的项目：把 Tauri/React 桌面壳迁移为 Qt 6/QML 原生 UI 属架构级重写，不在"修复轮"范围内。按用户要求，把它作为**独立封版工作项**评估（见 `FULL_REMEDIATION_REPORT.md` 的迁移计划与工时），**不得**用 WebView2 页面美化替代。过渡期先做 §13 的 CSP 收紧。
- 本表随修复推进更新"状态"列；证据写入 `FULL_REMEDIATION_REPORT.md` 与 `ACCEPTANCE_MATRIX.md`。
