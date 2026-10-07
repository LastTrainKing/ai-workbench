# PROGRESS — 断点

工作区：`C:\Users\user\Desktop\ai-workbench`
交付物对应 commit：安装包 `ba60d5a` · 源码包 `0c9ed34`
（本文件在 `0c9ed34` 之后单独提交，那次提交只改这一份文件）

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
| 第 1 步 安装程序 | **完成** | `verify_installer.py --apply` 33/33 · 静态 41/41（**均为 ba60d5a 的新安装包**） |
| 第 2 步 开局对话 + 空账号隔离 | **完成** | `verify-onboarding.mjs` 25/25；隔离断言 7/7 |
| 第 3 步 分级检索 | **完成** | 见下「真实数字」 |
| 第 4 步 Skill 层 | **完成** | 后端 + API + Skill 页 + 沙箱；`verify-skill-ui.mjs` 14/14 |
| 第 5 步 知识库自动补充 | **完成** | 11 测试，真实文件夹 |
| 第 6 步 记忆生命周期 | **完成** | 20 测试 |
| 第 7 步 界面 | **完成** | 首页四区（+ 模型引导）· 工作台产出优先、详情折叠 · 我的（含 Skill 页） |
| 第 8 步 自检 / 交付 / 文档 | **完成** | 见下 |

**本轮（用户装上软件实际使用后）修了四个问题 + 一个顺带发现的隔离漏洞：**

| # | 问题 | 状态 |
|---|---|---|
| 1 | 提交任务后不开始（`create` 后从没调 `execute`，永远停在 DRAFT） | **完成** |
| 2 | 没配模型也能提交，做不了才知道 | **完成** |
| 3 | 顶栏标签是旧的（失效的 queryKey 写错，保存后从不重拉） | **完成** |
| 4 | 工作台三栏工程面板占首屏，产出被压在下面 | **完成** |
| 附 | `GET /agent/runs` 把进程里**所有人**的运行返回给任何登录用户 | **完成** |

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

### 自检（本轮在 `0c9ed34` 上全部重跑）

```
verify_installer_static.py        41/41
verify_installer.py --apply       33/33
verify_pgsql_trim.py              13/13
verify_deepseek.py                62/62   （历史值，本轮未重跑）
verify-onboarding.mjs             25/25   隔离断言 7/7
verify-skill-ui.mjs               14/14
verify-context-ui.mjs             22/22
verify-delivery-ui.mjs            40/40   （本轮加了 4 条断言）
verify-knowledge-and-memory-ui    13 通过 / 0 失败
verify-agent-ui.mjs               26/26
verify-shell-behaviour.mjs        21/21
verify-sidebar.mjs                10/10
verify-config-gate.mjs            12/12   ← 本轮新增
verify-workbench-layout.mjs       19/19   ← 本轮新增
后端测试                          548 收集，548 通过，exit 0
旧路由                            18/18（verify-delivery-ui 内）· 16/16（verify-sidebar 内）
冷启动 + 二次启动                  36/36   （历史值，本轮未重跑）
```

> **注意：`verify-config-gate.mjs` 会写一个占位 Key 到 `/ai-config`，也会自己清掉。**
> 它必须先让设置页"脏"才能按保存。第一版没清，占位 Key 成了活动 Key、
> 覆盖了 `.env` 的真 Key，之后每次 `verify-onboarding.mjs` 都以
> DeepSeek 的 422 `Your api key: ****0000 is invalid` 失败 —— 看起来像产品坏了，
> 其实是这个测试留下的垃圾。现在写入和 `DELETE /ai-config` 成对出现，
> 并有两条断言检查清理成功。

---

## 四、交付物（已重建，**不再是过期的**）

| | 路径 | 体积 | 条目数 | SHA256 |
|---|---|---|---|---|
| 安装包 | `build/installer/AI-Workbench-Setup.exe` | 97,144,496 B | payload 8,692 文件 / 443.3 MB | `cdc8dfdb244a59e3e3e009bd8e01c999cd5b12acfc1407d028b886411ff6804a` |
| 源码包 | `archive/releases/AI-Workbench-0c9ed34-final.zip` | 9,702,947 B | 508 条目 | `d4b8417f7dbc5fd5cb36cd6ce99cf13e364ab958cc89d19ebb412067aecf5469` |

