# AI Workbench 1.1.0 — 交付包说明

这是一个**源码交付包**，用于在另一台 Windows 电脑上继续部署与开发。
它不是当前机器的运行环境快照：数据库数据、上传文件、向量库数据、虚拟环境、
依赖缓存与日志均不在包内。

---

## 一、包内包含

| 目录 / 文件 | 内容 |
|---|---|
| `backend/` | FastAPI 后端：`app/`（api · core · domain · models · repositories · services · ai · infrastructure · worker）、`alembic/` 迁移、`tests/` 测试、`requirements*.txt`、`Dockerfile`、`pyproject.toml` |
| `frontend/` | React 18 + TypeScript + Vite + Tailwind 前端源码、配置、测试、`Dockerfile`、`nginx.conf` |
| `worker/` | 独立 Worker 启动器与 Dockerfile |
| `scripts/` | 运行时下载、基础设施启停、开发进程管理、端到端验收、DeepSeek 验证、截图采集 |
| `docs/` | 架构 / 数据库 / API / AI / RAG / Agent / 部署 / 测试 / 安全 / 开发 文档，DeepSeek 配置说明，论文支撑包（10 章），简历描述，面试题 |
| `samples/` | 4 份示例文档（含 PDF）与 PDF 生成脚本 |
| `screenshots/` | 17 张真实运行截图 |
| 根目录 | `README.md`、`CHANGELOG.md`、`CONTRIBUTING.md`、`FINAL_CHECKLIST.md`、`PROJECT_DELIVERY.md`、`LICENSE`、`.env.example`、`.gitignore`、`.dockerignore`、`docker-compose.yml`、`start.bat` / `stop.bat` / `start.sh` / `stop.sh` |

**不含**（有意排除）：`node_modules/`、`.venv/`、`.runtime/`（数据库与上传文件）、
`vendor/runtime/`（约 350 MB 运行时二进制）、`dist/`、各类缓存、日志、
以及 **`.env`（其中含真实 API Key）**。

---

## 二、在新电脑上部署

### 1. 前置条件

- Windows 10/11 x64
- Python 3.11+（推荐 3.13）
- Node.js 18+（推荐 22）与 npm
- 约 2 GB 可用磁盘（含自动下载的运行时）

### 2. 解压并准备

```bat
:: 解压后进入目录
cd ai-workbench

:: Python 虚拟环境
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r backend\requirements-dev.txt

:: 前端依赖
cd frontend && npm install && cd ..
```

> 如果 `pip` 官方源不可用，追加 `-i https://mirrors.aliyun.com/pypi/simple`。
> 如果 `npm` 官方源不可用，追加 `--registry=https://registry.npmmirror.com`。

### 3. 配置密钥

```bat
copy .env.example .env
```

编辑 `.env`，至少填写：

```ini
SECRET_KEY=<随便一串长随机字符串>
DEEPSEEK_API_KEY=sk-你的密钥
```

完整说明见 `docs/deepseek-setup.md`。

### 4. 一键启动

双击桌面快捷方式，或运行：

```bat
scripts\start.bat
```

脚本会：检查依赖 → 首次自动下载 PostgreSQL / Redis / Qdrant 到 `vendor/`
→ 以项目级进程启动它们 → 启动 API 与前端 → 打开浏览器。

首次启动会下载约 350 MB 运行时，需要几分钟。**不需要** Docker，也**不会**修改系统设置。

### 5. 访问

| 服务 | 地址 |
|---|---|
| Web UI | <http://127.0.0.1:5173> |
| API 文档 | <http://127.0.0.1:8000/docs> |
| Qdrant 控制台 | <http://127.0.0.1:6333/dashboard> |

默认账号：**`demo` / `demo1234`**

### 6. 停止 / 重启

```bat
scripts\stop.bat              :: 停止全部（数据保留）
scripts\stop.bat --app-only   :: 只停应用，保留数据库
scripts\restart.bat           :: 只重启应用
scripts\restart.bat --full    :: 连同基础设施一起重启
```

数据库、向量库与上传文件在停止和重启过程中**不会被删除或清空**。

---

## 三、Docker 部署（可选，未在本机验证）

```bash
cp .env.example .env      # 设置 SECRET_KEY
docker compose up -d
```

`docker-compose.yml` 与三个 Dockerfile 均已提供并经过审阅，但开发机没有 Docker
且镜像仓库不可达，因此**未构建、未启动**。请在有 Docker 的机器上自行验证。
详见文件顶部的警告与 `docs/deployment.md`。

---

## 四、验证部署是否成功

```bat
:: 1. 依赖连通性
backend 目录下： ..\.venv\Scripts\python.exe -m app.cli check

:: 2. 端到端验收（75 项断言）
python scripts\smoke_test.py

:: 3. DeepSeek 真实链路验证（60 项检查，需要 Key）
.venv\Scripts\python.exe scripts\verify_deepseek.py

:: 4. 后端测试（331 项，含覆盖率）
cd backend && ..\.venv\Scripts\python.exe -m pytest tests --cov=app

:: 5. 纯单元测试（无需任何服务）
cd backend && ..\.venv\Scripts\python.exe -m pytest tests/unit

:: 6. 前端
cd frontend && npm run typecheck && npm run test && npm run build
```

---

## 五、关于 API Provider

默认使用 **DeepSeek V4.1 Flash**（`deepseek-flash`）。可在 **设置 → AI Provider**
中随时切换为 OpenAI 兼容端点、Ollama 本地模型或内置 Offline 引擎，切换后下一次
AI 调用即生效，无需重启。

未配置任何 Key 时系统不会崩溃：会自动降级到 Offline 引擎，并在界面、健康检查与
每条 AI 执行记录中如实标注，不会让人误以为是真实大模型在回答。

详见 `docs/deepseek-setup.md`。

---

## 六、已知限制

1. **Docker 路径未验证**（开发机无 Docker）
2. **Embedding 默认走离线向量化**（免费、确定性、384 维）；如需云端 embedding 需自行配置并重建向量集合
3. **无 OCR**：扫描版 PDF（无文本层）会以明确错误失败，不会产出空文档
4. **重排为词面重排**，未内置交叉编码器模型
5. **无流式输出**：AI 回答整体返回
6. **无速率限制 / 审计日志 / 上传病毒扫描**
7. **`start.sh` / `stop.sh` 未在 Linux 上实测**（验证路径是 Windows）

---

## 七、目录速查

```
ai-workbench/
├── backend/            FastAPI 后端 + 迁移 + 测试
├── frontend/           React 前端
├── worker/             独立 Worker
├── scripts/            运维与验证脚本
│   ├── start.bat       一键启动
│   ├── stop.bat        一键停止
│   ├── restart.bat     一键重启
│   ├── fetch-runtime.ps1   下载原生运行时
│   ├── infra-start.ps1     启动 PostgreSQL / Redis / Qdrant
│   ├── dev.ps1             应用进程管理
│   ├── smoke_test.py       端到端验收（75 项）
│   ├── verify_deepseek.py  DeepSeek 真实链路验证（60 项）
│   └── capture-screenshots.mjs  截图采集
├── docs/               全部文档
├── samples/            示例文档
├── screenshots/        运行截图
├── docker-compose.yml
├── start.bat / stop.bat / start.sh / stop.sh
├── .env.example        配置模板（无真实密钥）
└── README.md
```
