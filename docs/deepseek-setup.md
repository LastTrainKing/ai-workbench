# DeepSeek 接入与 API Provider 配置说明

本文档说明如何配置 DeepSeek，以及如何在 **DeepSeek / OpenAI 兼容 / Ollama / Offline**
四种 Provider 之间切换。所有操作都不需要改代码。

---

## 一、默认配置（开箱即用）

| 项 | 值 |
|---|---|
| Provider | `deepseek` |
| Model | `deepseek-flash`（即 **DeepSeek V4.1 Flash**） |
| Base URL | `https://api.deepseek.com` |
| 备用模型 | `deepseek-v4-pro`（推理更强，成本更高） |

`deepseek-flash` 是**推理模型**：它会先输出思维链（`reasoning_content`），再输出正式回答。
因此 `AI_MAX_TOKENS` 默认设为 `8192`——预算太小会导致「思考用尽 token、没有最终回答」。
系统在这种情况下会返回明确的 `ai_reasoning_budget_exhausted` 错误，而不是给一个空答案。

---

## 二、配置 API Key（三种方式，任选其一）

Key 只会存在于本机，**不会**进入 Git、日志、截图或交付压缩包。

### 方式 A：`.env`（推荐用于首次部署）

```bash
cd ai-workbench
cp .env.example .env          # Windows: copy .env.example .env
```

编辑 `.env`：

```ini
AI_PROVIDER=deepseek
DEEPSEEK_API_KEY=sk-你的密钥
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-flash
AI_MAX_TOKENS=8192
```

`.env` 已在 `.gitignore` 中，不会被提交。

### 方式 B：界面配置（推荐用于日常切换）

1. 启动项目：双击桌面 `AI Workbench` 快捷方式，或运行 `scripts\start.bat`
2. 打开 <http://127.0.0.1:5173>，用 `demo` / `demo1234` 登录
3. 左侧进入 **设置**，默认就在 **AI Provider** 标签页
4. 在「API Key」输入框填入密钥 → 点击 **应用配置**
5. 点击 **测试连接**，应显示绿色「连接成功」并给出延迟与 token 消耗

界面上的修改会写入 `.runtime/ai-config.json`（同样被 git 忽略），并**在下一次 AI 调用立即生效**，无需重启。

### 方式 C：环境变量

```powershell
$env:DEEPSEEK_API_KEY = "sk-你的密钥"
```

---

## 三、验证配置是否生效

```bash
# 当前实际生效的 Provider 与模型
curl http://127.0.0.1:8000/api/v1/health/deps

# 发一次极小的真实请求验证连通性
curl -X POST http://127.0.0.1:8000/api/v1/ai-config/test \
  -H "Authorization: Bearer <token>"
```

或直接在 UI 的 **设置 → AI Provider** 页面查看顶部横幅：

- 绿色：`当前为 DeepSeek：deepseek-flash`
- 黄色：`当前为 Offline Provider（主动选择）`
- 红色：`当前由离线确定性引擎作答` —— 会同时说明降级原因

顶栏右上角也会常驻显示当前 Provider 与模型。

---

## 四、切换到其他 Provider

在 **设置 → AI Provider** 中选择对应卡片，填写参数后点「应用配置」即可。也可以直接调 API：

### Ollama（完全本地，数据不出本机）

```bash
ollama serve
ollama pull qwen2.5:7b
ollama pull nomic-embed-text
```

```bash
curl -X PATCH http://127.0.0.1:8000/api/v1/ai-config \
  -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -d '{"provider":"ollama","model":"qwen2.5:7b","base_url":"http://127.0.0.1:11434"}'
```

### OpenAI 兼容端点（OpenAI / 通义千问 / Moonshot / 智谱 / SiliconFlow / vLLM / LM Studio）

```bash
curl -X PATCH http://127.0.0.1:8000/api/v1/ai-config \
  -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -d '{"provider":"openai_compatible","base_url":"https://api.openai.com/v1",
       "model":"gpt-4o-mini","api_key":"sk-..."}'
```

### Offline（内置确定性引擎）

```bash
curl -X PATCH http://127.0.0.1:8000/api/v1/ai-config \
  -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -d '{"provider":"offline"}'
```

### 恢复 `.env` 配置

```bash
curl -X DELETE http://127.0.0.1:8000/api/v1/ai-config \
  -H "Authorization: Bearer <token>"
```

---

## 五、配置优先级与存放位置

```
界面设置 (.runtime/ai-config.json)   ← 最高
        ↓
环境变量 (.env)
        ↓
代码默认值
```

界面上每一行都会显示该值来自哪里：`runtime`（界面设置）／`env`（.env）／`default`（默认值）。

| 文件 | 是否入库 | 说明 |
|---|---|---|
| `.env` | 否（git-ignored） | 本机密钥，交付包中不含 |
| `.env.example` | 是 | 只有占位符，无真实密钥 |
| `.runtime/ai-config.json` | 否（git-ignored） | 界面修改的运行时覆盖 |

---

## 六、安全说明

- API Key **只写不读**：任何接口都不会返回明文，只返回 `api_key_set` 与掩码 `sk-****(len=35)`
- 日志中的密钥会被脱敏
- 已通过自动化测试扫描全仓库，确保没有真实密钥出现在源码、测试、文档、脚本或截图中
- 未配置 Key 时不会崩溃：自动降级到 Offline 引擎，并在健康检查、界面和每条 AI 执行记录中如实标注 `degraded`

---

## 七、常见问题

**Q: 提示 `ai_insufficient_balance`？**
DeepSeek 账户余额不足，充值后重试，或在界面切到 Offline。

**Q: 提示 `ai_auth_error`？**
Key 无效或已过期。检查 `.env` 或界面中的 Key，注意不要有多余空格。

**Q: 提示 `ai_reasoning_budget_exhausted`？**
`deepseek-flash` 是推理模型，思考过程会消耗 token。把 `AI_MAX_TOKENS` 调大（默认 8192）。

**Q: 提示 `ai_model_not_found`？**
模型名不对。可用值为 `deepseek-flash` 与 `deepseek-v4-pro`，可在界面上「测试连接」旁的
「模型」下拉中查看，或调用 `GET /api/v1/ai-config/models?provider=deepseek&live=true`。

**Q: 返回 502 且提示 HTTP 400？**
错误信息里现在会带上 DeepSeek 的原始说明，按提示处理即可。

**Q: 想让向量化也走云端？**
Embedding 默认走离线哈希向量化（免费、确定性）。如需云端 embedding：

```ini
EMBEDDING_PROVIDER=openai_compatible
EMBEDDING_BASE_URL=https://api.openai.com/v1
EMBEDDING_API_KEY=sk-...
EMBEDDING_MODEL=text-embedding-3-small
```

注意：更换 embedding 模型会改变向量维度，需要重建 Qdrant 集合，详见
`docs/deployment.md` 的 "Qdrant dimension mismatch"。
