# PROGRESS — 断点

工作区：`C:\Users\user\Desktop\ai-workbench`
交付物对应 commit：安装包 `b617372` · 源码包 `b617372`

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

## 二、状态：第 1–8 步完成，之后又修了三轮用户实测问题

| 轮次 | 内容 | 状态 |
|---|---|---|
| 第 1–8 步 | 安装程序 / 开局对话 / 分级检索 / Skill / 知识库 / 记忆 / 界面 / 自检 | **完成** |
| 第 9 轮 | 提交后不开始 · 没配模型能提交 · 顶栏标签旧 · 界面太专业（+ 跨用户 run 泄漏） | **完成** |
| 第 10 轮 | 下载/打开点了没反应 · 任务做不全（PARTIAL）· 界面改对话式 | **完成** |
| 第 11 轮 | 记忆让用户做质检员（候选不去重 · 测试垃圾进库 · 没有清理） | **完成** |
| 第 11 轮附带 | 让应用在提权 shell 里也能起来（安装验收 30/33 → 32/33；非提权 33/33） | **完成**，一条已知缺口见第九节 |

### 第 10 轮（用户装上软件实测）

| # | 问题 | 根因 | 状态 |
|---|---|---|---|
| 1 | 下载点了没反应 | `<a href>` 不带 `Authorization`，端点 401 | **完成** |
| 1 | 打开点了没反应 | `window.open('file:///…')`，WebView 拦 | **完成** |
| 2a | 好文件被标「未校验」 | `run_state` 被塞进文件级 checks，`passed` 被拉黑 | **完成** |
| 2b | 简单任务 PARTIAL | `_final_verify` 只给一次工具调用，猜错路径即判 FAILED | **完成** |
| 3 | 界面太专业 | 首页是四张卡片堆叠的控制台 | **完成** |

### 第 11 轮（用户看到记忆页）

| # | 问题 | 根因 | 状态 |
|---|---|---|---|
| 1 | 4 条待确认说同一件事 | 去重只比 `status='active'` 的记忆，从不比待确认/已拒绝 | **完成** |
| 2 | 6 条测试垃圾进正式库 | `verify-context-ui.mjs` 往真库写、从不删 | **完成** |
| 3 | 没有清理手段 | 修产生机制 ≠ 清掉历史 | **完成** |
| 4 | 界面像"请你验收我的作业" | 待确认列 N 条、已生效一长串 | **完成** |

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

### 记忆去重的判据是**测出来的**，不是猜的

`scripts/sample_memory_similarity.py` 打 49 对（真实库 + 手写已知答案）的分布：

```
                                   jaccard   contain
同一件事  毕业设计「需要写」vs「要写」      0.647     0.786
不同的事  「用 Python」vs「用 Java」       0.778     0.875   ← 更高
```

**不是同一件事的那对，在两个指标上都比是同一件事的那对更高。** 所以没有阈值能分开
——任何阈值不是把 Python 并进 Java，就是漏掉同义改写。

结论：**「是不是同一件事」交给模型**（`evaluator._judge` 本来每次就要调一次，
把待确认和已拒绝一起给它，零额外成本）。词法指标只保留它能做的：**近重复**
（`same_thing`，阈值 0.60 + 「指向的事物必须相同」的护栏 —— 护栏是唯一能分开
Python/Java 的信号）。

### 记忆清理（真实库，跑之前 / 之后）

```
清理前  13 条已生效 / 4 条待确认
清理后   3 条已生效 / 2 条待确认
         ├─ 7 条测试垃圾        删除
         ├─ 4 条同义 → 1 条       use_count 278 → 1648（累加，不是从头）
         └─ 2 条真正不同的       封面信息 / 报告完整性 —— 一条都没被合并
```

### 自检（本轮全部重跑）