安装包对应 `ba60d5a`，源码包对应 `0c9ed34`。两者之间的差别**只有 `screenshots/` 的图像内容**
（回归跑动时重新生成），已验证：`git diff --name-only ba60d5a HEAD` 去掉 `screenshots/` 后为 0 个文件，
所以安装包对发布而言是当前的。

`archive/releases/AI-Workbench-ba60d5a-final.zip` 已删除 —— 它和 `0c9ed34` 那份**字节完全相同**
（两个构建都读同一份工作区，PNG 在 `ba60d5a` 构建前就已经是新的），留着只会让人以为有两个版本。

源码包由 `git ls-files` 生成 —— 508 个跟踪文件全部纳入、0 个被策略排除，自带的凭据扫描 PASS。
判断包新不新，别读包内的哈希，读 `entries` 与 `git ls-files | wc -l` 是否一致（现在都是 508）。

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
7. **"创建"和"开始"必须是同一个动作**（本轮加的）。用户做的是"交给它一件事"，
   代码里拆成两个接口又只调其中一个，就是问题 1。组合动作
   （`executeBuildRun` 必须跟在 `createBuildRun` 后面）写进调用方，
   不留下"能创建但不会开始"的入口。
8. **`DRAFT` 必须读起来就是"还没开始"**，不能像"进行中"。
   三栏空面板 + 一句「还没有…」＝ 读起来像在准备中，这是问题 1 藏了一整轮的原因。
9. **归属必须显式**（本轮加的）。`RunController.owner_id` 不给默认值：
   进程级注册表 + 无 owner = 任何人看到所有人的东西。默认 `None` 会让它悄悄回来。

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
- **失效一个 key 前先 grep 它到底存不存在。** `invalidateQueries({queryKey:['health-deps']})`
  看起来完全正常，且永远不报错，只是什么都不做（问题 3 的根因）。
- **测试写进系统的状态必须自己清掉。** 占位 API Key 让三个套件红了，
  而报告的失败原因（DeepSeek 422）指向的是产品。（见第三节末尾的注意事项。）
- **`<details>` 折叠后内容仍在 DOM 里** —— 用 `innerText` 判断"用户看得到吗"必然错。
  用 Playwright 的 `isVisible()`。
- **浏览器测试要等事件，不要等时长。** `waitForTimeout(4000)` 在空闲机上够、
  在负载下不够，会让同一次回归时绿时红；改成 `waitForRequest(...)` 之后稳定。
- **改后端要重启进程才生效。** 这台机器上 Playwright 打的是 API 在 8000 端口
  托管的 `frontend/dist`，所以改完前端必须 `npm run build` 才看得到效果 ——
  第一次跑验证时 40 条里有 5 条失败，就是因为浏览器加载的还是旧 bundle。

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

## 八、本轮做了什么

用户装上软件实际使用，报了四个问题。逐条核实（看代码 + 实际运行），四条全部成立。

### 问题 1 —— 提交任务后它不开始（真 bug）

`deliveryApi` 里**根本没有** `executeBuildRun`。`HomePage` 只调 `createBuildRun`
然后跳转，`POST /build-runs/{id}/execute` 从前端任何地方都没被调用过。
`DeliveryService.create_build_run` 不设置 `started_at`，所以行永远是 `DRAFT`。
而 `delivery.py` 的创建接口返回 `message="已开始处理"` —— 假话。
三个面板写着「还没有生成计划」「还没有开始执行」「本次被告诉了它什么」，
读起来像在准备中，用户就等着一个永远不会来的结果。

