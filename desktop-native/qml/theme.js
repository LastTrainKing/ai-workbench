.pragma library

// The design system, in one place.
//
// A dark, low-saturation blue-black ground with a single cyan accent reserved for primary
// actions, the current selection, progress and live state. Structure comes from spacing,
// surface elevation and type hierarchy — **not** from a border around every box, which is
// what the previous pass did and what made every screen read as a web dashboard.
//
// The values live here rather than in twenty pages because a theme spread across twenty
// files is twenty themes.

// ---------------------------------------------------------------- surfaces --
//
// **The rule that decides the whole look.** The page is one continuous dark ground.
// Content sits directly on it. A fill is used only where something is genuinely a
// separate surface — an input you can type in, a block you can act on, a row under the
// pointer, a panel that has to be read apart from its neighbours.
//
// What this replaced: every section of every page was its own blue-black rectangle with a
// 1px outline, and those rectangles nested. Twenty boxes on a screen is a management
// console; the difference between `bg` and `surface` is now small enough that a section
// does not need a fill to be legible, and large enough that a real surface still reads as
// one.
var bg          = "#080b12"   // the window ground — the only background most pages need
var bgElevated  = "#0a0e17"   // the rail: a shade, not a border
var surface     = "#101823"   // a raised block (hero, expanded detail)
var surfaceHover= "#151f2c"
var bgCard      = "#101823"   // alias kept for the pages that predate `surface`
var bgCardHover = "#151f2c"
var bgInput     = "#0c1119"   // fields, recessed *below* the ground
var border      = "#1b2432"   // hairlines only
var divider     = "#151d29"   // separators, one step above the ground
var borderFocus = "#38bdf8"

// ------------------------------------------------------------------ brand --
var accent      = "#22d3ee"
var accentDim   = "#0e7490"
//: The accent at low alpha, for a selected row's fill. `#AARRGGBB`.
var accentSoft  = "#1f22d3ee"

// ------------------------------------------------------------------- text --
var text        = "#e8eef8"
var textMuted   = "#93a4bd"
var textFaint   = "#63748d"

// ------------------------------------------------------------------ state --
var success     = "#34d399"
var warn        = "#fbbf24"
var danger      = "#f87171"
var info        = "#60a5fa"

// --------------------------------------------------------------- spacing --
// One scale, so two panels that should line up do.
var space1 = 4
var space2 = 8
var space3 = 12
var space4 = 16
var space5 = 24
var space6 = 32
var space7 = 48

// ----------------------------------------------------------------- radius --
var radiusSm = 6
var radiusMd = 10
var radiusLg = 14

// ------------------------------------------------------------------- type --
// Sizes, not scale factors: `Theme.scale` handles DPI, this handles hierarchy.
var fontDisplay = 26
var fontTitle   = 18
var fontHeading = 15
var fontBody    = 13
var fontSmall   = 12
var fontTiny    = 11

// ----------------------------------------------------------------- control --
var controlHeight = 34
var controlHeightLarge = 44

// -------------------------------------------------------------------- form --
//: The label column, fixed. Content-sized columns make the gap between a label and its
//: field depend on which label happens to be longest — on one form that was 43px for
//: 模型 and 67px for Base URL, which is what "looks like a form somebody assembled by
//: hand" means in pixels.
var formLabelWidth = 96
var formGap = 12

// A run's tone. Kept next to the colours so the two cannot drift: a status that is
// "warn" in the API and green in the UI is the kind of mismatch this file exists to stop.
function runTone(status) {
    switch (status) {
    case "SUCCEEDED":   return success
    case "COMPLETED":   return success
    case "RUNNING":     return accent
    case "INCOMPLETE":  return warn
    case "INTERRUPTED": return warn
    case "TIMEOUT":     return warn
    case "CANCELLED":   return textMuted
    case "FAILED":      return danger
    default:            return textMuted
    }
}

function taskTone(status) {
    switch (status) {
    case "DONE":        return success
    case "IN_PROGRESS": return accent
    case "BLOCKED":     return danger
    case "CANCELLED":   return textMuted
    default:            return textMuted
    }
}