```
verify_installer_static.py        41/41
verify_installer.py --apply       33/33  ← 非提权运行（用户真实路径，见下方说明）
                                  32/33  ← 提权运行：唯一失败是强杀后 5432 未释放
verify-conversation-ui.mjs        22/22   ← 本轮新增
verify-delivery-ui.mjs            44/44
verify-sidebar.mjs                12/12
verify-workbench-layout.mjs        9/9
verify-deliverable-actions.mjs    15/15   ← 本轮新增
verify-config-gate.mjs            12/12
verify-shell-behaviour.mjs        21/21
verify-focus-grid.mjs             17/17
verify-skill-ui.mjs               14/14
verify-knowledge-and-memory-ui    13 通过 / 0 失败
verify-onboarding.mjs             25/25   隔离断言 7/7
verify-context-ui.mjs             27/27
verify-agent-ui.mjs               26/26
verify-memory-cleanup-ui.mjs      19/19   ← 本轮新增
verify-no-test-litter.py          PASS    ← 本轮新增
verify-run-completes.py           PASS（终态 COMPLETED，2 份产出物全过）
后端测试                          588 收集，588 通过，exit 0
前端单测                          121 通过 / 10 跳过 · tsc 0 · lint 0 error
旧路由                            18/18（verify-conversation-ui 内）
```

> **安装验收要按用户的方式跑。** 用户是双击快捷方式启动的，shell **不提权**。
> 本 agent 宿主是提权的，那是一条不同的路径（PostgreSQL 必须以受限令牌启动），
> 所以在本机直接跑会得到 32/33。按用户的方式跑：
>
> ```powershell
> # 先 scripts\stop.bat，再用受限令牌启动验收
> runas /trustlevel:0x20000 "powershell.exe -NoProfile -ExecutionPolicy Bypass -File <项目>\.runtime\_run_acceptance_unelevated.ps1"
> # 结果在 .runtime\logs\acceptance-unelevated.log
> ```
>
> 实测 `elevated=False` → **33/33**，含 `[PASS] PostgreSQL port 5432 released`。

---

## 四、交付物（已重建）

| | 路径 | 体积 | 条目数 | SHA256 |
|---|---|---|---|---|
| 安装包 | `build/installer/AI-Workbench-Setup.exe` | 97,176,954 B | payload 8,707 文件 / 443.5 MB | `eb2f025bc7bdfab2291fbf1149e4084713c2a9306e57f000e81fce5b5975d8d4` |
| 源码包 | `archive/releases/AI-Workbench-b617372-final.zip` | 9,957,900 B | 540 条目 | `1b0d0c4ffdab6f644431307bb0eb37d037da42fef011eeecde28926e8b03156b` |

两者都对应 `b617372`。源码包由 `git ls-files` 生成 —— 540 个跟踪文件全部纳入、
0 个被策略排除，自带的凭据扫描 PASS。判断包新不新，读 `entries` 与
`git ls-files | wc -l` 是否一致（现在都是 540）。

**重建链**（顺序不能变，第 3 步必须在第 2 步之后）：

```bash
cd frontend && npm run build
cd ../desktop && npm run build
cd .. && .venv/Scripts/python.exe scripts/build_installer.py --build
.venv/Scripts/python.exe scripts/build_delivery_zip.py --output archive/releases/AI-Workbench-<hash>-final.zip
```

> 验收会真的装一遍、启动、注册账号、强杀、卸载。**跑之前必须先把开发环境的
> 5432/6379/6333/8000/5173 停掉**（`scripts\stop.bat`）——否则它测的是开发环境的服务。

---

## 五、不可谈判的设计（改了就是产品坏了）

1. **三道门都在 `adopt()` 里，故意不拆**：用户确认 + 安全扫描 + 脚本沙箱。
2. **个性化槽位空着保留 `{{param}}` 占位符**，空槽记进 `verification.unfilled_params`。
3. **搜索本身不导入任何东西。**
4. **矛盾记忆永远不被合并** —— 它们相似度高，会被合并步骤先吃掉。
5. **失败可见**：`None` / 空列表 / 空串不许同时表示"没有"和"出错了"。
6. **一个决定只能有一个实现者。** 本轮把「是不是同一件事」从两份实现
   （`evaluator._similarity` 0.82 与 `lifecycle.similarity` 0.62，且互相矛盾）
   收敛成 `memory/similarity.py` 一份；把「哪些工具产出文件」从两处收敛成
   `ai/tools/registry.py` 一份。