改：`deliveryApi.executeBuildRun(id)`；`HomePage` 一次 mutation 里 create → execute → 跳转；
`StartFailedError` 把"创建失败"和"创建成功但没启动"分开（前者留在首页报错，后者带着原因
送到工作台，因为运行记录存在、可以从那儿点开始）；`delivery.py` 改
`message="已创建，尚未开始"`；工作台 `DRAFT`/`READY` 渲染一个显眼的「开始」面板。

**证据**（浏览器实测，`verify-delivery-ui.mjs`）：
```
POST /build-runs | POST /build-runs/{id}/execute | GET /build-runs
status=EXECUTING  started_at=2026-10-07T14:54:07.964095+00:00
```
旧断言只检查了 `POST /build-runs` —— 产品坏着它也是绿的，这正是它没被发现的原因。

### 问题 2 —— 没配模型也能提交

首页现在读 `/health/deps`，`llm.degraded` 为真时在**输入框之前**就把话说清楚：
一块带边框的引导（原因 + 「去配置」直达设置页），输入框/格式/「开始」全部禁用，
按钮文案变「先配置模型」。配好后什么都不出现。

**判断只看 `ai.llm.degraded`。** `ai.embedding.degraded` 在本机是 `true` ——
DeepSeek 没有 embedding 接口，走离线哈希 n-gram 是**故意的**，
拿它来拦人会把能正常干活的配置判成不可用。

**证据**（`verify-config-gate.mjs`）：`llm.degraded=false embedding.degraded=true`
→ 不出现引导块、输入框可用、「开始」可用。

### 问题 3 —— 顶栏标签是旧的

`AIProviderSettings` 保存后 `invalidateQueries({ queryKey: ['health-deps'] })` ——
**没有任何查询用这个 key**。`TopBar` 用的是 `['health', 'deps']`，
所以保存 Key 之后顶栏那个标签从来没被失效过，一直显示旧值。
后端 `/health/deps` 返回 `"kind":"deepseek","degraded":false`，顶栏却写「离线启发式引擎」。

改：改成失效 `['health']` 前缀（同时覆盖顶栏和设置页），reset 路径同样的问题一起修；
`TopBar` 的 `staleTime` 60s → 5s，并加 30s `refetchInterval` + 聚焦重拉 ——
一个回答"它现在跑在什么上"的标签不该躺在 1 分钟的缓存里。

**证据**：`保存配置后 /health/deps 真的重新拉取了（无需刷新页面） — 1 次`，
顶栏 `deepseek · deepseek-flash`。

**这条检查被反向验证过**：把 `['health']` 改回 `['health-deps']`、`staleTime` 改回 60s，
重新构建后它报 `0 次` 并失败。不会失败的检查等于没检查。

### 问题 4 —— 界面太专业

**左侧菜单的前提已经不成立**：现在是 `首页 / 我的 / 工作 / 高级`，`高级` 默认折叠，
16 条旧路由全部可达 —— `verify-sidebar.mjs` 在本轮之前就是 10/10。
提示词里"左侧 16 个平铺"是旧状态。**这一处没有改动**，记在这里是因为
"改前 16 项平铺"如果写进报告就是假的前后对比。

真正改的是工作台：**产出物提到第一张卡**，三栏（执行计划 / 实时执行 / 本次上下文）
原样收进一个 `<details>` 叫「详情」，默认收起；跑的时候（`EXECUTING`）自动展开，
因为那正是有人在盯的时候。三个面板都还在、还在轮询、点一下就出来 ——
**没有为了好看删功能**。

**证据**（`verify-workbench-layout.mjs`）：已结束的任务上 `详情默认收起`、
`产出物可见`、`执行计划不可见`；展开后三栏都在。
反向验证：强制 `open={true}` → 17/19，失败的正是这两条。

> 断言用 Playwright 的可见性而不是 `innerText`：折叠的 `<details>` 内容仍在 DOM 里，
> 用文本存在性检查会在页面看起来毫无变化时依然通过。

### 顺带发现（不是用户报的）—— 运行列表跨用户泄漏

跑回归时 `verify-onboarding.mjs` 报"新账号看到了 14 个 BuildRun"。**那个数字是真的。**

