# SKILL — 你的做法

> 三层资产里的第三层。记忆是关于你的事实，知识库是你的资料，**Skill 是你做事的方式**。

---

## 一句话

**它自己出去找做法，验证过，装好，然后带着你的记忆和资料去用。**

不是等它从用户行为里慢慢长出来——那样新产品永远是空的，前几个月没用。
**主动出去找，第一天它就能带着合适的东西进场。**

---

## 六个阶段

| 阶段 | 在哪 | 做什么 |
|---|---|---|
| ① 发现 FIND | `ai/skills/discovery.py` | 由档案 + 当前需求生成搜索词，去 GitHub 搜 |
| ② 评估 ASSESS | `ai/skills/discovery.py` | 筛掉过期/无许可/不可信的，**输出候选并附理由** |
| ③ 验证 VERIFY | `ai/skills/scanner.py` | 安全扫描 + 格式校验 + 来源记录 |
| ④ 采纳 ADOPT | `ai/skills/registry.py` | **用户确认后**才入库，并绑定个性化参数 |
| ⑤ 使用 USE | `ai/skills/registry.py` | 匹配问题、渲染个性化正文、执行时仍过权限 |
| ⑥ 复盘 REVIEW | `ai/skills/registry.py` | 记使用次数、打分、必要时退役 |

---

## 格式

Markdown + front matter。手写解析器，不用 YAML 依赖——**11 个已知键，
而一个接受任意 YAML 的解析器是更大的攻击面**，而这里的输入全部来自互联网。

```markdown
---
name: weekly-report
description: 把一周的工作整理成周报
when_to_use: 需要交周报的时候
license: MIT
version: 1.2.0
params:
  - audience: 汇报对象
  - tone: 语气
scripts:
  - import csv
---
## 做法
你是 {{audience}} 的助手。按 {{tone}} 的风格……
```

| 字段 | 说明 |
|---|---|
| `name` / `description` | 必填。没有 description 就无法判断什么时候该用它 |
| `when_to_use` | **匹配时主要看这个字段**——作者填它就是为了说明"什么时候适用" |
| `body` | 做法本身。`{{param}}` 是注入点 |
| `scripts[]` | 可选。**有脚本就是脚本型**，风险更高 |
| `params[]` | **个性化注入点**，见下 |
| `source` / `source_commit` | **必填**。不可追溯 = 不能重新验证 |
| `license` | 脚本型必填 |
| `version` | 版本 |

`parse_skill()` **从不抛异常**——谁能发仓库谁就能碰到它。格式错误返回错误列表。

---

## 个性化在哪里发生

**从 GitHub 导入的 skill 是通用的，不是个性化的。** 真正让它变成你的，是绑定那一层：

```
通用骨架  ← 从公开渠道导入
    + 你的档案（{{audience}} = 院学生会指导老师）
    + 你的偏好（{{tone}} = 正式、简洁）
    = 按你的样子把这件活干完
```

**没填的槽保留 `{{param}}` 占位符，不留空。**
留空会把"我们还不知道你这个"变成读起来像知道的话——这是本项目反复出现的失败形状。
哪些槽还空着记进 `verification.unfilled_params`，**用来问，不是用来猜**。

---

## 安全验证

`scan_skill()` 在入库前跑，**七类规则全部阻止入库**：

| 规则 | 拦什么 |
|---|---|
| `fetch-and-run` | 先下载再执行（`curl ... \| bash`） |
| `outbound-network` | 外发数据 |
| `external-host` | 指向白名单外的任何主机 |
| `credential-access` | 读 `.env` / `.ssh` / `.aws` |
| `privilege-or-sandbox-escape` | `shell=True`、`os.system`、`eval` |
| `destructive` | 删除/加密用户文件 |
| `prompt-injection` | 针对模型的指令 |

**扫描是行为导向的，不是关键词导向的。** 第一版只匹配调用语法（`requests.post(`），
一句"上传到 https://drop.example/collect"就穿过去了——**同一个外发行为，只是没写函数调用**。
现在是**以主机为准，动词只决定语气**。

**脚本会被扫描，不只扫正文**——脚本才是真正执行的那一类。

**prompt 注入是阻止而不是警告**：skill 正文会被注入 prompt，
针对模型的指令是对调用方的攻击，警告会让它进入它被设计要去的地方。

---

## 两道门都在 `adopt()` 里，故意不拆

```python
async def adopt(session, *, user_id, skill, confirmed, param_values=None):
    if not confirmed:
        raise AdoptionRefused(["未经用户确认，不能入库"])
    scan = scan_skill(skill)
    if scan.blocks_adoption:
        raise AdoptionRefused([...])
```

**拆到不同调用点，就是其中一个迟早被跳过的原因。**

**确认了也不够**——恶意 skill 即使用户说"是"也会被拒，
因为**用户是对自己没读过的东西说"是"**。

**搜索不写入任何东西。** 没有"搜到就装"的路径；评估阶段存在的意义就是让人选。
浏览器测试数了 API 调用证明这点：`adopt 0 → 0`。

---

## 库不会变成垃圾场

`installed_skills` 记录让 skill **可复盘**而不只是被存着的东西：

- `source` + `source_commit`——不可追溯就不能在环境变了之后重新验证
- `verified_at` + `verification`——**入库时扫描发现了什么，原样保留**
- `use_count` + `last_used_at`——复盘需要证据
- `rating`——`None` 表示没人评过，**这和"它没问题"是两回事**

`record_use()` **成功失败都记**——只记成功会让 `use_count` 变成质量分而不是使用次数，
而复盘要看到的恰恰是"试了但没用起来"的那个，那才是该退役的。

`retire` 是**停用不是删除**：一个被导入、用过、判定不好的 skill，
是关于那个来源的证据，删掉就扔掉了。

---

## 真实数字

**发现（真实调 api.github.com）**：3 个搜索词 → 7 个候选 → 保留 5 个

```
保留: addyosmani/agent-skills  ★101936  MIT
筛掉: anthropics/skills        → 没有声明 license，无法确认能否合法使用
```

**搜索词构造器第一版是错的，空结果就是证据**：用用户自己的话去搜，每次都 0 条——
GitHub 仓库搜索匹配的是仓库名和描述，绝大多数是英文，中文短语匹配不上。
**"没搜到"和"没有"长得一样**，这是发现阶段最糟的失败。
改用 `topic:agent-skills`（29,319 条）作底。

---

## 已知限制

- 未配置 `GITHUB_TOKEN` 时用仓库搜索（开放），**候选池更小**；代码搜索需要 token
- `fetch_skill_file()` 已写，**未对真实仓库端到端跑过**
- **沙箱试运行未实现**——脚本型 skill 目前过扫描但不过沙箱
- 限流时报 `ValidationError` 并如实说明，不静默返回空列表

---

## 相关文件

```
backend/app/ai/skills/schema.py     格式与解析
backend/app/ai/skills/scanner.py    安全扫描
backend/app/ai/skills/registry.py   匹配/采纳/个性化/使用/复盘
backend/app/ai/skills/discovery.py  发现与评估
backend/app/models/skill.py         installed_skills 表
backend/app/api/v1/skills.py        HTTP 端点
frontend/src/features/skills/       Skill 页
scripts/verify-skill-ui.mjs         浏览器验证（14/14）
```