7. **"创建"和"开始"必须是同一个动作。**
8. **`DRAFT` 必须读起来就是"还没开始"。**
9. **归属必须显式。**
10. **文件好不好 ≠ 这次运行走完没有**（本轮加的）。`checks` 回答前者、决定
    `verified`；`run_state` 回答后者，是并列字段、永远不参与 `passed`。
    两者塌缩成一个，好文件就会被标成「未校验」。
11. **"核验没通过" ≠ "核验没跑成"**（本轮加的）。工具报错是「检查没发生」，
    不是「答案是否」。`_final_verify` 因此返回 `(passed, conclusive, evidence)`。
12. **记忆的删除必须让用户看见**（本轮加的）。`plan()` 只算不写、`apply()` 只执行
    用户看过的计划。重复的**只标记 `merged`**（"他用四种说法讲过"本身是信息）；
    垃圾才真删，且逐条列出来。
13. **`superseded` 不是 `rejected`**（本轮加的）。把重复候选折进更完整的一条，
    不是用户说了不。记成拒绝会让「拒绝过的不再提」把这条事实永久静音。
14. **进程的清理只能靠 job 对象，不能靠父子关系**（本轮加的）。桌面壳建一个
    `KILL_ON_JOB_CLOSE` 的 job、把启动器放进去，所有后代自动继承 —— 强杀时由操作系统
    收尾。**任何绕开启动器去启动的进程都会漏出这个安全网**：PG 走计划任务降权那条路
    时就是这样（验收里唯一一条红的）。**已知未修**，见第九节；但那条路只在 shell
    提权时才走，用户双击启动的路径不受影响。

---

## 六、编码规则（已踩过多次）

| 文件 | 规则 |
|---|---|
| `.bat` | 必须**纯 ASCII**（cmd.exe 按 ANSI 读） |
| `.ps1` | 含非 ASCII 必须带 **UTF-8 BOM** |
| `.nsi` | 含中文必须带 **UTF-8 BOM** |

- 提交信息用 **`git commit -F <文件>`**（PowerShell 会吃掉反引号）
- **Edit 工具会把整份文件的 CRLF 归一成 LF。**
- **失效一个 key 前先 grep 它到底存不存在。**
- **测试写进系统的状态必须自己清掉。** 本轮又踩一次：清理测试第一版在模型调用
  失败时留下 3 条记忆，而它自己正是来抱怨垃圾的。现在失败路径也收尾。
- **`<details>` 折叠后内容仍在 DOM 里** —— 用 Playwright 的 `isVisible()`。
- **浏览器测试要等事件，不要等时长。** 退回接口会同步跑记忆判定（一次真模型调用），
  6 秒的固定等待读到的还是 textarea 里的字，断言"通过"在了错的东西上。
- **改后端要重启进程才生效；改前端要 `npm run build`** —— API 托管的是 `frontend/dist`。
- **推理模型 + 小 `max_tokens` = 没有回答，不是被截断。** 本轮清理接口硬编码 2000，
  实测思考用了 6,212 字符 → 502。用 `reasoning_budget()`。
- **`str.format` 会把提示词里的 JSON 花括号当占位符。** 用 `replace()`。
- **Playwright/脚本里不要调用 `powershell`/`cmd`** —— 在本环境被安全策略拦，
  而且失败得很安静（`stdout=None` 会被读成"没有新进程"）。枚举进程用 `ctypes`。
