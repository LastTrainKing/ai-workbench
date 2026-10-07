# PROGRESS — 断点

工作区：`C:\Users\user\Desktop\ai-workbench`
交付物对应 commit：安装包 `ec77e0c` · 源码包 `37360ac`
（本文件在 `37360ac` 之后单独提交，那次提交只改这一份文件）

> **读完这一页就能开工。** 已完成的阶段不再写"下一步"——下面只有当前真实状态。

---

## 一、这是什么

**一个私人 agent。** 开局它先认识你，然后主动去公开渠道把该有的资料和做法找回来、
验证过、装好。之后你交给它活，它带着「你的记忆 + 你的资料 + 你的做法」去干，
干完给你验收，**你退回一次它下次就改对**。

它是 agent，不是 AI。用词是「执行 / 交付 / 验收」。

**三层资产：**

```
① 记忆    backend/app/ai/memory/       关于你的事实   → MEMORY.md
② 知识库  backend/app/services/        你的资料       → RAG.md
③ Skill   backend/app/ai/skills/       你的做法       → SKILL.md
              ↓ 三者结合
   按你的方式完成你的事 —— 检索阶梯见 RETRIEVAL.md
```

---

## 二、状态：第 1–8 步全部完成，交付物已按当前 commit 重建

| 步骤 | 状态 | 证据 |
|---|---|---|
| 第 1 步 安装程序 | **完成** | `verify_installer.py --apply` 33/33 · 静态 41/41（**均为 ec77e0c 的新安装包**） |
| 第 2 步 开局对话 + 空账号隔离 | **完成** | `verify-onboarding.mjs` 25/25 |
| 第 3 步 分级检索 | **完成** | 见下「真实数字」 |
| 第 4 步 Skill 层 | **完成** | 后端 + API + Skill 页 + 沙箱；`verify-skill-ui.mjs` 14/14 |
| 第 5 步 知识库自动补充 | **完成** | 11 测试，真实文件夹 |
| 第 6 步 记忆生命周期 | **完成** | 20 测试 |
| 第 7 步 界面 | **完成** | 首页四区 · 工作台 tier 行 · 我的（含 Skill 页） |
| 第 8 步 自检 / 交付 / 文档 | **完成** | 见下 |

---

## 三、真实数字

### 分级检索（各 5 次取中位）

```
记忆命中     median  1.6 ms   → 由「memory」回答
知识库命中    median 35.8 ms   → 由「knowledge」回答
都不命中     median 25.1 ms   → 由「general」回答
记忆比通用路径快 15.5×
```

判定方式：`RELEVANCE_FLOOR = 1.693`（两组分布间隙 0.14、样本 14 个）。
**余量不宽 —— 语料或嵌入一变就要重跑 `scripts/sample_relevance.py` 重测。**
skill 层（第 2 级）建起来之后**尚未单独计时**。

> 两次猜阈值的失败记录在 `RETRIEVAL.md`。规则：**先测分布，再选阈值。**

### 权限分级（本次实测，`AgentPolicy(mode=SUPERVISED, level=N)`）

```
level        0    1    2    3
supervised  16   34   44   44
assist      15   15   15   15   ← 全部非改动类
autonomous  32   36   44   44
```

旧的 15/32/42/42 是 `document_read` + `document_write` 加入之前的数字。

### Skill 发现（真实调 api.github.com）

```
3 个搜索词 → 7 个候选 → 保留 5 个
筛掉 anthropics/skills → 没有声明 license
```

`fetch_skill_file` 实测：`addyosmani/agent-skills/README.md` → HTTP 200、
blob sha `c6039f4d…`、**24,729 字符**、`source_commit` = 40 位 SHA。

> **注意**：`raw.githubusercontent.com` 在本环境**不可达**（HTTP 000），
> `api.github.com` 可达（HTTP 200）。抓取走 Contents API。

### 自检（以下为历史实测，**本轮未重跑**）

> 本轮只改了注释与文案，未改任何行为 —— 但这句话是**推断**，不是验证。
> 只有下面标了 ← 的两项是本次在 `ec77e0c` 上重跑的。

```
verify_installer_static.py        41/41   ← 本次在新安装包上重跑
verify_installer.py --apply       33/33   ← 本次在新安装包上重跑
verify_pgsql_trim.py              13/13
verify_deepseek.py                62/62
verify-onboarding.mjs             25/25
verify-skill-ui.mjs               14/14
verify-context-ui.mjs             22/22
verify-delivery-ui.mjs            35/35
verify-knowledge-and-memory-ui    13 通过 / 0 失败
verify-agent-ui.mjs               26/26
verify-shell-behaviour.mjs        21/21
后端测试                          544 收集，544 通过，exit 0   ← 本次重跑
旧路由                            18/18
冷启动 + 二次启动                  36/36
```

