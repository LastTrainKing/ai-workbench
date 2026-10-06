# 重装 Windows 之前，先备份这些

> 这份是给你照着做的清单，不是笔记。
> 你要重装 Windows 来解除 Smart App Control 阻塞。**重装会清空这台机器上的所有环境状态。**

**一句话：最省事的做法是把 `C:\Users\user\Desktop\ai-workbench` 整个文件夹拷到移动硬盘，
再单独把 `.env` 抄一份。** 下面按重要性说明为什么。

---

## 1. 【丢了要重新申请】`ai-workbench\.env`

**这是整份清单里最要紧的一条。**

里面是 API Key。它被 `.gitignore` 排除，**不在源码包里，也不在 git 里**——
换句话说，如果只靠 git 恢复，这个文件会消失，你得重新去申请一个 key。

**怎么做：**

```
1. 打开 C:\Users\user\Desktop\ai-workbench\.env
2. 把里面以 DEEPSEEK_API_KEY= 开头那一行的值抄下来
   （记事本、手机拍照、密码管理器都行）
3. 最好再单独复制一份 .env 到 U 盘
```

---

## 2. 【丢了就没了】数据目录 —— 先确认它在哪

这是**用户数据**：数据库、向量库、上传的文件、记忆、AI 产出物。

**它可能在两个地方，取决于你用的是哪一份。实测（2026-10-07）：**

| 位置 | 这台机器上 | 什么时候用这个 |
|---|---|---|
| `C:\Users\user\Desktop\ai-workbench\.runtime\` | **存在，482 MB** ← 你的数据在这里 | 开发目录（现在用的这份） |
| `%LOCALAPPDATA%\AI Workbench Data\` | **不存在** | 用安装包装出来的副本 |

**所以：这次要备份的是项目里的 `.runtime\`，不是 `%LOCALAPPDATA%` 那个。**
（`%LOCALAPPDATA%\AI Workbench Data\` 是安装版的默认位置，你现在没有装。）

**怎么自己确认：** 两个路径都看一眼，哪个存在、哪个有 `pgdata\` 子目录，数据就在那里。

```
1. 有真实资料 → 整个 .runtime\ 拷到移动硬盘
2. 只是测试数据 → 可以跳过，但要自己确认一遍
```

**顺带**：`%LOCALAPPDATA%\AI Workbench\` 如果存在（安装副本的程序文件），**不用备份**——
重新装一次就有。

---

## 3. 【建议备份】整个 `C:\Users\user\Desktop\ai-workbench\`

实测 **5,450,753,371 字节（约 5.1 GB）**。代码在 git 里，但这个文件夹里还有 git 管不到的东西：

| 会丢的 | 说明 |
|---|---|
| 未提交的改动 | `git status` 里那些 |
| `.env` | 见第 1 条 |
| `.runtime\` | 482 MB，开发用的数据库、日志、快照 —— **见第 2 条** |

**怎么做：** 整个文件夹拷到移动硬盘。**最省事，也最不容易漏。**
这一步做对了，第 1 条和第 2 条就都覆盖了。

---

## 4. 【交付物，建议留一份】

```
build\installer\AI-Workbench-Setup.exe              97,063,523 B
archive\releases\AI-Workbench-9edc203-final.zip      7,316,244 B
```

拷到 U 盘或网盘。**这两个不用重新构建也能拿到**，但重建要花时间，留一份省事。

---

## 5. 【可以不备份，能重新弄出来】

| 东西 | 体积 | 怎么弄回来 |
|---|---|---|
| `vendor\` 运行时 | 约 1.4 GB | `powershell -File scripts\fetch-runtime.ps1` |
| `.venv\` | 约 340 MB | 重建，**而且不能拷贝**（见下） |
| `frontend\node_modules\` | 约 220 MB | `cd frontend && npm install` |
| `build\` 构建产物 | 约 550 MB | `python scripts\build_installer.py --build` |

**`.venv` 为什么不能拷贝：** Windows 的 venv 把解释器的绝对路径写死在 `pyvenv.cfg` 里。
复制到别的路径、别的机器，它指向的解释器就不在了。这个项目已经因此踩过一次坑——
复制过去的 venv 当时"能用"，只是因为原解释器还留在同一台机器上。

---

## 重装之后

照 [`how-to-finish-verification.md`](how-to-finish-verification.md) 做。
里面有：拷哪两个文件、校验哈希、逐步安装、补验收要跑的命令、以及怎么重建开发环境。

**重装后第一件事：确认 Smart App Control 是【关】的。**
开了它会再拦一次；而它从开到关不可逆，关掉又要再重装一次。