- **从提权的 shell 启动 PostgreSQL 会失败**（`Execution of PostgreSQL by a user with
  administrative permissions is not permitted`）。`scripts/infra-start.ps1` 检测到提权后
  会依次试三种降权启动，每一种都在别的场合失败过，实测记录在
  `scripts/_restricted-launch.ps1` 的文件头：
  ① `runas /trustlevel:0x20000`（普通提权终端可用，桌面宿主里 `exit 1` 且无输出）；
  ② 进程内受限令牌（需要 `SeImpersonatePrivilege`，本机令牌拿不到，`win32=5`）；
  ③ **计划任务 `RunLevel = Limited`** —— 不需要任何特权，在本机唯一可用。
  任务在端口起来后立刻注销，**进程不会被杀掉**（实测：端口仍在听，任务已消失）。
  **但这也让 PG 离开了 job 对象** —— 它成了计划任务服务的子进程，不再是启动器的后代，
  于是强杀应用时它活了下来（验收报 `[FAIL] PostgreSQL port 5432 released`，其余端口都释放了）。
  试过三种修法，**都失败**，别重复走：
  ① 桌面壳按 `postmaster.pid` 收养 —— `AssignProcessToJobObject` 报 `win32=5`（提权进程
     给受限令牌进程赋值需要 `SeAssignPrimaryTokenPrivilege`/`SeIncreaseQuotaPrivilege`，
     本机一个都拿不到）。**这条留在代码里**（`lifecycle::adopt_escaped_services`），
     每次提权启动都会写一行 `adopt: postgres N is NOT in the job ... win32=5` ——
     保留它是因为它是这条缺口唯一的运行时信号（"失败可见"）；
  ② 把 job 命名、由受限令牌的 `_start-postgres.ps1` 先加入 job 再启动 PG —— 同一句话
     还是 `OpenJobObjectW win32=5`；
  ③ 换 DACL（`OW` / `AU`）并降强制完整性标签到 Medium —— 仍然是 `win32=5`。
  结论：**提权进程建的 job，受限令牌就是打不开**，原因未定位。只在提权那条路出现。

---

## 七、环境备忘

- **不要打开 Smart App Control**
- 启动整套：`scripts\start.bat --hidden`（PG 5432 / Redis 6379 / Qdrant 6333 / API 8000 / Web 5173）
- 停止整套：`scripts\stop.bat` · 只重启应用：`scripts\restart.bat --hidden`
- 后端测试需要 PG 在跑；不需要 DB 的测试放 `backend/tests/unit/`
- git 不在 PATH，PortableGit 在
  `C:\Users\user\.workbuddy-ai\binaries\PortableGit\versions\1.2.0\cmd`
- `pytest` 的 `addopts` 已带 `-q`，所以 `pytest -q` 是**双 -q**，不打印汇总行。看 exit code。
- **`restart.bat` 的退出码不会传回调用方**，看到 `failed` 先查端口再下结论。

---

## 八、这三轮做了什么

### 第 10 轮

**问题 1 —— 下载 / 打开点了没反应。** `deliveryApi.downloadUrl` 返回一个字符串，
被用在 `<a href download>` 上：导航不带 `Authorization`，端点 401，按钮什么都不做。
`打开` 用 `window.open('file:///…')`，WebView 拦掉。改成 `requestBlob` 取字节 + `saveBlob`
（`filename*=utf-8''` 解析中文名），以及 `POST /deliverables/{id}/open` ——
**客户端只发 id，路径由后端从自己的记录里解析**，Tauri 那层「不接受页面传来的路径」
的规矩因此没被破。

**问题 2a —— 好文件被标「未校验」。** `_collect` 把 `run_state` 当成一条 `ok: False`
的检查塞进 `checks`，`passed` 因此为假。现在 `make_verification(checks, run_state=…)`
把运行级信息做成**并列字段**，界面分「文件本身 / 这次运行」两栏。

**问题 2b —— 简单任务 PARTIAL。** 从库里读出来：5 步全 DONE，文件 35,700 B / 11 页
在盘上、能读回，而 `_final_verify` 只让模型调**一次**工具，它猜的路径少了
`deliverables/` 前缀，`document_read` 报「文件不存在」，整次运行判 FAILED。
修法：把本次真实产出的路径列表喂给核验器（`artifacts` / `_artifact_block`），
取证失败可重试（区别于「答案是 no」），并把「没跑成」与「没通过」分开
（`conclusive`）。**同一个任务重跑：终态 COMPLETED，6/6 步，2 份产出物 verified=True。**

**问题 3 —— 界面改对话式。** `/` 现在是对话时间线：一轮 = 一次 build run，
用户说的话在右边、它的动作和产出在同一条线上、输入框固定在底部。状态是**一句话**
（`runSentence`）不是状态码。执行计划 / 实时执行 / 本次上下文收进「详情」（默认收起，
运行中自动展开）。侧栏降到四项 + 高级（18 条旧路由仍可达）。上一版四区首页
**没删**，搬到了 `/home`。右侧「AI 助手」面板只在对话页隐藏。

