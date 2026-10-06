# PROGRESS — 实时断点

> 给下一个接手的对话：读完这一页就能开工，不需要读任何历史报告。
> 每次 commit 之前更新它，让它跟着代码一起提交。

---

【本阶段状态：第 3 步 **PASS**；第 4 步进行中（格式 + 安全扫描已完成并测试）；第 5–8 步未开始】

## 第 3 步 分级检索 —— **PASS**

**判定方式是这样选出来的（不是猜的）**：先写 `scripts/sample_relevance.py` 采样 ——
8 个知识库确实覆盖的问题 + 6 个合理但不相关的问题，把每个候选信号的两组分布都打出来：

```
fused_top     covered 0.294-0.977   unrelated 0.099-0.632   overlap 0.337
vector_top    covered 0.117-0.557   unrelated 0.097-0.124   overlap 0.007
keyword_top   covered 0.000-14.565  unrelated 0.000-3.246   overlap 3.246
overlap_top   covered 1.000-16.000  unrelated 0.000-2.000   overlap 1.000
combined      covered 1.763-7.283   unrelated 0.647-1.623   gap 0.140  ← 可分
```

**绝对向量相似度几乎能单独分开**（重叠 0.007 vs 融合分的 0.337）——
因为它**不按返回集归一化**，正是前两次失败缺的那个性质。
它在一处失败：离线哈希嵌入对精确短语打分偏低。稀疏分在相反处失败：换个说法就是 0。
取两者较大值，两组就分开了。`RELEVANCE_FLOOR = 1.693`（间隙中点）。

**间隙只有 0.14、样本只有 14 个，这一点如实写明** —— 这是一个能用的判据，
不是余量充足的判据。注释里写了：语料或嵌入一变就要重测，一条命令。

**修完后的真实数字**（各 5 次取中位）：

```
记忆命中     median  1.6 ms   → 由「memory」回答
知识库命中    median 35.8 ms   → 由「knowledge」回答（相关度 3.26）
都不命中     median 25.1 ms   → 由「general」回答（知识层相关度 0.72，低于 1.693）
```

**记忆命中比通用路径快 15.5×。** 第三档现在**真的落到通用路径**了。

验收：知识级可分 PASS · 三种情况真实毫秒 PASS · no-hit 落通用路径 PASS ·
工作台显示命中层级 **未做（第 7 步）** · 检索为空时的说明 **未接到界面** ·
skill 级毫秒 **待第 4 步完成后测**。

## 第 4 步 Skill 层 —— 进行中

**已完成并测试（21 个测试全绿）**：

- `backend/app/ai/skills/schema.py` —— skill 格式 + 手写 front matter 解析器
  （不用 YAML 依赖：11 个已知键，接受任意 YAML 的解析器是更大的攻击面）
- `backend/app/ai/skills/scanner.py` —— **安全扫描，7 类规则全部阻止入库**

**扫描器自己被抓到一次**：第一版只匹配调用语法（`requests.post(`），
测试用一句"上传到 https://drop.example/collect"就穿过去了 ——
**同一个外发行为，只是没写函数调用**。这正是这个模块 docstring 里警告的错误，由它自己犯的。
改成**以主机为准、动词只决定语气**。

脚本会被扫描，不只扫正文 —— 脚本才是真正执行的那一类。

**未完成**：发现（GitHub 搜索）· 评估 · 沙箱试运行 · 采纳入库 · 个性化注入 ·
复盘 · API 端点 · 前端 Skill 页 · 用第 3 步的测量证明 skill 级更快。

## 第 4 步 Skill 层 —— 后端完成一半（commit `17716f0`）

**已完成并测试（21 + 20 = 41 个测试全绿，后端全量 477 个 0 失败）**：

| 文件 | 内容 |
|---|---|
| `skills/schema.py` | skill 格式 + 手写 front matter 解析；无 `source` 直接拒绝 |
| `skills/scanner.py` | 安全扫描，7 类规则全部阻止入库 |
| `skills/registry.py` | 匹配 / 采纳门 / 个性化绑定 / 使用计数 / 复盘 |
| `skills/discovery.py` | FIND + ASSESS（真实 GitHub 搜索 + 评估） |
| `models/skill.py` | `installed_skills` 表 |

**FIND + ASSESS 已实测**（真实调 api.github.com，不是 fixture）：

```
搜索词: ['帮我写毕业论文的文献综述 skill', 'topic:agent-skills', 'topic:claude-skills']
结果:   3 个搜索词 → 7 个候选 → 保留 5 个
保留:   addyosmani/agent-skills ★101936 MIT（101936 star；3 天前更新；license MIT）等
筛掉:   anthropics/skills → 没有声明 license，无法确认能否合法使用
```

**查询构造器第一版是错的，空结果就是证据**：用用户自己的话去搜，每次都 0 条 ——
GitHub 仓库搜索匹配的是仓库名和描述，绝大多数是英文，中文短语匹配不上。
**"没搜到"和"没有"长得一样**，这是发现阶段最糟的失败。
探测后改用 `topic:agent-skills`（29,319 条）作底，用户自己的 ASCII 词作细化。

复用已有的 `PermissionPolicy.check_domain` —— 再加一个白名单就是同一个问题的第二个答案。
**搜索失败 ≠ 搜索为空**：限流会抛 `ValidationError` 并如实报出，不会静默返回空列表。

**两个门都在 `adopt()` 里，故意不拆开**：用户确认 + 安全扫描。
拆到不同调用点，就是其中一个迟早被跳过的原因。
**确认了也不够** —— 恶意 skill 即使用户说"是"也会被拒，因为用户是对自己没读过的东西说"是"。

**个性化 = `{{param}}` 绑定**。没填的槽**保留占位符而不是留空** ——
留空会把"我们还不知道你这个"变成读起来像知道的话。
哪些槽还空着记在 `verification.unfilled_params` 里，**用来问，不是用来猜**。

`SKILL_LAYER_BUILT` 已翻成 True，tier 2 接上了（传 session + user）。

**未完成**：抓取候选的 skill 文件正文（`fetch_skill_file` 已写但未对真实仓库测）·
沙箱试运行 · API 端点 · 前端 Skill 页 · 用第 3 步的测量证明 skill 级命中更快。

## 第 5–8 步：未开始

---

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
