# 清理计划（CLEANUP_PLAN）

生成时间：2026-10-10
仓库：`C:\Users\user\Desktop\ai-workbench`
分支：`fix/release-freeze-20261009`

判定原则：**只删可再生产物**。源码、Git 历史、测试、最终安装包、历史归档、用户真实数据一律不动。
每一项都必须先确认「删掉之后能不能重建」，能重建才列入 B 类。

---

## 一、A 类：必须保留

| 路径 | 大小 | 用途 | 为什么不能删 |
|---|---|---|---|
| `archive/releases/*`（13 个） | 186.3 MB | 历史发布归档，含 `AI-Workbench-2.2.0-final.exe` | 用户明确要求保留 2.2.0；其余是不可重建的历史版本 |
| `build/installer/AI-Workbench-Native-Setup.exe` | 118.4 MB | **最终安装包** | 交付物本体 |
| `build/install-backup/` | 49.4 MB | 安装前用户数据 + 配置备份 | 唯一一份安装前快照 |
| `build/user-data-baseline.json` | 220,984 B | 用户数据逐文件 SHA-256 基线 | 数据完整性核验的唯一依据 |
| `build/user-data-integrity.json` | 2,331 B | 完整性核验结果 | 证据 |
| `build/native-installer-acceptance.json` | 6,541 B | 安装验收 41 项结果 | 证据 |
| `build/native-installer-report.json` | 505 B | 安装包大小 / SHA-256 / 压缩比 | 证据 |
| `build/final-install.json` | 5,568 B | 本机最终安装核验结果 | 证据 |
| `build/qdrant-key-report.json` | 2,312 B | Qdrant key 12 项验收 | 证据 |
| `build/installer-acceptance.json` | 7,174 B | 旧版安装验收 | 证据 |
| `build/review-package-manifest.json` | 154,433 B | 已删除验收包的 1,139 项清单 | 该包已删，这是它存在过的唯一记录 |
| `desktop-native/dist/` | 249.8 MB | 已验证的原生应用构建产物 | 打包验收与安装包重建的输入；删掉要重跑 PyInstaller |
| `%LOCALAPPDATA%\AI Workbench Data\` | 68.5 MB | **用户真实数据** | 绝对禁止触碰 |

## 二、B 类：可以删除

| 路径 | 大小 | 内容 | 处理方式 | 为什么可以删 |
|---|---|---|---|---|
| `build/native-installer-payload/` | 463.8 MB | 安装包中间载荷（3928 文件） | 删除 | `build_native_installer.py` 每次从 `desktop-native/dist/` 重新装配 |
| `build/installer-payload/` | 444.0 MB | 旧 Web 安装器的中间载荷 | 删除 | 同上，旧架构产物 |
| `.runtime/snapshots/` | 680.7 MB | 17,039 个文档快照（测试产物） | 删除 | 测试运行生成，无消费方 |
| `.runtime/qdrant/` | 462.5 MB | **开发用**向量库 | 删除 | 与用户数据里的 Qdrant 是两个目录；开发栈重启时重建 |
| `.runtime/pgdata/` | 167.0 MB | **开发用** PostgreSQL 集群（id `7692108033577590720`） | 删除 | 与用户集群（id `7694244596779072996`）无关；开发栈重启时 `initdb` 重建 |
| `.runtime/workspace/` `storage/` `backups/` `logs/` `pids/` 等 | 33 MB | 开发栈运行状态 | 删除 | 运行时状态 |
| `.runtime/*.log` `*.json` 等松散文件 | ~0.1 MB | 历次构建/启动日志 | 删除 | 一次性日志 |
| `desktop-native/build/` | 66.9 MB | PyInstaller 中间目录 | 删除 | `build_native.py --clean` 会重建 |
| `build/acceptance-logs/` | 44 文件 | 安装验收的输出日志 | 删除 | 结果已进 `native-installer-acceptance.json` |
| `build/release-freeze/` | 0.1 MB | 中间脚本与旧 commit 消息 | 删除 | 一次性 |
| `build/*.log`、`build/_*.txt`、`build/_old15.py` 等 | ~60 KB | 临时调试输出 | 删除 | 一次性 |
| `%TEMP%\aiw-*` `awbnative-*` `aw-final-*` 等 99 项 | 39.6 MB | 验收脚本的一次性临时目录 | 删除 | 脚本本应自行清理，属残留 |
| `Desktop\screenshots\` | 92 KB | 一张旧截图 `tier-line.png` | 删除 | 过期截图 |

**合计可回收：约 2.4 GB**

## 三、明确不动的东西

- 任何 git 跟踪的文件（`git status` 必须保持 clean）
- `archive/releases/` 全部归档
- `%LOCALAPPDATA%\AI Workbench Data\`（用户真实数据）
- `%LOCALAPPDATA%\AI Workbench\`（已安装程序）
- 桌面 `AI Workbench.lnk`、开始菜单 `AI Workbench`

## 四、执行方式

不用 `rm -rf` 直接打。流程：

1. 逐项 `os.walk` 统计文件数 / 字节数（执行前记录）
2. 逐项删除，**记录失败的文件与异常类型**
3. 删除后复查：目标不存在、无同名残留、Git 仍 clean、安装包仍在、用户数据指纹一致
4. 任何一项删不掉 → 记录原因，不掩盖

## 五、本轮已发生的一件事故（如实记录）

在生成本计划之前，我用「逐目录删一个文件探测可删性」的方式做排查，这个方式**误删了 3 个文件**：

| 被删文件 | 影响 |
|---|---|
| `build/installer/AI-Workbench-Native-Setup.exe` | **主交付物**，已用 `build_native_installer.py --build` 重建 |
| `build/native-installer-payload/AI-Workbench-Native.exe` | 中间产物，随重建恢复 |
| `build/restored-user-data/native-startup.log` | 上一轮事故文件的副本，非唯一证据（事故本身已记录在 `NATIVE-MIGRATION-PROGRESS.md`） |

教训：探测可删性必须用**改名**（可逆），不能用删除。本计划第 4 步因此改为先统计后删除、不再做单文件探测。