// A person's words for a run status. The API's vocabulary is for the API.
function runWord(status) {
    switch (status) {
    case "SUCCEEDED":   return "已完成"
    case "COMPLETED":   return "已完成"
    case "RUNNING":     return "执行中"
    case "INCOMPLETE":  return "未完成"
    case "INTERRUPTED": return "已中断"
    case "TIMEOUT":     return "超时"
    case "CANCELLED":   return "已取消"
    case "FAILED":      return "失败"
    default:            return status
    }
}

// A person's words for a task status, for the same reason as `runWord`.
function taskWord(status) {
    switch (status) {
    case "TODO":        return "待办"
    case "IN_PROGRESS": return "进行中"
    case "DONE":        return "已完成"
    case "BLOCKED":     return "受阻"
    case "CANCELLED":   return "已取消"
    default:            return status
    }
}

// Big Picture sizing: everything scales from one factor so a 4K display and a laptop
// panel get the same layout, not the same pixel counts.
function scale(base, factor) {
    return Math.round(base * factor)
}



//: Permission levels, in the window's language. The backend's `PermissionLevel.label()`
//: returns "Observe only" — correct for an API response and wrong on a Chinese screen.
//: Kept as a map from the same integers so the two cannot disagree about the ordering.
function permissionLevelLabel(level) {
    switch (Number(level)) {
    case 0:  return "只看不改"
    case 1:  return "工作区内"
    case 2:  return "系统级"
    case 3:  return "完全控制"
    default: return String(level)
    }
}

//: Autonomy mode, same reasoning.
function agentModeLabel(mode) {
    switch (mode) {
    case "assist":     return "只建议"
    case "supervised": return "受监督"
    case "autonomous": return "自主"
    default:           return mode || ""
    }
}

//: A role's mark, by `role_id`.
//:
//: The wire value is an identifier (`assistant`, `researcher`), because that is what a
//: template should carry — a glyph is a display decision and belongs in the theme. Task
//: book §八 asks for the roster to read at a glance (📚 学术助手 / 💻 软件工程师), and a
//: glyph does that in one character where a label needs a line.
function roleGlyph(roleId) {
    switch (roleId) {
    case "assistant":         return "🧭"
    case "researcher":        return "🔍"
    case "knowledge_manager": return "📚"
    case "document_expert":   return "📝"
    case "software_engineer": return "💻"
    case "blank":             return "⬜"
    default:                  return "🧩"
    }
}

//: The role's display name, falling back to the identifier rather than to nothing.
//: An unknown role is a real case — a template can be removed in a later version — and
//: showing the raw id is more useful than showing an empty string.
function roleName(contract, roleId) {
    if (contract && contract.name) return contract.name
    return roleId || "未命名岗位"
}

// ------------------------------------------------------------- 岗位合同（§八）--
//
// 这些取值函数放在 theme.js 而不是页面里，因为员工页和合同组件都要用同一套规则。
// 两处各写一遍，迟早会在"员工自己的字段优先还是模板优先"这件事上给出两个答案。
//
// 两处来源，职责分明，谁也不硬编码：
//   * 员工行（`employee.*`）—— 这个员工自己的；用户改过就以它为准
//   * 岗位模板（`templates`）—— 这个岗位的合同，按 `role_id` 查
//
// 合同是模板的属性，不是员工的属性：同一岗位的两个员工共享同一份 SOP 与验收标准。
// 把模板复制进每一行，会让"改一次岗位定义"变成"改 N 个员工"。

//: 这一岗位的合同，找不到返回 null。
function contractFor(employee, templates) {
    if (!employee || !templates) return null
    for (var i = 0; i < templates.length; i++) {
        if (templates[i].role_id === employee.role_id) return templates[i]
    }
    return null
}

