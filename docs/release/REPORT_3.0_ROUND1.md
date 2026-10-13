# AI Workbench 3.0 第一轮执行报告

任务书 §十六 要求的七节。日期：2026-10-14

---

## 1. 修改前问题

按 §二 的差距分析（`docs/product/3.0_GAP_ANALYSIS.md`）核对后的结论：**这个项目不缺能力，缺投影。**

最尖锐的一条是 §八 对应的：**员工合同的数据一直在往窗口送，界面没渲染。**

- `EmployeeOut`（`api/v1/employees.py:46`）从员工层建起来那天就在返回 `avatar`、
  `responsibilities`、`out_of_scope`、`service_scope`、`knowledge_scope`、`memory_scope`、
  `model_policy` 七个字段。
- `EmployeesPage.qml` 只渲染了其中 6 个：`name` / `mission` / `persona` / `role_id` /
  `summary` / `skills` / `tools` / `status`。
- 岗位模板另有 9 个字段（`inputs` / `sop` / `acceptance` / `needs_approval` / `prohibitions` /
  `definition_of_done` / `deliverable_kind` / `deliverable_location` / `name`），而**页面已经在
  加载这份目录了**（用于雇佣卡片），只是没在这里用。

任务书 §八 要的"岗位 / 使命 / 能力 / 工作方式 / 限制"，除使命外一个都没显示。

顺带发现三个同类问题（记录，未修）：

| 已有 | 位置 | 状态 |
|---|---|---|
| `memory_scope: {read: [global,user,project], write: [global]}` | 员工行 | 不显示（§十 直接相关） |
| `model_policy: {light: "fast", primary: "reasoning"}` | 员工行 | 不显示（员工级模型路由已存在） |
| `knowledge_scope: {knowledge_bases, projects}` | 员工行 | 不显示 |

---

## 2. 修改方案

**只改渲染，不动数据。**

- 取数规则放进 `theme.js`（`contractFor` / `contractField` / `contractLines` / `hasContract`
  + `contractSections`），因为员工页和合同组件要用同一套。两处各写一遍，迟早会在
  "员工自己的字段优先还是模板优先"这件事上给出两个答案。
- 渲染抽成 `components/EmployeeContract.qml`。页面里已经有一层 Repeater（遍历员工），合同
  还要一层（遍历分节），两个 `modelData` 同作用域是要靠翻找才能读懂的代码。
- **合同属于岗位，不属于员工。** 同一岗位的两个员工共享同一份 SOP 与验收标准；把模板复制进
  每一行，会让"改一次岗位定义"变成"改 N 个员工"。员工自己的字段有值时优先 —— 那正是两者
  都保留的意义。
- 默认收起。名册的用途是"挑一个员工"，不是"读它的合同"；每人 12 节会把名册撑成文档。
- SOP 带序号（它是步骤），其余用「·」（它们是并列条目）。
- `roleGlyph` 取代名字首字。首字把"小文"和"小武"画成同一个圈，而这个位置要回答的是
  "这是干什么的"。字形映射放主题里，不放模板：模板该带标识符，`avatar` 正是。

**并修掉一个被这次改动暴露的字体缺陷。** 首字换成 emoji 后渲染成豆腐块。根因不是 emoji，
是 `configure_font` 只设了**单一字体族**，而 Microsoft YaHei 没有 pictogram —— 与那个函数
本来要防的失败是同一个，只是换了一类字符。改为用 `QFont.setFamilies` 设回退链，并把 emoji
字体**从磁盘加载**（与 CJK 字体同样的理由：`offscreen` 平台报告零个字体族，按名字查什么都
查不到，截图会在有字体的机器上显示成方块）。

---

## 3. 修改文件

| 文件 | 改动 |
|---|---|
| `desktop-native/qml/pages/EmployeesPage.qml` | 角色图标取代首字；岗位名进元信息行；"查看合同 / 收起合同"开关；`contractOpen` 状态；页面级 helper 改为委托 `Theme.*` |
| `desktop-native/qml/components/EmployeeContract.qml` | **新增**。12 节合同渲染，内联 `Section` 组件 |
| `desktop-native/qml/theme.js` | `roleGlyph` / `roleName` / `contractFor` / `contractField` / `contractLines` / `hasContract` / `contractSections` |
| `desktop-native/main.py` | 字体回退链（`_apply_interface_font` / `_emoji_families` / `EMOJI_FALLBACKS` / `EMOJI_FONT_FILES`） |
| `docs/product/3.0_GAP_ANALYSIS.md` | **新增**。§二 要求的差距分析 |

**零后端改动、零新接口、零新请求、零数据库迁移。**

---

## 4. 新增能力

用户现在能看到（此前一个字都看不到）：

- **岗位** —— 通用助理 / 研究员 / 知识管理员 / 文档与演示专家 / 软件工程师 / 空白岗位
- **使命** —— 这个岗位为什么存在
- **职责** —— 它会做什么（清单）
- **工作方式（SOP）** —— 它按什么顺序做（带编号）
- **需要的输入** —— 开始之前它需要什么
- **不做什么** —— 明确的边界
- **先问你的事** —— 哪些动作它会停下来问（与审批机制对应）
- **限制** —— 硬性禁止项
- **验收标准** —— 它凭什么说做完了
- **完成定义** —— 完成是什么样子
- **产出格式 / 产出位置** —— 交付物长什么样、放在哪

