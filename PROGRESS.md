# PROGRESS — 实时断点

> 给下一个接手的对话：读完这一页就能开工，不需要读任何历史报告。
> 每次 commit 之前更新它，让它跟着代码一起提交。

---

【本阶段状态：第 3 步 PASS；第 4 步后端 + Skill 页完成；第 5、6 步完成；第 7 步部分；第 8 步未做】

## 第 3 步 分级检索 —— PASS

判定方式靠**测量**选出，不是猜。`scripts/sample_relevance.py` 采样 8 个覆盖 + 6 个不相关问题，
打印每个候选信号的两组分布。`vector_top` 重叠只有 0.007（融合分 0.337），
因为绝对相似度**不按返回集归一化**；再与稀疏分取较大值 → 两组分开，间隙 0.140。
`RELEVANCE_FLOOR = 1.693`。**间隙只有 0.14、样本 14 个，余量不宽，如实写明。**

实测（各 5 次中位）：记忆 1.6ms · 知识库 35.8ms · 都不命中 25.1ms（**真的落通用路径**）。
记忆比通用路径快 **15.5×**。

## 第 4 步 Skill 层 —— 后端与界面完成

| 文件 | 内容 |
|---|---|
| `skills/schema.py` | 格式 + 手写 front matter 解析；无 `source` 直接拒绝 |
| `skills/scanner.py` | 7 类规则全部阻止入库（21 测试） |
| `skills/registry.py` | 匹配 / 采纳门 / 个性化绑定 / 使用计数 / 复盘（20 测试） |
| `skills/discovery.py` | FIND + ASSESS，**真实 GitHub 实测**：3 词 → 7 候选 → 留 5 |
| `api/v1/skills.py` | 6 个端点（10 个 HTTP 测试） |
| `features/skills/SkillPanel.tsx` | Skill 页，**浏览器实测 14/14** |

**搜索本身不导入任何东西**（测试数了 API 调用：adopt 0 → 0）。

## 第 5 步 知识库自动补充 —— 完成（11 测试）

真实文件夹（5 个毕设文件 + 2 个菜谱 + .DS_Store + node_modules + 二进制）：
分类正确、近重复按字符 shingle 检测并报百分比、缺口**只提议不下载**。

## 第 6 步 记忆生命周期 —— 完成（20 测试）

合并 / 衰减 / 冲突 / 遗忘。**测试抓到一个真 bug**：矛盾的记忆相似度高达 0.75，
会被合并步骤先吃掉 —— 那是系统在悄悄删掉用户的一个信念。已排除。

## 第 7 步 界面 —— 首页四区已有；工作台 tier 行 PASS

`POST /retrieval/tiers` HTTP 实测通过，**界面也实测通过**：

```
/workbench/10fde5de-06c7-438c-8826-27c5fd3218fe
tier 调用 1 次
页面文字：这次由「知识库」回答 —— 没有走通用推理，用的是你自己的东西
          记忆 未命中 3ms · Skill 未命中 5ms · 知识库 命中 51ms
```

**我上一次把它标成 FAILED 是错的，而且错法值得记下来：**
我传的是 AI 运行 id（`run_fb68...` / `e90d141c-...`），但工作台的 `:runId` 要的是
**BuildRun 的 id**（`deliveryApi.buildRun(runId)`）。三种地址都渲染运行列表，
不是因为页面不会选，是因为**系统里一个 BuildRun 都没有**（`build-runs` 返回 0 条）。
我建了一条，页面立刻正确渲染。

**又是"用推断代替证据"**：我看到"没选中"就断定是选择逻辑坏了，没先问"有没有可选的"。

## 第 8 步 全量自检 —— 已跑，结果如下

| 检查 | 结果 |
|---|---|
| `verify_installer_static.py` | **41/41** |
| `verify_pgsql_trim.py` | **13/13** |
| `verify_deepseek.py` | **62/62**（修掉 provider 假声明后，原 59/60） |
| `verify-onboarding.mjs` | **25/25** |
| `verify-skill-ui.mjs` | **14/14** |
| `verify-context-ui.mjs` | **22/22** |
| `verify-delivery-ui.mjs` | **35/35**（原 17/27） |
| `verify-knowledge-and-memory-ui.mjs` | **13 通过 / 0 失败 / 0 未验证** |
| `verify-agent-ui.mjs` | **26/26** |
| `verify-shell-behaviour.mjs` | **21/21** |
| `verify-ai-ui.mjs` | 截图脚本，missing labels 0，console errors none |
| 后端测试 | **522 收集，0 失败** |
| 旧路由 | **18/18**（含在 delivery-ui 里） |