//: 员工自己的字段优先，模板兜底。返回 undefined 表示这一项没有内容 —— 调用方据此
//: 决定是否画出这一节，而不是画一个空标题。
function contractField(employee, templates, key) {
    var own = employee ? employee[key] : undefined
    var empty = own === undefined || own === null || own === "" ||
                (Array.isArray(own) && own.length === 0)
    if (!empty) return own
    var t = contractFor(employee, templates)
    return t ? t[key] : undefined
}

//: 把任意值转成"要渲染的若干行"。**这是本文件里唯一一处做形状判断的地方。**
//:
//: 两个坑，都在真实运行里出现过：
//:
//: 1. `Array.isArray` 对**类数组**返回 false。QML 的 `property var` 在传递 QVariantList
//:    时给出的对象有 `length` 和索引，却不被 `Array.isArray` 认。原来那句
//:    `Array.isArray(v) ? v : [String(v)]` 于是把它当成标量 —— 要么渲染成
//:    `[object Object]`，要么（在加了字典分支之后）直接返回空，**整节消失**。
//: 2. 字典必须返回空。`String({a:1})` 是 `"[object Object]"` —— 一句凭空造出来的话。
//:
//: 判据用"有没有 `length`"而不是 `Array.isArray`：它同时覆盖原生数组与类数组，
//: 而且字典没有 `length`，天然被排除。
function toLines(v) {
    if (v === undefined || v === null || v === "") return []
    if (typeof v === "string") return [v]
    if (typeof v === "number" || typeof v === "boolean") return [String(v)]
    if (typeof v.length === "number") {
        var out = []
        for (var i = 0; i < v.length; i++) {
            var item = v[i]
            if (item === undefined || item === null) continue
            out.push(String(item))
        }
        return out
    }
    return []
}

//: 一份清单，渲染成一行一行。**不用顿号连成一句**：SOP 是有顺序的步骤，职责是可数的
//: 条目，连成一行会让"第 3 步"这类信息消失。
function contractLines(employee, templates, key) {
    return toLines(contractField(employee, templates, key))
}

function hasContract(employee, templates, key) {
    return contractLines(employee, templates, key).length > 0
}

//: 合同的分节，顺序就是任务书 §八 的顺序：岗位、使命、能力、工作方式、限制、验收。
//: `ordered` 的那一节带序号 —— SOP 是步骤，编号是有意义的；其余是并列条目。
var contractSections = [
    { title: "岗位",       key: "name",                 single: true },
    { title: "使命",       key: "mission",              single: true },
    { title: "职责",       key: "responsibilities" },
    { title: "工作方式",   key: "sop",                  ordered: true },
    { title: "需要的输入", key: "inputs" },
    { title: "不做什么",   key: "out_of_scope" },
    { title: "先问你的事", key: "needs_approval" },
    { title: "限制",       key: "prohibitions" },
    { title: "验收标准",   key: "acceptance" },
    { title: "完成定义",   key: "definition_of_done",   single: true },
    { title: "产出格式",   key: "deliverable_kind",     single: true },
    { title: "产出位置",   key: "deliverable_location", single: true }
]

// ------------------------------------------------------- 岗位声明（尚未生效）--
//
// 这三个字段**只在模板里声明、被 API 返回，运行时从不读取**。这不是推测，是查过的：
//
//   * `employee.model_policy` —— 全仓库没有一处读它；`"reasoning"` / `"fast"` 只出现在
//     注释里，从没被当作路由键解析过。模型仍然是全局的那一个。
//   * `employee.memory_scope` —— 没有任何代码按它过滤记忆。而且它声明的
//     `["global","user","project"]` 里，`user` **不是** `MemoryEntry.scope` 的合法值
//     （`ai/memory/evaluator.py:321` 只产出 `global` 与 `project`）。
//   * `employee.knowledge_scope` —— 六个模板里都是 `{"knowledge_bases": [], "projects": []}`，
//     即全部为空。
//
// 所以它们**不能**被当成边界来展示。"这个员工只能写全局记忆"是一句假话，而它是
// 安全方向上的假话 —— 用户会据此认为存在一道并不存在的墙。
//
// 展示它们是对的（字段确实存在，藏起来是另一种不诚实），但必须标成声明，
// 并明说它还没接进执行。这一节的措辞因此比别处硬。

