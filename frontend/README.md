# AI Workbench — 前端

本地优先的 AI 知识与任务执行平台的单页应用。后端为 FastAPI（`http://127.0.0.1:8000`），
前端在本仓库的 `frontend/` 目录内，完整对接 `docs/api.md` 定义的接口契约。

- **技术栈**：React 18 + TypeScript + Vite 5、Tailwind CSS v3、`@tanstack/react-query`、
  `zustand`、`react-router-dom`、`react-markdown` + `remark-gfm`、`d3-force`、`lucide-react`。
- **无组件库**：所有按钮、表单、表格、抽屉、图表、知识图谱均为手写实现。
- **暗色优先**：默认暗色主题，可切换亮色并持久化到 `localStorage`。

---

## 快速开始

```bash
cd frontend
npm install                 # 本环境使用 npmmirror：npm install --registry=https://registry.npmmirror.com
cp .env.example .env        # 可选，默认值即可直接跑
npm run dev                 # http://127.0.0.1:5173
```

后端需要先启动在 `http://127.0.0.1:8000`。Vite 已配置 `/api` 代理，浏览器发出的请求不跨域。

登录页内置演示账号：**`demo` / `demo1234`**（页面提供一键填充）。

## 环境变量

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `VITE_API_BASE_URL` | `/api/v1` | 类型化 API 客户端（`src/lib/api.ts`）的基地址。开发态保持相对路径，由 Vite 代理转发到后端；仅当前后端不同源时才改成绝对地址。 |

## 可用脚本

| 命令 | 作用 |
| --- | --- |
| `npm run dev` | 启动开发服务器（`127.0.0.1:5173`，`strictPort`） |
| `npm run build` | `tsc --noEmit` 类型检查后产出 `dist/`（生产 sourcemap 关闭） |
| `npm run typecheck` | 仅类型检查，不产出文件 |
| `npm run lint` | ESLint（`.ts` / `.tsx`） |
| `npm run format` | Prettier 格式化 `src/` |
| `npm test` | Vitest 单元测试（单次） |
| `npm run test:watch` | Vitest 监听模式 |
| `npm run preview` | 预览已构建的 `dist/` |

## 目录结构

```
src/
├─ lib/           纯函数与契约层（可单测）
│  ├─ api.ts      类型化客户端：Bearer token、解包 {success,data}、抛出 ApiError、401 自动登出
│  ├─ types.ts    与 docs/api.md 一一对应的接口定义
│  ├─ utils.ts    cn / 时间 / 字节 / 数字格式化、状态色调与文案映射
│  ├─ citations.ts 答案解析、[n] 引用标记、关键词高亮
│  ├─ tree.ts     任务树构建/扁平化/统计/过滤
│  └─ palette.ts  命令面板模糊匹配与分组
├─ store/         zustand：auth（会话持久化）、ui（主题/侧栏/面板/弹层）、toast
├─ components/
│  ├─ ui/         Button、表单控件、反馈（骨架/空态/错误态/进度条）、弹层、基础元素
│  ├─ layout/     AppShell 三栏外壳、Sidebar、TopBar、RightPanel、导航表
│  ├─ Markdown.tsx       GFM 渲染 + 引用芯片点击定位
│  ├─ CommandPalette.tsx Ctrl/Cmd+K 全局命令面板
│  └─ ShortcutsOverlay.tsx `?` 快捷键说明
├─ features/      文档上传/切片抽屉/AI 分析、知识库选择、任务弹窗
├─ pages/         14 个页面（路由级懒加载）
├─ routes/        ProtectedRoute / PublicOnlyRoute
├─ test/          Vitest setup 与真实后端 E2E 脚本
├─ index.css      设计令牌（CSS 变量）、`@layer` 基础样式与 `.md-body`
└─ App.tsx        路由表
```

## 页面

| 路由 | 页面 |
| --- | --- |
| `/login` | 登录 / 注册 |
| `/` | 仪表盘（KPI、优先级任务、今日到期、文档、AI 运行、笔记、活动、Provider 状态） |
| `/projects`、`/projects/:id` | 项目列表与详情（统计 + 知识库/任务/笔记/运行分页） |
| `/knowledge`、`/knowledge/:kbId/documents` | 知识库管理与库内文档 |
| `/documents` | 文档总览（上传、筛选、切片抽屉、AI 分析、重处理/删除） |
| `/search` | 检索（keyword / semantic / hybrid，向量与关键词分项打分、命中高亮） |
| `/chat` | RAG 问答（多知识库、模式与 top_k、引用芯片、运行追踪） |
| `/planner` | 任务规划与学习计划（阶段 → 任务 → 子任务树，一键落为任务） |
| `/tasks` | 任务（列表 / 树视图、依赖与知识关联、完成冲突提示） |
| `/notes` | 笔记（Markdown 编辑 / 分栏 / 预览、置顶、AI 标记） |
| `/agent` | 智能体（执行表单、工具轨迹时间线、工具目录） |
| `/ai-runs`、`/ai-runs/:runId` | AI 运行记录与指标（手写 SVG 图表）、运行详情多标签 |
| `/graph` | 知识图谱（d3-force，拖拽平移缩放、点击跳转） |
| `/settings` | 设置（资料 / 系统 / 提示词版本管理 / 指标） |

## 架构要点

- **数据层**：所有请求经 `src/lib/api.ts`，统一解包 `{ success, data }` 信封；失败时抛出带
  `code` / `message` / `details` / `requestId` / `status` 的 `ApiError`。401 会清空会话并跳转
  `/login`。React Query 的 `retry` 依据 `ApiError.isRetryable` 判断。
- **轮询**：仅在当前页存在进行中的工作时（文档 `PENDING`/`PROCESSING`）才开启
  `refetchInterval`，空闲即停止。
- **反馈一致性**：每个列表/表格都有加载骨架、空状态与错误态（含 `code` 与 `request_id`）；
  破坏性操作统一走 `confirm()` 确认弹窗；API 错误统一经 toast 展示。
- **引用渲染**：`linkifyCitations()` 先把答案中的 `[n]` 改写为 `[n](#citation-n)`（跳过代码块），
  再由 react-markdown 的自定义 `a` 渲染器替换为可点击芯片，点击滚动到对应来源卡片。
- **主题系统**：`index.css` 中每个颜色以空格分隔的 RGB 通道声明（如 `--c-base: 11 13 16`），
  Tailwind 用 `rgb(var(--c-x) / <alpha-value>)` 暴露为语义色（`bg-surface`、`border-subtle`、
  `text-muted`、`accent`…），因此 `bg-danger/10`、`border-info/40` 等透明度修饰符可用。

## 测试与验证

单元测试（无需后端）：

```bash
npm test
```

真实后端端到端脚本（会登录、建项目、建知识库、上传样例、轮询到 COMPLETED、混合检索、
RAG 问答并断言引用非空，最后级联删除清理现场）：

```bash
VITE_API_BASE_URL=http://127.0.0.1:8000/api/v1 AI_WORKBENCH_E2E=1 \
  npx vitest run src/test/api-flow.e2e.test.ts
```

该脚本默认被跳过（需 `AI_WORKBENCH_E2E=1`），因为它依赖正在运行的后端。

## 已知状态

后端在无外部模型时可运行于离线降级模式：`/api/v1/health/deps` 会返回 `ai_degraded: true`
并附原因。前端不会隐藏这一点 —— 顶栏与仪表盘会显示降级警示徽标，检索/问答仍可正常工作
（使用内置的确定性启发式引擎）。
