# PROGRESS — 实时断点

> 给下一个接手的对话：读完这一页就能开工，不需要读任何历史报告。
> 每次 commit 之前更新它，让它跟着代码一起提交。

---

【本阶段状态：第 3 步**部分完成**——度量已跑出真数字，但"是否命中"的判断**仍不可信**】

## 第 3 步 分级检索 —— 真实状态

### 已跑出的真实数字（`scripts/measure_tiers.py`，各问 5 次取中位数）

```
记忆命中        median  1.9 ms   (min 1.5, max  4.1)   ← 由「memory」回答
知识库命中      median 24.9 ms   (min 21.4, max 1337.2)
都不命中        median 25.0 ms   (min 24.7, max 26.7)
```

**记忆命中比通用路径快 13.1×（检索层，不含模型推理）。**

一次 1337 ms 的尖峰出现在知识库档（首次调用，嵌入/索引冷启动）——**如实列出，没有剔除**。

### 但有一个**没解决的问题**，必须写清楚

**"是否命中"的判断目前不可信。** 两次尝试都失败了，而且我有证据：

| 尝试 | 结果 | 证据 |
|---|---|---|
| 用检索器的 fused score + 0.35 阈值 | **失败** | 完全无关的问题（"解释量子纠缠和相对论"）拿到 **0.70 分**，远高于阈值。该分数是在返回集内归一化的，无论有没有匹配，top 值都高 |
| 改用**词重合**门限（≥2） | **仍失败** | 同一个无关问题这次**命中**了知识库（2 段）。CJK 二元组在常用词上大量伪重合（"关系"、"一下"） |

**后果**：上表里"知识库命中 24.9 ms"这一行**不可信**——那个"命中"是假的。
"都不命中 25.0 ms"这一行**实际走的是知识库**，不是通用路径。

**所以「三级命中路径」这个验收项是 `FAILED`，不是 PASS。**

### 下一步该怎么修（有方向，没做）

需要一个**真正有判别力的相关性信号**。候选：

1. **用 `keyword_score`（稀疏/BM25 原始分）而不是归一化后的 fused score** ——
   检索器已经算了这个字段，但**我还没测它的分布**，不能假设它可用
2. **给 `_terms` 加停用词表** —— 现在"关系""一下"这种词和"毕设""部长"权重相同
3. 直接**在文档层面判断覆盖**（问题里的实词是否出现在该文档的标题/概念里），
   而不是在 chunk 层面比字符串

**无论选哪个，都要先测分数分布再定阈值，不能再猜。** 这一轮已经因为"猜一个阈值"失败两次了。

---

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
