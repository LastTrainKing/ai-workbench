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