//: 记忆范围的值 → 中文。`user` 与 `task` 当前不在记忆系统的词表里，但**照实翻译**：
//: 把它们显示成"未知"会掩盖"声明与实现对不上"这个事实本身。
function memoryScopeLabel(scope) {
    switch (scope) {
    case "global":  return "全局记忆"
    case "user":    return "用户偏好（记忆系统当前不使用这个词）"
    case "project": return "项目记忆"
    case "task":    return "当前任务（记忆系统当前不使用这个词）"
    case "session": return "本次会话（记忆系统当前不使用这个词）"
    default:        return "未知范围（" + scope + "）"
    }
}

//: 模型路由的值 → 中文。这些是**策略名**，不是模型名。
function modelRouteLabel(route) {
    switch (route) {
    case "main":      return "主力模型"
    case "reasoning": return "推理模型"
    case "fast":      return "快速模型"
    case "light":     return "轻量模型"
    default:          return "未知路由（" + route + "）"
    }
}

//: `{read: [...], write: [...]}` → 两行中文。**不写成权限口吻**：它现在不是权限。
//:
//: 用 `toLines` 而不是 `Array.isArray` —— 同一个类数组的坑，这里也会踩。
function memoryScopeLines(scope) {
    if (!scope || typeof scope !== "object") return []
    var out = []
    if (scope.read !== undefined) {
        var read = toLines(scope.read)
        out.push("声明可读：" + (read.length ? read.map(memoryScopeLabel).join("、") : "无"))
    }
    if (scope.write !== undefined) {
        var write = toLines(scope.write)
        out.push("声明可写：" + (write.length ? write.map(memoryScopeLabel).join("、") : "无"))
    }
    return out
}

//: `{primary: "main", light: "fast"}` → 两行中文。
function modelPolicyLines(policy) {
    if (!policy || typeof policy !== "object") return []
    var out = []
    if (policy.primary) out.push("复杂任务声明使用：" + modelRouteLabel(policy.primary))
    if (policy.light) out.push("轻量任务声明使用：" + modelRouteLabel(policy.light))
    return out
}

//: `{knowledge_bases: [], projects: []}` → 一行或两行。空数组是"未限定"，不是"没有"。
function knowledgeScopeLines(scope) {
    if (!scope || typeof scope !== "object") return []
    var out = []
    if (scope.knowledge_bases !== undefined) {
        var bases = toLines(scope.knowledge_bases)
        out.push("知识库：" + (bases.length ? bases.join("、") : "未限定（使用运行时提供的全部）"))
    }
    if (scope.projects !== undefined) {
        var projects = toLines(scope.projects)
        out.push("项目：" + (projects.length ? projects.join("、") : "未限定"))
    }
    return out
}

//: 取某一节的渲染行。**按 `render` 分派，不按 key 猜** —— 字典字段走 `contractLines`
//: 会得到 `["[object Object]"]`，那是一句凭空造出来的话。
function declaredLines(employee, templates, key, render) {
    var value = contractField(employee, templates, key)
    switch (render) {
    case "memory":    return memoryScopeLines(value)
    case "model":     return modelPolicyLines(value)
    case "knowledge": return knowledgeScopeLines(value)
    default:          return []
    }
}

//: 岗位声明三节。与 `contractSections` 分开，因为它们的形状不同（字典 vs 清单），
//: 而且要带上"尚未生效"的标记。
var declaredSections = [
    { title: "记忆范围", key: "memory_scope",    render: "memory" },
    { title: "知识范围", key: "knowledge_scope", render: "knowledge" },
    { title: "模型策略", key: "model_policy",    render: "model" }
]

//: 这一节的内容来自岗位声明，但**尚未接入执行**。组件据此显示那条警告。
var declaredNotice =
    "以上是岗位声明，当前尚未接入执行：模型仍是全局配置，记忆也不会按此过滤。"