### 第 11 轮

**去重写在产生之前。** `evaluate()` 现在把 active / pending / rejected 三份清单一起给
判定器，它回 `duplicates`，然后：命中 active → 累加那条记忆；命中 pending → 累加那条
候选的 `evidence_count`（界面显示「这条我提过 N 次」，而不是列 N 条）；命中 rejected
→ 不再提议。

**测试垃圾不再进库。** `verify-context-ui.mjs` 写完自己删、并断言删掉了；档案字段也还原。
`verify-no-test-litter.py` 是回归断言：跑完全量测试后记忆表里不许有测试特征文本。

**一次性清理，先给用户看再删。** `POST /memory-cleanup/plan` 只算不写、
`/apply` 只执行用户看过的计划。界面上「检查一下记忆库」→ 列清单 →「就按这个来」。

**界面跟着改。** 待确认合并后只出现一条、每条写清依据来自哪次交付；
已生效按主题分组；候选显示提过几次。

### 第 11 轮附带：让应用在提权 shell 里也能起来（`088e0f5` / `6170b4f` / `b617372`）

安装验收本来跑不过，不是产品坏了，是**本 agent 宿主是提权的**，而 PostgreSQL 拒绝以
管理员身份运行。这条链修了三步，每一步都是被实测逼出来的：

1. **`runas /trustlevel` 在桌面宿主里不可用**（`exit 1`、无输出）。改成进程内受限令牌，
   本机拿不到 `SeImpersonatePrivilege`，`win32=5`。
2. **改用计划任务 `RunLevel = Limited`** —— 不需要任何特权，本机唯一可用。
   这一步把验收从 30/33 拉到 32/33：`API healthy`、`serves the UI`、
   `registered a fresh account` 三条由红转绿。
3. **代价是 PG 离开了 job 对象**，强杀后 5432 不释放 —— 见第六节，三种修法都失败，
   已知未修。**但用户双击启动的路径不受影响**：非提权跑验收是 33/33。

---

## 九、仍然 NOT VERIFIED

- **提权启动时，强杀窗口不会带走 PostgreSQL**（验收唯一一条红的：
  `[FAIL] PostgreSQL port 5432 released`）。原因未定位（见第六节，DACL/完整性标签/
  访问掩码都排除过了）。**只在提权那条路出现**：非提权跑同一个验收是 **33/33**，
  含这一条。**不许当成通过。**
- **「点打开之后真的弹出了程序窗口」没有验证。** 唯一能证明它的方法是真去启动那个
  程序，而那样每跑一次测试就往用户桌面上扔一个 PowerPoint 窗口——用户明确说过不要。
  现在验证到的是：接口返回 200、解析出的路径就是这个产出物的路径、`os.startfile`
  在 OS 边界被以该路径调用一次、文件不存在时明确报错。**窗口本身没有验证，不许当成通过。**
- **问题 2 的"拦住"分支（没配模型时不让提交）仍未在浏览器里跑过** —— 本机
  `llm.degraded=false`。
- **`verify_deepseek.py` 62/62 与冷启动 36/36 是历史值，本轮未重跑。**
- **skill 层（第 2 级）的毫秒数未测。**
- **`start.sh` / `stop.sh` 未验证** —— POSIX 路径从未在本机跑过。
- 沙箱是**进程级隔离 + 检测 + 回滚**，不是内核沙箱。
- 未配置 `GITHUB_TOKEN` 时用仓库搜索（开放），候选池更小。
- Embedding 仍是离线哈希 n-gram（DeepSeek 无 embedding 接口）。
- Ollama / OpenAI 端点未实测 · Docker 路径未验证。
- **安装包版本 2.1.0 与壳版本 2.0.0 不一致** —— 有意的，见 `docs/deployment.md`。
- **清理计划的稳定性依赖模型**：同一次请求可能返回 502（`ai_reasoning_budget_exhausted`
  在把预算提到 6000 之前），也可能返回空计划。计划里带编号校验，越界一律忽略，
  但**没有**对「模型说没什么可清理、而库里明显有重复」做二次核对。
- **`archive/releases/` 里还留着 8 个历史源码包**，会让"哪个是最新的"变得不清楚。
