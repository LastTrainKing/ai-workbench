.pragma library

// The palette, in one place.
//
// A Steam-Big-Picture read: near-black blue ground, a single cyan accent, large cards,
// and a focus ring that is obvious from across a room. The values are here rather than
// sprinkled through the pages because a theme that lives in twenty files is twenty
// themes.

var bg          = "#05070d"
var bgElevated  = "#0b1220"
var bgCard      = "#101a2c"
var bgCardHover = "#16233a"
var border      = "#1e2d47"
var borderFocus = "#38bdf8"

var accent      = "#22d3ee"
var accentDim   = "#0e7490"
var text        = "#e6edf7"
var textMuted   = "#8ea3bf"
var textFaint   = "#5b708f"
var success     = "#34d399"
var warn        = "#fbbf24"
var danger      = "#f87171"

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

// Big Picture sizing: everything scales from one factor so a 4K display and a laptop
// panel get the same layout, not the same pixel counts.
function scale(base, factor) {
    return Math.round(base * factor)
}

// The numbered pages, in order. `Ctrl+1` is the first entry, `Ctrl+2` the second, and so
// on — so the rail's hint text and the actual shortcut cannot disagree about which key
// opens which page. They were two lists before, and a hint that lies is worse than no
// hint: it teaches the user a key that does something else.
var pages = [
    { key: "dashboard",    label: "工作台" },
    { key: "agent",        label: "Agent" },
    { key: "knowledge",    label: "知识库" },
    { key: "conversation", label: "对话" },
    { key: "permissions",  label: "权限" },
    { key: "search",       label: "搜索" },
    { key: "graph",        label: "图谱" },
    { key: "planner",      label: "Planner" },
    { key: "tasks",        label: "任务" },
    { key: "projects",     label: "项目" },
    { key: "notes",        label: "笔记" },
    { key: "runs",         label: "Agent" },
    { key: "settings",     label: "设置" }
]

//: How many pages have a keyboard shortcut. **Nine, and not `pages.length`.**
//:
//: The shell used to advertise "Ctrl+10" for the tenth page and create a `Shortcut` with
//: that sequence. Qt has `Key_1`..`Key_9` and no `Key_10`, so the sequence never matched
//: anything: four destinations carried a hint for a key that did nothing. The rail still
//: lists them, and arrowing through it reaches them.
var shortcutCount = 9

//: "Ctrl+3" for a page that has one, "" for a page that does not — either because it sits
//: past the ninth (documents and ask are reached from a knowledge base rather than from a
//: key, and the rail covers the rest).
function shortcutFor(key) {
    for (var i = 0; i < pages.length && i < shortcutCount; i++) {
        if (pages[i].key === key) return "Ctrl+" + (i + 1)
    }
    return ""
}
