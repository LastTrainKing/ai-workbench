# 版本一报告：稳定化与发布基线

§5 要求。日期：2026-10-14
**只写已证实的。未执行的标 `NOT RUN`。**

---

## 1. 实际完成

版本一不做 UI 改造，只把当前状态整理成可重复验证、可安全修改的基线。

| 产出 | 性质 |
|---|---|
| `scripts/scan_git_history_secrets.py` | **新增**。扫描**全部 Git 历史**（原扫描器只看已跟踪文件的当前内容） |
| `docs/release/RELEASE_BASELINE.md` | **新增**。§4.1 要求的基线 |
| `docs/release/SECURITY_RELEASE_CHECK.md` | **新增**。§4.2 要求的安全核查 |

**产品代码零改动** —— 本轮只增加了一个工具与两份文档。

### 1.1 提交

| commit | 内容 |
|---|---|
| `c4ef2d7` | release: the v1 baseline, and a secret scanner that looks at history |

起始 HEAD `b5cf78dd86c1ae788e6db8a471002daf5eaab15f`，工作区起始与结束均为干净。

---

## 2. 关键证据

### 2.1 基线（§4.1，全部实测）

| 项 | 值 |
|---|---|
| 分支 / HEAD | `feat/private-ai-employee` / `b5cf78dd…` |
| 工作区 | 干净；`git diff --stat` 与 `git diff --check` 均为空 |
| 版本号 / tag | `2.5.0` / 只有 `baseline-2.3.0-freeze`、`pre-productization`（**都不是发布 tag**） |
| 工具链 | Python 3.13.14 · PySide6/Qt 6.12.0 · Node v22.22.2 · Rust 1.98.1 |
| 迁移 | 10 个文件，head `a1e5c7b9d204`，**开发库与测试库均一致**（只读核验，未执行迁移） |
| 历史提交数 | 266 |

**发现一条要立刻处理的**：安装包构建于 `00:37`，源码最后提交于 `00:54` —— **岗位合同与记忆
投影的改动不在安装包里**。版本一不重建（版本三的闸门要求重建并对齐 commit）。

### 2.2 安全（§4.2）

| 检查项 | 结果 |
|---|---|
| 当前工作树含真实凭据 | **否** |
| 待提交 diff | 空 |
| **全部 Git 历史含真实凭据** | **否** —— 1877 个 blob / 63,561,072 字节全扫 |
| 形状命中 | 17 处（`sk-…` 14、AWS 3），**逐条复核，全部是测试夹具** |
| `.gitignore` 覆盖运行时文件 | 是 |
| `.env.example` 只含空值 | 是 |
| 需轮换的凭据 | **无** |

**新增扫描器在自身发现并修掉两个缺陷**，两个都是"工具说没问题，而它其实什么也没查"：

1. 第一版**扫了 0 个对象却报"干净"** —— `str()` 套在 bytes 返回值上得到字面量 `b'...'`，
   按换行切分只得一行，40 位 id 过滤全灭。已改为明确返回 `str`，并加自检：**少于 10 个对象
   时中止并返回非 0，不报"干净"**。
2. 第二版报 **1406 处"真实凭据泄漏"**，全是假阳性 —— 宽松模式匹配到了 `.env` 的注释分隔线
   （60 个连字符），而源码注释里到处是同样的分隔线。已改为按"这是什么"取值（键名匹配
   `*_KEY` / `*_SECRET` 的值），不按"它长什么样"。

### 2.3 内核 P0/P1（§4.3，七项在当前 HEAD 逐项确认）

| # | 不变式 | 证据 |
|---|---|---|
| 1 | 结束信号晚于持久化 | `autonomous_service.py:264` `controller.settled = True` 在 `finally` 内、员工释放与 `_persist_run` **之后** |
| 2 | 状态一致性 | `:333` `run.finished_at = now`（此前从不写入）+ `latency_ms` 由 `started_at` 推导 |
| 3 | 异常释放 | `:259` `_release_employee(...)` 在 `finally` 内，独立会话、不抛异常 |
| 4 | 桌面真实启动路径 | `bootstrap.py:294` `report["reconcile"] = await _reconcile()` —— 挂在 `ensure_ready()` 而非 FastAPI lifespan |
| 5 | 文件交付真实性 | `build_executor.py:298` 无产物时 `COMPLETED → WAITING_USER` |
| 6 | 权限与隔离 | `permissions.py` 工具白名单判定；隔离测试见 `test_api_contract.py` / `test_employees.py` |
| 7 | 密钥与缓存 | `ai_config.py:164,187` 配置写入后 `reset_provider_cache()`；缓存键由解析后的配置派生 |

第 4 项是本轮之前修过的（`591fa2e`）：桌面端不起 HTTP 服务，所以 `app.main.lifespan` 在产品里
**从不执行**，而中断对账一直挂在那里 —— 等于没做。

