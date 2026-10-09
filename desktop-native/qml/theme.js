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
