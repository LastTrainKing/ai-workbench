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

## 第 7 步 界面 —— 首页四区已有；工作台加了 tier 行

`POST /retrieval/tiers` **HTTP 实测通过**：`由「memory」回答 · memory=命中(10ms)：命中 5 条（共 7 条）`。
**界面渲染 NOT VERIFIED** —— 上下文卡片只在选中一次运行时渲染，我的浏览器检查没打开运行。

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
