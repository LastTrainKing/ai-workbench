# PROGRESS — 实时断点

> 给下一个接手的对话：读完这一页就能开工，不需要读任何历史报告。
> 每次 commit 之前更新它，让它跟着代码一起提交。

---

【本阶段状态：第 3 步进行中（模块已写，**未测**）；第 4–8 步未开始】

## 第 3 步 分级检索 —— 当前真实状态

**已写**：`backend/app/ai/rag/tiers.py`

```
① memory     一次索引查表，按词重合度打分（不用向量：这一层要最快）
② skill      第 4 步填；现在用 SKILL_LAYER_BUILT=False 明确报"尚未建立"
③ knowledge  一次混合检索
④ general    推理路径，唯一不个性化的一层
```

每一层记录 **命中与否 + 毫秒**，`TieredResult.explain()` 输出一行
「由「memory」回答 · memory=命中(3ms) · skill=未命中(0ms) · …」。
**走到第一个能答的层就停** —— 提前停止本身就是优化，四层全问会让度量失去意义。

`backend/app/ai/skills/registry.py`：**故意留的桩**。
它返回空列表，但**同时暴露 `SKILL_LAYER_BUILT = False`**，让 tier 2 报
「skill 层尚未建立」而不是「没有匹配的 skill」——
**空列表和"没查过"在报告里长得一样，但只有一个是真的。**

**已验证**：ruff 干净；模块可导入；`TIERS` 与 `SKILL_LAYER_BUILT` 取值正确。

**NOT VERIFIED（必须补）**：
- **三种情况的真实毫秒数还没测出来** —— 这是第 3 步的验收核心，没它"更快"就是空话
- 三级各自的命中路径没有测试
- 检索为空时用户看到的说明还没有接到界面上
- 工作台"本次上下文"还没显示"这次是从哪一级拿到的"

**下一步（具体到命令）**：写 `scripts/measure_tiers.py`，用真实会话建三类问题
（命中记忆 / 命中知识库 / 都不命中），调 `resolve()` 并打印真实毫秒数。
会话工厂在 `backend/app/infrastructure/database.py`。

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
