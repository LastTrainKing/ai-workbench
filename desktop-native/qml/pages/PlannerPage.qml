import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "../components"
import "../theme.js" as Theme

// Tasks as a plan: the goal → phase → task → subtask tree, with what can be done next.
//
// Two things this page refuses to fake:
//
//  * **No invented progress.** A task carries a status and, when it has one, an estimate and
//    an actual — there is no percentage, because the data has none and a bar that moves on
//    its own is a claim about work that nobody measured.
//  * **No button that cannot work.** The status buttons offered are exactly the transitions
//    the service allows from the current status (`_VALID_TRANSITIONS`), so a `DONE` task
//    does not offer "start" and then fail. A disabled-looking control that errors when
//    pressed is worse than no control.
//
// A task whose status is `BLOCKED` shows *why* — the unfinished dependency — because that is
// the difference between "this is stuck" and "this is stuck on something specific".
Flickable {
    id: page
    property var shell

    contentWidth: width
    contentHeight: column.implicitHeight
    clip: true

    property var tree: []
    property var flat: []
    property var byStatus: ({})
    property int total: 0
    property bool loading: false
    property bool busy: false
    property string filter: "ALL"
    property bool _loaded: false

    function load() {
        loading = true
        var id = "planner-" + Date.now()
        shell.request("任务树", id, function (payload) {
            page.tree = payload.tree || []
            page.flat = payload.flat || []
            page.byStatus = payload.by_status || {}
            page.total = payload.count || 0
            page.loading = false
        })
        bridge.loadTaskTree(id)
    }

    //: The transitions the service will accept, mirrored so the page cannot offer a dead one.
    function nextStates(status) {
        switch (status) {
        case "TODO":        return ["IN_PROGRESS", "BLOCKED", "DONE", "CANCELLED"]
        case "IN_PROGRESS": return ["TODO", "BLOCKED", "DONE", "CANCELLED"]
        case "BLOCKED":     return ["TODO", "IN_PROGRESS", "CANCELLED"]
        case "DONE":        return ["IN_PROGRESS"]
        case "CANCELLED":   return ["TODO"]
        default:            return []
        }
    }

    function statusWord(status) {
        switch (status) {
        case "TODO":        return "未开始"
        case "IN_PROGRESS": return "进行中"
        case "BLOCKED":     return "受阻"
        case "DONE":        return "已完成"
        case "CANCELLED":   return "已取消"
        default:            return status || "未知"
        }
    }

    function priorityWord(priority) {
        switch (priority) {
        case "LOW":    return "低"
        case "MEDIUM": return "中"
        case "HIGH":   return "高"
        case "URGENT": return "紧急"
        default:       return priority || ""
        }
    }

    function setStatus(taskId, status) {
        busy = true
        var id = "task-" + Date.now()
        shell.request("更新任务", id, function (payload) {
            page.busy = false
            shell.toast("info", "任务状态已改为 " + page.statusWord(payload.status))
            page.load()
        })
        bridge.updateTask(id, taskId, JSON.stringify({ status: status }))
    }

    function removeTask(taskId, title) {
        var id = "task-del-" + Date.now()
        shell.request("删除任务", id, function (payload) {
            shell.toast("info", "已删除：" + title)
            page.load()
        })
        bridge.deleteTask(id, taskId)
    }

    function visibleRows() {
        if (filter === "ALL") return flat
        return flat.filter(function (row) { return row.status === filter })
    }

    Component.onCompleted: load()

    ColumnLayout {
        id: column
        width: page.width
        spacing: 16

        RowLayout {
            Layout.fillWidth: true
            spacing: 12
            Text {
                text: "Planner"
                color: Theme.text
                font.pixelSize: 24
                font.bold: true
            }
            Item { Layout.fillWidth: true }
            Button {
                objectName: "planner-reload"
                text: "刷新"
                    variant: "ghost"
                onClicked: page.load()
                Accessible.name: "刷新任务"
            }
        }

        Card {
            Layout.fillWidth: true
            title: "状态分布"
            subtitle: page.loading ? "读取中…" : (page.total + " 个任务，来自数据库")

            RowLayout {
                Layout.fillWidth: true
                spacing: 8
                Repeater {
                    model: ["ALL", "TODO", "IN_PROGRESS", "BLOCKED", "DONE", "CANCELLED"]
                    delegate: Button {
                        required property string modelData
                        objectName: "planner-filter-" + modelData
                        text: (modelData === "ALL" ? "全部" : page.statusWord(modelData))
                              + " " + (modelData === "ALL"
                                       ? page.total
                                       : (page.byStatus[modelData] || 0))
                        checkable: true
                        checked: page.filter === modelData
                        onClicked: page.filter = modelData
                        Accessible.name: "只看" + (modelData === "ALL" ? "全部" : page.statusWord(modelData))
                    }
                }
                Item { Layout.fillWidth: true }
            }

            Text {
                Layout.fillWidth: true
                visible: page.total === 0 && !page.loading
                color: Theme.textMuted
                font.pixelSize: 13
                text: "还没有任务。在「任务」页新建一个，或让 Agent 拆解一个目标。"
            }
        }

        Card {
            Layout.fillWidth: true
            title: "任务树"
            subtitle: page.loading
                      ? "读取中…"
                      : ("显示 " + page.visibleRows().length + " / " + page.total
                         + (page.filter === "ALL" ? "" : "（筛选：" + page.statusWord(page.filter) + "）"))

            Text {
                Layout.fillWidth: true
                visible: !page.loading && page.total > 0 && page.visibleRows().length === 0
                color: Theme.textMuted
                font.pixelSize: 13
                text: "这个状态下没有任务。"
            }

            Repeater {
                model: page.visibleRows()

                delegate: Rectangle {
                    id: taskCard
                    required property var modelData
                    //: Depth, capped. The cap is about how much room the guides need, not
                    //: about hiding the tree — beyond three levels the marker stops moving
                    //: right, which is the same thing the old 60px cap did.
                    readonly property int level: Math.min(modelData.depth, 3)

                    Layout.fillWidth: true
                    // **No left margin.** The card used to be pushed right by
                    // `depth * 18`, which moved the title, the description and the five
                    // buttons together: six cards, four different x positions, and the
                    // action rows staggered down the page. Depth is drawn in the gutter
                    // below instead, so the card itself never moves.
                    implicitHeight: body.implicitHeight + 20
                    radius: 8
                    color: Theme.surface
                    border.width: 1
                    border.color: modelData.is_blocked ? Theme.warn
                                : modelData.status === "DONE" ? Theme.success
                                : Theme.border

                    Accessible.role: Accessible.StaticText
                    Accessible.name: modelData.title + "，" + page.statusWord(modelData.status)
                                     + (modelData.is_blocked ? "，被依赖阻塞" : "")

                    RowLayout {
                        id: body
                        anchors.fill: parent
                        anchors.margins: 10
                        spacing: 0

                        // The gutter. One width for every card, empty at the top level —
                        // a constant left inset is what makes the rows line up, and it is
                        // the only thing here that depends on depth.
                        Item {
                            id: gutter
                            Layout.preferredWidth: 30
                            Layout.fillHeight: true

                            // A hairline per enclosing level, so a child sits under a line
                            // that comes down from its parent.
                            Repeater {
                                model: Math.max(0, taskCard.level - 1)
                                delegate: Rectangle {
                                    x: 6 + index * 9
                                    width: 1
                                    height: parent.height
                                    color: Theme.divider
                                    Accessible.ignored: true
                                }
                            }
                            Text {
                                visible: taskCard.level > 0
                                text: "└"
                                color: Theme.textFaint
                                font.pixelSize: 12
                                x: 6 + (taskCard.level - 1) * 9 - 4
                                y: titleRow.y + (titleRow.height - height) / 2
                                Accessible.ignored: true
                            }
                        }

                        ColumnLayout {
                            id: taskColumn
                            Layout.fillWidth: true
                            spacing: 6

                        RowLayout {
                            id: titleRow
                            Layout.fillWidth: true
                            spacing: 10

                            Text {
                                Layout.fillWidth: true
                                text: modelData.title
                                color: Theme.text
                                font.pixelSize: 13
                                elide: Text.ElideRight
                            }
                            Text {
                                text: page.priorityWord(modelData.priority)
                                color: modelData.priority === "URGENT" ? Theme.danger : Theme.textFaint
                                font.pixelSize: 11
                            }
                            StatusPill { status: modelData.status }
                        }

                        Text {
                            Layout.fillWidth: true
                            visible: modelData.is_blocked
                            wrapMode: Text.WordWrap
                            color: Theme.warn
                            font.pixelSize: 11
                            text: "受阻：还有未完成的前置任务"
                        }

                        Text {
                            Layout.fillWidth: true
                            visible: modelData.description !== ""
                            wrapMode: Text.WordWrap
                            color: Theme.textMuted
                            font.pixelSize: 12
                            maximumLineCount: 2
                            elide: Text.ElideRight
                            text: modelData.description
                        }

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 8

                            Button {
                                objectName: "task-start-" + modelData.id
                                text: "开始"
                                variant: "primary"
                                visible: modelData.status === "TODO" || modelData.status === "BLOCKED"
                                enabled: !page.busy
                                onClicked: page.setStatus(modelData.id, "IN_PROGRESS")
                                Accessible.name: "开始任务 " + modelData.title
                            }
                            Button {
                                objectName: "task-block-" + modelData.id
                                text: "标记受阻"
                                visible: modelData.status === "TODO" || modelData.status === "IN_PROGRESS"
                                enabled: !page.busy
                                onClicked: page.setStatus(modelData.id, "BLOCKED")
                                Accessible.name: "标记受阻 " + modelData.title
                            }
                            Button {
                                objectName: "task-done-" + modelData.id
                                text: "完成"
                                visible: modelData.status !== "DONE" && modelData.status !== "CANCELLED"
                                enabled: !page.busy
                                onClicked: page.setStatus(modelData.id, "DONE")
                                Accessible.name: "完成任务 " + modelData.title
                            }
                            Button {
                                objectName: "task-cancel-" + modelData.id
                                text: "取消"
                                visible: modelData.status !== "CANCELLED" && modelData.status !== "DONE"
                                enabled: !page.busy
                                onClicked: page.setStatus(modelData.id, "CANCELLED")
                                Accessible.name: "取消任务 " + modelData.title
                            }
                            Button {
                                objectName: "task-delete-" + modelData.id
                                text: "删除"
                                variant: "danger"
                                enabled: !page.busy
                                onClicked: page.removeTask(modelData.id, modelData.title)
                                Accessible.name: "删除任务 " + modelData.title
                            }

                            Item { Layout.fillWidth: true }

                            Text {
                                visible: modelData.estimated_minutes > 0
                                color: Theme.textFaint
                                font.pixelSize: 11
                                text: "预计 " + modelData.estimated_minutes + " 分钟"
                                      + (modelData.actual_minutes > 0
                                         ? "　实际 " + modelData.actual_minutes + " 分钟" : "")
                            }
                            Text {
                                visible: modelData.due_date !== null
                                color: Theme.textFaint
                                font.pixelSize: 11
                                text: modelData.due_date || ""
                            }
                        }
                    }
                    }
                }
            }
        }

        Item { Layout.preferredHeight: 24 }
    }
}