**本轮修掉的三个真缺陷：**

1. **一半的运行记录在撒谎** —— `autonomous_service.py` 把权限等级 `autonomous` 当成
   provider 写进运行记录，model 为空。100 条里 50 条如此。历史行改成 `unknown` 不是猜成
   deepseek。断言拆成三条（静默空洞 / 假 provider / 显式 unknown）。
2. **delivery 测试的夹具是假的** —— 用一个 .pptx 后缀的文本文件，readback 检查正确地
   拒绝了它（`PackageNotFoundError`）。**验证是对的，夹具是假的。** 换成 python-pptx
   生成的真文件（28,239 B）。
3. **那条检查自己的说明在撒谎** —— 它的 detail 无条件写"N 项检查，全部通过"，
   于是失败的那次**在自己的失败信息里报告全部通过**。正是它挡住了继续查下去的路。
   改成列出失败项和详情，`PackageNotFoundError` 才浮出来。

**本轮我自己的一个错误结论：** 我把「工作台 tier 行」标成 FAILED，理由是我试了三种地址都
没选中运行。**错了** —— 我传的是 AI 运行 id，工作台的 `:runId` 要的是 BuildRun 的 id，
而且系统里一个 BuildRun 都没有。建了一条之后立刻正确渲染：
`这次由「知识库」回答 · 记忆 未命中 3ms · Skill 未命中 5ms · 知识库 命中 51ms`。
**又是"看到没选中就断定选择逻辑坏了"，没先问"有没有可选的"。**

## 交付物（本轮重建，commit `e62ed31`）

| | 路径 | 体积 | SHA256 |
|---|---|---|---|
| 安装包 | `build/installer/AI-Workbench-Setup.exe` | 97,131,592 B | `cc36577146e3c33687e3a459cc1c4091c4a78bd766c6fc3e3cd69d8991e7ec11` |
| 源码包 | `archive/releases/AI-Workbench-e62ed31-final.zip` | 8,429,452 B / 492 条目 | `ce5adb02d35e760f407174de568574c00b16e8c0e17f2b50450c2d32b53e1ad9` |

- 前端生产构建 → Tauri build → EXE → NSIS → ZIP，**全部重跑，没有用旧产物**
- 源码包 492 条目 == `git ls-files` 492
- Secret Scan **PASS**（无凭据、无运行时产物、必需条目齐全）

## 冷启动 + 二次启动 —— **36/36 PASS**

```
projects 不变 — 2 → 2            documents 不变 — 7 → 7
tasks 不变 — 254 → 254           memories 不变 — 10 → 10
delivery_deliverables_rejected 不变 — 10 → 10
第 5 步创建的运行在重启后仍存在 — 50 → 51
第 5 步创建的产出物在重启后仍存在 — 22 → 23
desktop 进程只有一个 · API 端口只有一个监听 · 5173 依然未监听
桌面断言（快捷方式）7 PASS / 0 FAIL
```

## 文档同步 —— 已完成

| 文档 | 状态 |
|---|---|
| `SKILL.md` | **新增**：六个阶段、格式、七类安全规则、个性化注入点、两道门 |
| `RETRIEVAL.md` | **新增**：阶梯、判定方式（含两次失败的证据与分布）、真实毫秒数 |
| `MEMORY.md` | 追加「生命周期」：合并/衰减/冲突/遗忘、阈值、合并吃掉冲突那个 bug |
| `ARCHITECTURE.md` | 追加「第三层 Skill」：六阶段、个性化在哪发生、两道门、库不变垃圾场 |

## 文档同步 —— 完成（八份）

| 文档 | 改动 |
|---|---|
| `SKILL.md` | **新增** |
| `RETRIEVAL.md` | **新增** |
| `MEMORY.md` | 追加「生命周期」 |
| `ARCHITECTURE.md` | 追加「第三层 Skill」 |
| `AGENT.md` | 追加阶梯 / 失败可见 / 它自己去拿 / 恢复 |
| `README.md` | 追加「The difference」定位段 |
| `HANDOFF.md` | 指向 `PROGRESS.md` 并写清三层资产的位置 |

四份根文档现在都互相引用 `SKILL.md` / `RETRIEVAL.md`。
`docs/` 下的 `permissions.md` / `testing.md` / `security.md` / `deployment.md` / `rag.md`
是小写版本，**未逐份检查对齐**——这一条如实留着。

## 第 8 步 剩余（未做）

前端生产构建已过 · Tauri build / EXE / NSIS 安装包 / 最终 ZIP **未重建** ·
文档同步（README / ARCHITECTURE / SKILL / RETRIEVAL / MEMORY / AGENT 等）**未做** ·
冷启动 + 二次启动 **未跑**。