---

## 四、交付物（已重建，**不再是过期的**）

| | 路径 | 体积 | 条目数 | SHA256 |
|---|---|---|---|---|
| 安装包 | `build/installer/AI-Workbench-Setup.exe` | 97,128,573 B | payload 8,689 文件 / 443.3 MB | `e0c0222229a7bfc635ceaab47ff3586daf3af5769ada50138e78fc12c18992d2` |
| 源码包 | `archive/releases/AI-Workbench-37360ac-final.zip` | 8,457,736 B | 496 条目 | `16b81e2cdf72ddde109baee4ed8ff0760a67cf62ae391629a971b9ccdbfa2b8d` |

安装包对应 `ec77e0c`（其后再没有改动过会进 payload 的源码）；源码包对应 `37360ac`，
由 `git ls-files` 生成 —— 496 个跟踪文件全部纳入、0 个被策略排除，自带的凭据扫描 PASS。

**关于这一格 commit 差**：源码包由 `git ls-files` 生成，所以它**装的是构建它那个
commit 的 PROGRESS.md**。`37360ac` 里的 PROGRESS.md 已经是本文件的当前版本
（已实测：包内 `PROGRESS.md` 与工作区逐字节相同），只有「源码包自身的 SHA256」
这一格是上一次构建留下的数字 —— 文档要写下自己所在归档的哈希，这个错位无法消除，
所以在这里写明而不是假装没有。判断包新不新，看 `entries`/`tracked files` 与
`git ls-files | wc -l` 是否一致（现在都是 496）。

**重建链**（顺序不能变，第 3 步必须在第 2 步之后）：

```bash
cd frontend && npm run build
cd ../desktop && npm run build
cd .. && .venv/Scripts/python.exe scripts/build_installer.py --build
.venv/Scripts/python.exe scripts/build_delivery_zip.py --output archive/releases/AI-Workbench-<hash>-final.zip
```

第 3 步会把 `desktop/src-tauri/target/release/ai-workbench-desktop.exe` 复制成
payload 根目录的 `AI Workbench.exe`；少了它，装出来的东西装得上、起不来。

重建后必须重跑：

```bash
.venv/Scripts/python.exe scripts/verify_installer_static.py       # 41/41
.venv/Scripts/python.exe scripts/verify_installer.py --apply      # 33/33
```

> 验收会真的装一遍、启动、注册账号、强杀、卸载。**跑之前必须先把开发环境的
> 5432/6379/6333/8000 停掉**（`scripts\stop.bat`）——否则它测的是开发环境的服务，
> 第 7 步的「端口释放」会假失败。

---

## 五、不可谈判的设计（改了就是产品坏了）

1. **三道门都在 `adopt()` 里，故意不拆**：用户确认 + 安全扫描 + 脚本沙箱。
   **确认了也不够** —— 恶意 skill 即使用户说"是"也会被拒；
   **脚本型 skill 没跑过沙箱不允许入库**。
2. **个性化槽位空着保留 `{{param}}` 占位符，不留空。**
   空槽记进 `verification.unfilled_params`，**用来问，不是用来猜**。
3. **搜索本身不导入任何东西**（测试数过 API 调用：adopt 0 → 0）。
4. **矛盾记忆永远不被合并** —— 它们相似度高达 0.75，会被合并步骤先吃掉，
   那是系统在悄悄删掉用户的一个信念。
5. **失败可见**：`None` / 空列表 / 空串不许同时表示"没有"和"出错了"。
   `SKILL_LAYER_BUILT` 就是为此存在。
6. **一个决定只能有一个实现者** —— `autonomous_service.py` 曾用权限等级
   `autonomous` 冒充 provider，那是同一个决定的第二个实现者。

---

## 六、编码规则（已踩过多次）

| 文件 | 规则 |
|---|---|
| `.bat` | 必须**纯 ASCII**（cmd.exe 按 ANSI 读） |
| `.ps1` | 含非 ASCII 必须带 **UTF-8 BOM** |
| `.nsi` | 含中文必须带 **UTF-8 BOM** |

- 提交信息用 **`git commit -F <文件>`**（PowerShell 会吃掉反引号）
- 长文本、含反引号的内容用 **Write 工具写文件**，不要走 bash heredoc
- **模型字段靠猜必错** —— 写之前先 grep 一遍（`Mapped[Any]` 不可解析、
  `MemoryCandidate` 用 `proposed_kind`、`DocumentChunk` 没有 `owner_id`）