### 2.4 测试（§4.4）

| 项 | 结果 |
|---|---|
| ruff（`backend/` + `desktop-native/`，项目既有范围） | All checks passed |
| **后端全量测试** | 见 §6 |
| QML 页面加载 | 20/20 |
| Shell 验收 | 31/31 |
| 布局审计（19 页 × 3 档） | 0 处问题，0 QML 错误 |
| 投影层回归（58 条断言） | 58/58 |
| 秘密扫描（历史） | 见 §2.2 |

---

## 3. 发布阻断

| 级别 | 项 | 根因 | 影响 | 是否阻断 GitHub 发布 |
|---|---|---|---|---|
| **P0** | 安装包落后于源码 | 版本一未重建 | 安装版不含岗位合同与记忆投影 | **是**（§16 明确列出"安装包与源码 commit 对不上"） |
| **P0** | 干净全量测试未完成 | 见 §6 | 无法声明"测试通过" | **是**（§16 明确列出） |
| **P1** | `build/rc/` 不在版本控制 | 三个验收工具（shell / 布局 / QML 加载）在 gitignore 里 | 干净克隆无法复现这些验收 | 否（但削弱 §15 的可复现性） |
| **P1** | `scripts/` 有 157 个既有 lint 问题 | 项目 lint 范围历来只覆盖 `backend/` + `desktop-native/` | 无 | 否 |
| **P1** | 真实桌面截图未做 | 本环境为无头；`offscreen` 报告零个字体族 | 视觉正确性未在真实桌面确认 | 否（但发布说明须写明） |
| **P2** | 测试夹具的 `sk-…` / `AKIA…` 持续触发扫描告警 | 有意为之（必须像真凭据才能测出"该拒绝"） | 每次扫描需人工复核 | 否 |

**无未处理的 P0 之外的核心阻断。** 两个 P0 都属于版本三的范围，且都有明确路径。

---

## 4. 本版本构建物

**未构建安装包。** 版本一只做基线。现有产物（`build/installer/AI-Workbench-Native-Setup.exe`，
124,987,470 字节）**已确认落后于源码**，不作为本版本交付物。

---

## 5. 下一版本范围（严格）

只列达到第三版本发布门槛必需的工作：

**版本二（交付闭环与体验定型）**
1. 统一结果契约（读侧 DTO，不得成为第二套状态权威）—— §8.1
2. 运行结束页直接显示产物卡片与真实操作（预览 / 打开 / 定位 / 继续修改）—— §8.2
3. Markdown/TXT 原生预览；其他格式诚实显示类型与验证情况 —— §8.3
4. 状态语言中文化，从真实状态映射 —— §8.4
5. 按类型验证器（md/txt/docx/xlsx/pptx/pdf/code）—— §10
6. 投影层断言扩展到交付卡片与 provider 切换 —— §11

**版本三（发布准备）**
7. README / LICENSE / CONTRIBUTING / SECURITY / CHANGELOG / `.env.example` —— §14
8. GitHub Actions（静态检查 + 单元测试；Windows 构建单独 workflow）—— §14
9. 干净克隆验证 —— §15
10. 重建安装包并**对齐 commit** —— §17
11. 把 `build/rc/` 的三个验收工具纳入版本控制（P1，可复现性）—— §15
12. tag + `RELEASE_NOTES.md` + 推送命令（**未经授权不推送**）—— §18

**不做**：多智能体、插件市场、远程服务、浏览器自动化、WebView、第二套运行时。

---

## 6. 全量测试结果

**状态：RUNNING（未完成）**

命令（干净进程）：

```
cd backend && ../.venv/Scripts/python.exe -m pytest tests -q -p no:randomly --tb=line -rs
```

| 项 | 值 |
|---|---|
| 起始 HEAD | `c4ef2d7` |
| 环境 | 真实 PostgreSQL / Redis / Qdrant（非 stub） |
| 测试库 | `aiworkbench_test`，alembic `a1e5c7b9d204` |
| 日志 | `build/rc/v1-full-test.log` |
| 进度 | 24%（本报告提交时仍在运行） |

**本节在测试跑完前不填通过/失败数字。** §16 把「最终干净全量测试没有实际执行且被报告成通过」
列为发布闸门 —— 所以这里如实写 RUNNING，而不是从分组结果拼一个结论。

跑完后填入：passed / failed / skipped 实数，以及每个 skipped 的原因。

**已知的一次教训**：本轮在测试运行期间并发跑了 Git 历史扫描（读取 63 MB 对象）与若干数据库
查询，测试明显变慢。这正是上一轮那 18 个假失败（并发干扰）的同一个成因 —— 全量测试应当在
没有其他负载时跑。