## 第 8 步 全量回归 + 交付 —— 未做

---

【上一阶段状态】

【上一阶段状态】

## 第 4–8 步：未开始

- 第 4 步 Skill 层（本轮核心：自己去找 → 评估 → 验证 → 用户确认 → 导入）
- 第 5 步 知识库自动补充
- 第 6 步 记忆生命周期
- 第 7 步 界面重组
- 第 8 步 全量回归 + 文档同步 + 交付重建

规格见 `docs/prompts/10-第3到8步-一次跑完.md`。

---

【上一阶段状态】

**第 2 步（开局对话 + 空账号隔离）—— 完成，`scripts/verify-onboarding.mjs` 25/25 PASS**

| 项 | 结果 |
|---|---|
| 注册后自动进 `/welcome` | PASS（实际 `/welcome`） |
| 路径 B「先随便看看」免填表进入 | PASS，刷新后不重复弹 |
| 设置页「重新看一遍新手教程」 | PASS，清标记 + 回 `/welcome` |
| 路径 A 抽取 → 展示 → 确认才写入 | PASS，`identity="计算机专业大三学生"`；确认前 identity 为空 |
| 新账号 8 类数据全为 0 | PASS 7/7（对照 demo：项目 2 / 任务 20 / 笔记 14） |
| 未走向导访问 `/projects` 被送回 `/welcome` | PASS |

**本轮修掉的两个真缺陷**

1. **注册与登录共用 `navigate(redirectTo)`** → 新账号永远进不了向导。
   修法是一个守卫（`RequireOnboarding`）而不是两个分支：注册/登录/老账号/直接访问，
   问的是同一个问题，答案只在一处。
2. **`/profile/extract` 的 `max_tokens=500`** → 推理模型把预算全花在思考上，
   **向导的主路径一次都没成功过**。`providers/budget.py` 正是为此存在，
   它的 docstring 已记录过同样两次失败。三处硬编码全部改用 `reasoning_budget()`。

**回归**：后端 430 个测试 0 失败 · 前端 114 通过 · `npm run lint` 0 error ·
`tsc --noEmit` 干净 · 静态自检 **41/41**。

---

## 已完成并验证（上一阶段）

**第 1 步（共 8 步）：安装程序。**

目标：先把 payload 优化好（剔除 `python-nuget.zip`、裁剪 pgsql 并验证），
再做最小可行安装包——先让它能装能跑，再补首次启动初始化和卸载逻辑。

**核心验收只有一条：删掉 `C:\Users\user\Desktop\ai-workbench` 之后，软件仍然能装、能跑、能用。**

推进顺序（来自 `发给新对话.txt`，不要改）：

```
1 安装程序            ← 当前
2 开局对话 + 第二个空账号验证（8 类数据全为 0）
3 分级检索（先建立"更快"的度量：三种情况的真实毫秒数）
4 Skill 层（本轮核心，从零建：自己去找 → 评估 → 验证 → 你确认 → 导入）
5 知识库自动补充
6 记忆生命周期
7 界面重组
8 全量回归 + 冷启动 + 文档同步 + 交付重建 + 最终报告
```

**硬约束**：第 1、2 步没做完之前不要动第 7 步的界面重组。

---

## 已完成并验证

### 第 1 步 · payload 优化（完成）

| 项 | 优化前 | 优化后 | 证据 |
|---|---|---|---|
| payload 原始 | 1,204.9 MB / 25,620 文件 | **439.7 MB / 8,671 文件** | `build/payload-report.json` |
| 压缩后 | 411.5 MB | **151.3 MB**（ZIP）/ **92.6 MB**（安装包） | `build/installer-report.json` |

砍掉的东西：
- `vendor/runtime/pgsql` 里 **800.1 MB** 与运行无关的目录（`pgAdmin 4` 615.9 MB / `symbols` 155.6 MB /
  `doc` 15.5 MB / `include` 12.4 MB / `StackBuilder` 0.8 MB）→ 919.8 MB 降到 119.7 MB
- `vendor/runtime/*.zip`（下载缓存，13.68 MB，同一份东西出现两次）

**裁剪已验证**：`scripts/verify_pgsql_trim.py` **13/13 PASS** —— 在临时集群上（端口 55432，
不碰开发库）跑 initdb → 启动 → pg_isready → `SELECT 1` → create/insert/count → 干净停止。
裁剪后 payload 也做过完整冷启动：PG → Redis → Qdrant → API 全 READY、health 200、UI 200、登录成功。