`RunController` **没有 owner 字段**。注册表是进程级的（这本身没问题，一个进程驱动所有运行），
但 `GET /agent/runs` 把 `registry.all()` 返回给任何登录用户，按 id 的那批接口
（读计划、拉事件流、暂停、取消、diff、回滚）完全不检查归属。
`user: CurrentUser` 一直是这些接口的参数，**从来没被用过** —— 看起来像在校验。

改：`RunController.owner_id` 做成**必填不给默认值**（给 `None` 默认会让这个 bug 悄悄回来；
必填让它当场炸：6 个既有测试立刻以 `missing 1 required positional argument` 失败，
这正是这个改动在起作用）；`RunRegistry.owned_by()`；API 层一个 `_owned_controller(run_id, user)`
被全部 8 个按 id 的路由使用；"不存在"和"不是你的"返回**同一个错误**（区分开等于
向没有权限的人确认这个 id 存在）。

**证据**：
```
demo 启动一个 run → demo 看到 1（修复前是 14）
新账号           → GET /agent/runs = 0
未认证           → 401
新账号读 demo 的 id → 422，与一个已失效的 run 同样的消息
```

新增 `backend/tests/unit/test_run_isolation.py`（4/4）。其中一条**断言模块源码**：
每个按 id 的路由必须调用归属检查 —— 这个失败模式是"漏写"，
行为测试只有在正好走到那条路由时才会发现。反向验证：删掉一个检查，
它直接报 `cancel_run does not verify run ownership`。

### 测试卫生（自己造的坑，已填）

`verify-config-gate.mjs` 要让设置页变脏才能按保存，于是写了一个占位 Key。
那是一次**真实**写入，进程级生效，覆盖了 `.env` 里的真 Key，
之后每个 `verify-onboarding.mjs` 都以 DeepSeek 的 422
`Your api key: ****0000 is invalid` 失败。当时看起来像产品坏了。
现在写入和 `DELETE /ai-config` 成对，并有两条断言验证清理成功。

---

## 九、仍然 NOT VERIFIED

- **问题 2 的"拦住"分支没有在浏览器里跑过** —— 本机 `llm.degraded=false`（Key 配好了），
  所以只验证了**不拦**的那一侧（而且重点验证了 embedding 走离线时不会被误拦）。
  要验证真正拦住的那一侧，需要一个 llm 不可用的状态（清掉 Key 或指向一个坏端点）。
  `verify-config-gate.mjs` 里那两条断言已经写好，条件满足时会自动改跑那一侧。
- **`verify_deepseek.py` 62/62 与冷启动 36/36 是历史值，本轮未重跑。**
- **skill 层（第 2 级）的毫秒数未测** —— 它建起来了，但还没有用同一套方法证明它比
  通用路径快。`scripts/measure_tiers.py` 可以测，需要 PG 在跑。
- `docs/` 之外还有别的文档（`docs/thesis/` 中文各章、`docs/archive/` 历史文件）**未逐份核对**。
- **`start.sh` / `stop.sh` 未验证** —— POSIX 路径从未在本机跑过。
- 沙箱是**进程级隔离 + 检测 + 回滚**，不是内核沙箱 ——
  它能发现并撤销越界写入，**不能阻止它发生**。
- 未配置 `GITHUB_TOKEN` 时用仓库搜索（开放），候选池更小；代码搜索需要 token。
- Embedding 仍是离线哈希 n-gram（DeepSeek 无 embedding 接口）。
- Ollama / OpenAI 端点未实测 · Docker 路径未验证。
- **安装包版本 2.1.0 与壳版本 2.0.0 不一致** —— 这是有意的（两者版本不同的东西），
  已在 `docs/deployment.md` 写明；但**没有做版本号的统一策略**。
- **`archive/releases/` 里还留着 8 个历史源码包**（`e62ed31` / `d4642ca` / `37360ac` 等）。
  没删是因为无法判断哪些还有用；但它们会让"哪个是最新的"变得不清楚。