以及：**全产品的 emoji / 图形符号现在能正确渲染**（字体回退，不只是修这一处）。

---

## 5. 测试结果

| 项 | 结果 |
|---|---|
| QML 页面加载 | **20/20** |
| shell 验收（导航 / 快捷键 / 可访问性） | **31/31** |
| 布局审计（19 页 × 3 档分辨率） | **0 处问题，0 QML 错误** |
| ruff（`backend/` + `desktop-native/`） | All checks passed |
| 真实渲染验证 | **点开合同抓图**，12 节全部渲染；SOP 编号正确；清单符号正确；同时只有 1 个员工展开 |

实测取到的合同数据（研究员岗位，真实数据库）：

```
mission:          桥梁测试使命
responsibilities: ['检索并筛选相关资料，记录来源与位置', '对相互矛盾的材料给出并列结论，而不是取一个', …]
out_of_scope:     ['不替用户判断材料的真实性与合法性', '不做没有来源支撑的推断']
service_scope:    把一堆资料读成一份有出处可查的结论。
memory_scope:     {'read': ['global','user','project'], 'write': ['global']}
model_policy:     {'light': 'fast', 'primary': 'reasoning'}
```

**回归测试的缺口（诚实记录）**：本轮没有为合同渲染新增自动化断言。现有覆盖是"页面能加载"
（`check_qml_pages.py`）与"shell 完好"（31 项），**它们不会在合同某节停止渲染时失败**。
按 §十四 的要求，应当补一条断言"展开合同后至少渲染 N 节"。见 §6。

---

## 6. 未解决风险

| 风险 | 说明 |
|---|---|
| **合同渲染无自动化断言** | 本轮验证靠抓图。抓图能证明今天是对的，不能防止明天坏掉。**这是最该补的一条。** |
| **`memory_scope` / `model_policy` / `knowledge_scope` 仍不可见** | 三项都是员工级配置，都已存在。`model_policy` 的值（`fast` / `reasoning`）是内部名，直接显示会让人困惑，需要先定中文说法 |
| **§六 TaskResult 未做** | 差距分析里的判断是"实现为读侧投影，不改 agent 输出"。判断已给出，代码未写 |
| **§七 成果中心** 缺预览 / 质量分 / 归档 | 上一轮已有 4 个操作（打开 / 文件夹 / 继续修改 / 重新生成） |
| **§九 按类型验收** 未做 | 验证层只有 `file_exists` + `readback` 两个通用检查 |
| **§十 记忆分类** 未做 | 7 类 `kind` 与 `scope` 都在库里，界面不显示 |
| **§五 首页仍不能选员工** | 要点"交给员工执行"跳到 Agent 页才选 |
| 字体回退未在真实桌面验证 | 截图环境是 `offscreen`，走的是磁盘加载那条路径；真实桌面走字体库那条路径，未实测 |
| 3.0 未重新构建安装包 | 本轮改动只在源码与开发栈验证 |

---

## 7. 当前 AI Workbench 3.0 完成度评价

任务书列了 6 个优先级（§五–§十）。本轮完成度：

| 优先级 | 状态 | 说明 |
|---|---|---|
| §八 员工岗位与人格展示 | **完成** | 12 节合同，来自数据不硬编码 |
| §五 首页重构 | 部分（上一轮） | 问候 + 输入 + 最近成果已有；缺"选员工"与三处状态文案统一 |
| §六 统一结果协议 | 未做 | 方案已定（读侧投影） |
| §七 成果中心 | 部分 | 4 个操作可用；缺预览 / 质量分 / 归档 |
| §九 按类型验收 | 未做 | 只有 2 个通用检查 |
| §十 记忆分类 | 未做 | 数据齐，界面缺 |

**完成度：约 1.5 / 6。**

### 一句话评价

这一轮的改动很小，但它证明了一件对后面五轮都重要的事：**这个项目的产品化瓶颈几乎全在投影层。**
员工合同 12 个字段、记忆 7 类分类、验证层、模型路由计划 —— 数据全在库里，API 也全在返回，
缺的只是"把它画出来"和"把技术名换成用户的话"。

这意味着后面五项**都不需要碰引擎、不需要迁移、不需要新接口**，风险低、可增量交付。也意味着
**验证方式要跟着变**：这类改动不会有测试变红，只会有界面变空。本轮已经吃过一次 —— 首页读错
字段名（`summary.llm` 而非 `summary.providers.llm`）不抛任何异常，只是对配好 Key 的用户说
"未配置 API Key"，是**抓图**看出来的，不是测试。

所以 §6 里那条"合同渲染无自动化断言"不是一句客套的风险提示，而是这一整轮工作方式的结构性
缺口：**投影层的正确性必须靠"看"来验证，而"看"目前是人做的。**