### 第 1 步 · 安装程序（已构建；上一个 FAILED 已从设计上消除）

| 项 | 值 |
|---|---|
| 安装包 | `build/installer/AI-Workbench-Setup.exe` |
| 体积 | **92.6 MB** |
| 压缩比 | 443.1 MB → 92.6 MB（20.9%） |
| 构建 | `python scripts/build_installer.py --build` |
| 验收 | `python scripts/verify_installer.py --apply` |

**数据布局改了，卸载缺陷从设计上消失。**

之前 FAILED 的是「卸载时保留用户数据」：把 `.runtime` 移到别处这一步失败，
而第一版那个移动**静默失败并把数据库一起删了**。

追那个移动是错的修法——问题在布局本身：数据放在安装目录里，
"删掉程序"就必然意味着"要么删数据、要么留个目录"。所以把数据**在安装时**就放到外面：

```
%LOCALAPPDATA%\AI Workbench\        程序文件，卸载时删除
%LOCALAPPDATA%\AI Workbench Data\   数据库 / 向量 / 上传，卸载永不触碰
```

路径写在 `<root>/data-dir.txt` 一行里，启动器和后端读同一行，不可能各说各话。

**这需要把路径集中化**：原先有 5 个模块各自算 `PROJECT_ROOT / ".runtime"`
（storage / ai-config / snapshots / agent workspace / agent policy）。
5 份同一个决定 = 5 次装出来的副本把状态一半写进程序目录、一半写在外面的机会。
现在全部走 `app/core/config.py` 的 `runtime_root()`。

**已验证**：解析顺序 环境变量 → `data-dir.txt` → `.runtime`；
实测无标记 → 开发目录、有标记 → `AI Workbench Data\.runtime`；
**438 个后端测试全通过**。

---

## 未完成

### 第 1 步还差

1. **重跑安装验收**（安装包正在重建）：目标 30/30。
2. 首次启动的**进度可见性**尚未单独验证（splash 是否真的显示初始化进度与失败原因）。

### 第 2 步及以后

未开始（开局对话 + 空账号数据隔离）。规格见 `docs/prompts/08-第八轮.md`
的「能力 A」与第五部分。

---

## 下一步

**第 3 步：分级检索。**

先建立「更快」的度量 —— 同一个问题在三种情况下的真实毫秒数：

```
情况 1  记忆 / skill 命中     ← 应该最快
情况 2  知识库命中
情况 3  都没有，走通用路径     ← 最慢
```

规格见 `docs/prompts/08-第八轮.md` 的「能力 E」。
第 3 步要先把度量建起来，因为第 4 步（Skill 层）做完要靠它证明真的变快了。

## 环境备忘

### 换机器重建（重装 Windows 后照这个来）

```
1. .venv —— 【不能拷贝】
   Windows 的 venv 把解释器绝对路径写在 pyvenv.cfg 里，换个机器或换个路径就是坏的。
   本项目已经因此踩过一次：复制过去的 venv 能用，只是因为原解释器还在那台机器上。
   重建：python -m venv .venv
         .\.venv\Scripts\pip install -r backend\requirements.txt

2. 原生运行时 —— 一条命令重新物化：
   powershell -File scripts\fetch-runtime.ps1
   重新下载 PostgreSQL / Redis / Qdrant / Python，Python 那一步会顺带装依赖。

3. frontend/dist —— 【不在 git 里】，需要构建：
   cd frontend && npm install && npm run build
   没有它，start-desktop.bat:88 会自己尝试 npm run build；那台机器没装 Node.js 就会失败。

4. git —— 本机 git 不在 PATH。重装后先确认：
   装了 Git for Windows → 直接可用
   没装 → 用 PortableGit，并把位置告诉 build_delivery_zip.py 的 find_git()

5. 重装后第一件事：确认 Smart App Control 是【关】的。
   开了它会再拦一次；而它从开到关不可逆，关掉又要再重装一次。
```

### 本机现状（重装后会变）

- 仓库：`C:\Users\user\Desktop\ai-workbench`，`vendor/` 与 `build/` 都在 `.gitignore` 里
- 随包解释器：`vendor/runtime/python/python.exe`（322 MB，含全部依赖）
- 原生运行时：`vendor/runtime/{pgsql,qdrant,redis}`
- 一次性物化：`powershell -File scripts\fetch-runtime.ps1`
- 测试：后端 `.\.venv\Scripts\python.exe -m pytest tests\ -q`；前端 `cd frontend && npm test`
- 编码铁律：`.bat` 纯 ASCII 且**不能用 shell 内联命令写**；`.ps1` 带 UTF-8 BOM