- 浏览器测试的 `:runId` 是 **BuildRun 的 id**，不是 AI 运行 id
- **Edit 工具会把整份文件的 CRLF 归一成 LF。** `installer/ai-workbench.nsi` 里
  有一段本来是 CRLF，被归一后 git 里多出 5 行"内容相同"的改动。NSIS 注释不进
  编译产物（实测：带注释与不带注释的两个最小脚本，MD5 都是
  `c22a22814f99a12361cf4abb139b4878`），所以无影响 —— 但下次改这个文件要知道。

---

## 七、环境备忘

- **不要打开 Smart App Control** —— 从「评估中」变成「正式开启」就锁死，只能重装系统
- 启动整套：`scripts\start.bat --hidden`（PG 5432 / Redis 6379 / Qdrant 6333 / API 8000）
- 停止整套：`scripts\stop.bat`
- 后端测试需要 PG 在跑；不需要 DB 的测试放 `backend/tests/unit/`
  （`integration/` 会拉起数据库夹具）
- git 不在 PATH，PortableGit 在
  `C:\Users\user\.workbuddy-ai\binaries\PortableGit\versions\1.2.0\cmd`
- `pytest` 的 `addopts` 已经带了 `-q`，所以 `pytest -q` 是**双 -q**，
  **不会打印 "N passed" 汇总行**。看 exit code，或数进度行的点。

---

## 八、本次接手做了什么

### 5.1 重建交付物

在 `ec77e0c` 上跑完整条链（frontend → desktop → installer → delivery zip），
体积/条目数/SHA256 见第四节，然后**在新安装包上重跑**安装验收：33/33 与 41/41。

### 5.2 `docs/` 下五份小写文档逐份核对

**五份全部有偏差，全部已改。** 每份文件末尾都追加了一句「核对到哪个 commit、
比对了什么、改了什么」。

| 文档 | 改了什么 |
|---|---|
| `permissions.md` | 工具数 15/32/42/42 → 实测 16/34/44/44；Assist 14 → 15；**autonomous 段说反了**（registry 是 high，autonomous 会直接放行，只有 critical 才拦）；Level 0 的"read the web"不成立（网络工具要 System）；风险表补齐到代码里的全部模式 |
| `security.md` | §2.6「agent 不能执行任意代码」**是假的**（System 级有 5 个 shell/解释器工具），改成如实描述；`_redact` 在 `database.py` 不在 `logging.py`；"No audit log"忽略了已存在的 `tool_audit_log`；"每个异常都记录或重抛"夸大了三处真实回退 |
| `deployment.md` | §1 说没有 `docker-compose.yml` 和 `start.bat`/`start.sh` —— 三个都在；模板 9 → 10；**四个 AI 默认值全错**；§9 引用的端口冲突提示在任何脚本里都不存在 |
| `testing.md` | `alembic/versions/` 不再是空的（5 个迁移）；验证脚本 11 → 25（补上另外 14 个的说明） |
| `rag.md` | `AI_MAX_TOKENS` 2048 → 8192；`reindex --drop` **这个开关不存在**；补上完全没写的四级检索阶梯 |

顺带修掉三处**代码里已经变成假话的文本**（都不改行为）：

- `vector_store.py` 的维度不符报错，叫人去跑一个不存在的 `--drop` 开关
- `tiers.py` 的模块说明还写着"skill 层尚未建立"（它已经建起来了）
- `permissions.py` 里 `OBSERVE` 的注释还在说 "read the web"
- `docker-compose.yml` 指向 `FINAL_CHECKLIST.md`，实际在 `docs/archive/`
- `installer/ai-workbench.nsi` 头部写死了 payload 文件数（每次构建都会变）

---

## 九、仍然 NOT VERIFIED

- **skill 层（第 2 级）的毫秒数未测** —— 它建起来了，但还没有用同一套方法证明它比
  通用路径快。`scripts/measure_tiers.py` 可以测，需要 PG 在跑。
- `docs/` 之外还有别的文档（`docs/thesis/` 中文各章、`docs/archive/` 历史文件）
  **未逐份核对**。本轮只覆盖了 `permissions/testing/security/deployment/rag` 五份。
- **`start.sh` / `stop.sh` 未验证** —— POSIX 路径从未在本机跑过。
- 沙箱是**进程级隔离 + 检测 + 回滚**，不是内核沙箱 ——
  它能发现并撤销越界写入，**不能阻止它发生**
- 未配置 `GITHUB_TOKEN` 时用仓库搜索（开放），候选池更小；代码搜索需要 token
- Embedding 仍是离线哈希 n-gram（DeepSeek 无 embedding 接口）
- Ollama / OpenAI 端点未实测 · Docker 路径未验证
- **安装包版本 2.1.0 与壳版本 2.0.0 不一致** —— 这是有意的（两者版本不同的东西），
  已在 `docs/deployment.md` 写明；但**没有做版本号的统一策略**。
