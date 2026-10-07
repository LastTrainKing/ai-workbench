# PROGRESS — 断点

工作区：`C:\Users\user\Desktop\ai-workbench`
当前 commit：`cff0587`

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

## 二、状态：第 1–8 步全部完成

| 步骤 | 状态 | 证据 |
|---|---|---|
| 第 1 步 安装程序 | **完成** | `verify_installer.py --apply` 33/33 · 静态 41/41 |
| 第 2 步 开局对话 + 空账号隔离 | **完成** | `verify-onboarding.mjs` 25/25 |
| 第 3 步 分级检索 | **完成** | 见下「真实数字」 |
| 第 4 步 Skill 层 | **完成** | 后端 + API + Skill 页 + 沙箱；`verify-skill-ui.mjs` 14/14 |
| 第 5 步 知识库自动补充 | **完成** | 11 测试，真实文件夹 |
| 第 6 步 记忆生命周期 | **完成** | 20 测试 |
| 第 7 步 界面 | **完成** | 首页四区 · 工作台 tier 行 · 我的（含 Skill 页） |
| 第 8 步 自检 / 交付 / 文档 | **完成** | 见下 |

---

## 三、真实数字（不要重测，除非代码改了）

### 分级检索（各 5 次取中位）

```
记忆命中     median  1.6 ms   → 由「memory」回答
知识库命中    median 35.8 ms   → 由「knowledge」回答
都不命中     median 25.1 ms   → 由「general」回答
记忆比通用路径快 15.5×
```

判定方式：`RELEVANCE_FLOOR = 1.693`（两组分布间隙 0.14、样本 14 个）。
**余量不宽 —— 语料或嵌入一变就要重跑 `scripts/sample_relevance.py` 重测。**

> 两次猜阈值的失败记录在 `RETRIEVAL.md`。规则：**先测分布，再选阈值。**

### Skill 发现（真实调 api.github.com）

```
3 个搜索词 → 7 个候选 → 保留 5 个
筛掉 anthropics/skills → 没有声明 license
```

`fetch_skill_file` 实测：`addyosmani/agent-skills/README.md` → HTTP 200、
blob sha `c6039f4d…`、**24,729 字符**、`source_commit` = 40 位 SHA。

> **注意**：`raw.githubusercontent.com` 在本环境**不可达**（HTTP 000），
> `api.github.com` 可达（HTTP 200）。抓取走 Contents API。

### 自检（全部实跑）

```
verify_installer_static.py        41/41
verify_pgsql_trim.py              13/13
verify_deepseek.py                62/62
verify-onboarding.mjs             25/25
verify-skill-ui.mjs               14/14
verify-context-ui.mjs             22/22
verify-delivery-ui.mjs            35/35
verify-knowledge-and-memory-ui    13 通过 / 0 失败
verify-agent-ui.mjs               26/26
verify-shell-behaviour.mjs        21/21
后端测试                          544 收集，0 失败
旧路由                            18/18
冷启动 + 二次启动                  36/36
```

---

## 四、交付物

| | 路径 | 体积 | SHA256 |
|---|---|---|---|
| 安装包 | `build/installer/AI-Workbench-Setup.exe` | 97,131,592 B | `cc36577146e3c33687e3a459cc1c4091c4a78bd766c6fc3e3cd69d8991e7ec11` |
| 源码包 | `archive/releases/AI-Workbench-e62ed31-final.zip` | 8,429,452 B / 492 条目 | `ce5adb02d35e760f407174de568574c00b16e8c0e17f2b50450c2d32b53e1ad9` |

**⚠️ 这两个是在 `e62ed31` 构建的。之后改了后端代码（沙箱 `631fd48`、抓取 `cff0587`），
所以它们已经过期。** 重建链：

```
cd frontend && npm run build
cd desktop  && npm run build
.venv/Scripts/python.exe scripts/build_installer.py --build
.venv/Scripts/python.exe scripts/build_delivery_zip.py --output archive/releases/AI-Workbench-<hash>-final.zip
```

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

---

## 七、环境备忘

- **不要打开 Smart App Control** —— 从「评估中」变成「正式开启」就锁死，只能重装系统
- 启动整套：`scripts\start.bat --hidden`（PG 5432 / Redis 6379 / Qdrant 6333 / API 8000）
- 后端测试需要 PG 在跑；不需要 DB 的测试放 `backend/tests/unit/`
  （`integration/` 会拉起数据库夹具）

---

## 八、仍然 NOT VERIFIED

- `docs/` 下小写文档（`permissions.md` / `testing.md` / `security.md` /
  `deployment.md` / `rag.md`）**未逐份检查对齐**
- 交付物**已过期**（见第四节），需按新 commit 重建
- 沙箱是**进程级隔离 + 检测 + 回滚**，不是内核沙箱 ——
  它能发现并撤销越界写入，**不能阻止它发生**
- 未配置 `GITHUB_TOKEN` 时用仓库搜索（开放），候选池更小；代码搜索需要 token
- Embedding 仍是离线哈希 n-gram（DeepSeek 无 embedding 接口）
- Ollama / OpenAI 端点未实测 · Docker 路径未验证