//: How an employee's status reads on screen. The wire values are lower-case machine
//: words; these are what a person is shown. Kept here rather than in the page so the
//: roster and the detail view cannot disagree.
function employeeStatusLabel(status) {
    switch (status) {
    case "idle":             return "空闲"
    case "working":          return "工作中"
    case "waiting_approval": return "等待授权"
    case "waiting_user":     return "等你回答"
    case "paused":           return "已暂停"
    case "failed":           return "上次失败"
    case "archived":         return "已归档"
    default:                 return status || "未知"
    }
}

//: Statuses that mean "this employee can take work right now".
function employeeIsActive(status) {
    return status === "idle" || status === "working" || status === "waiting_approval"
}

//: The accent for a status. Only "needs you" is loud.
function employeeStatusTone(status) {
    switch (status) {
    case "working":          return accent
    case "waiting_approval":
    case "waiting_user":     return warn
    case "failed":           return danger
    case "archived":         return textFaint
    default:                 return textMuted
    }
}

// ------------------------------------------------------- navigation model --
//
// **The one list.** The rail renders these groups, and `pages` below is this same list
// flattened — so the order the arrow keys move through, the order `Ctrl+N` numbers, and
// the hint printed next to each entry cannot disagree. They were two hand-written lists
// before, and the rail carried an entry (`documents`) that no shortcut could ever reach.
//
// The groups are the things a person actually does: work on something, ask the AI, look
// something up, plan ahead, and configure.
var navGroups = [
    { title: "工作区", entries: [
        { key: "dashboard",    label: "工作台",   icon: "home" },
        { key: "projects",     label: "项目",     icon: "folder" },
        { key: "tasks",        label: "任务",     icon: "check" },
        { key: "documents",    label: "文档",     icon: "page" },
        { key: "notes",        label: "笔记",     icon: "note" }
    ] },
    // **The group this release is about.** Everything here was reachable through the API
    // and nowhere in the window: an agent could produce a file, verify it, register it and
    // version it, and there was no screen that showed any of it.
    { title: "员工", entries: [
        { key: "employees",    label: "我的员工", icon: "person" },
        { key: "deliverables", label: "交付物",   icon: "box" }
    ] },
    { title: "AI 工作", entries: [
        { key: "conversation", label: "对话",     icon: "chat" },
        { key: "agent",        label: "Agent",    icon: "spark" },
        { key: "runs",         label: "运行记录", icon: "history" },
        // Last, and not numbered: it is empty most of the time, and an approval is
        // something the user goes looking for rather than browses.
        { key: "approvals",    label: "待审批",   icon: "alert" }
    ] },
    { title: "知识", entries: [
        { key: "knowledge",    label: "知识库",   icon: "book" },
        { key: "ask",          label: "问答",     icon: "question" },
        { key: "search",       label: "搜索",     icon: "search" },
        { key: "graph",        label: "图谱",     icon: "graph" },
        { key: "memory",       label: "记忆",     icon: "memory" }
    ] },
    { title: "计划", entries: [
        { key: "planner",      label: "Planner",  icon: "calendar" }
    ] },
    { title: "系统", entries: [
        { key: "permissions",  label: "权限",     icon: "shield" },
        { key: "settings",     label: "设置",     icon: "gear" }
    ] }
]

//: The rail's order, flattened from `navGroups`. Built here rather than written again.
var pages = (function () {
    var flat = []
    for (var g = 0; g < navGroups.length; g++) {
        for (var e = 0; e < navGroups[g].entries.length; e++) {
            flat.push(navGroups[g].entries[e])
        }
    }
    return flat
})()

//: How many pages have a keyboard shortcut. **Nine, and not `pages.length`.**
//:
//: Qt has `Key_1`..`Key_9` and no `Key_10`, so a `Shortcut` past the ninth can never
//: fire. Four destinations used to carry a hint for a key that did nothing.
var shortcutCount = 9

//: "Ctrl+3" for a page that has one, "" for a page that does not.
function shortcutFor(key) {
    for (var i = 0; i < pages.length && i < shortcutCount; i++) {
        if (pages[i].key === key) return "Ctrl+" + (i + 1)
    }
    return ""
}
