import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "../components"
import "../theme.js" as Theme

// Say what you want done, watch it happen, read what came out.
//
// The three regions are the three things a person asks about a run: what it decided to do
// (plan), what it actually did (steps and tool calls), and what they got (result). All of it
// comes from the executor that already exists — `AgentExecutor` writes the run, the plan and
// every invocation to `ai_runs` / `agent_runs` / `tool_calls`, and this page reads them back
// through `loadRunDetail`. There is no second execution path here, and there is deliberately
// no way to re-run a step from this screen: a tool call that changed something cannot be
// safely repeated, and a button that offers to would be a lie about that.
Flickable {
    id: page
    property var shell

    contentWidth: width
    contentHeight: column.implicitHeight
    clip: true

    property string goal: ""
    property bool running: false
    property string progressText: ""
    property var outcome: null
    property var detail: null
    property string errorText: ""

    readonly property var plan: detail ? (detail.plan ?? []) : []
    readonly property var calls: detail ? (detail.tool_calls ?? []) : []
    readonly property var unresolved: detail ? (detail.unresolved ?? []) : []

    // Files the run actually wrote, taken from the tool calls' own arguments. Only calls that
    // succeeded, because a failed write has no file at the end of it.
    readonly property var artifacts: {
        var seen = {}
        var out = []
        if (!detail) return out
        for (var i = 0; i < calls.length; i++) {
            var call = calls[i]
            if (call.status !== "SUCCEEDED") continue
            var args = call.arguments || {}
            var path = args.path || args.directory || ""
            if (!path || seen[path]) continue
            seen[path] = true
            out.push({ path: path, tool: call.tool_name })
        }
        return out
    }

    // A goal handed over by another page (a project saying "continue this").
    onGoalChanged: if (goal !== goalArea.text) goalArea.text = goal

    function start() {
        var text = goal.trim()
        if (text === "") {
            shell.toast("warn", "先写下你想完成什么")
            goalArea.forceActiveFocus()
            return
        }
        if (running) return
        running = true
        progressText = "正在启动…"
        outcome = null
        detail = null
        errorText = ""
        var id = "agent-run-" + Date.now()
        shell.request("Agent 运行", id, function (payload) {
            page.outcome = payload
            page.running = false
            page.progressText = ""
            if (!payload) {
                page.errorText = "运行没有返回结果，请查看运行记录。"
                return
            }
            shell.toast(payload.status === "SUCCEEDED" ? "info" : "warn",
                        "结束：" + payload.status + "（" + payload.stopped_reason + "）")
            page.loadDetail(payload.run_id)
        })
        bridge.startAgentRun(id, text)
    }

    function loadDetail(runId) {
        if (!runId) return
        var id = "agent-detail-" + Date.now()
        shell.request("运行详情", id, function (payload) { page.detail = payload })
        bridge.loadRunDetail(id, runId)
    }

    function cancel() {
        bridge.cancelAgentRun()
        progressText = "已请求取消；当前步骤结束后停止"
    }

    function statusWord(status) {
        switch (status) {
        case "SUCCEEDED":  return "已完成"
        case "FAILED":     return "失败"
        case "CANCELLED":  return "已取消"
        case "INCOMPLETE": return "未完成"
        case "EXECUTING":  return "执行中"
        case "PENDING":    return "等待中"
        default:           return status || "未知"
        }
    }

    function statusColor(status) {
        switch (status) {
        case "SUCCEEDED":  return Theme.success
        case "FAILED":     return Theme.danger
        case "UNKNOWN":    return Theme.warn
        case "CANCELLED":
        case "INCOMPLETE": return Theme.warn
        default:           return Theme.textMuted
        }
    }

    Connections {
        target: bridge
        function onProgress(id, text) {
            if (page.running) page.progressText = text
        }
        function onFailed(id, kind, message) {
            if (!page.running) return
            page.running = false
            page.progressText = ""
            page.errorText = kind + "：" + message
        }
    }

    ColumnLayout {
        id: column
        width: page.width
        spacing: 16

        RowLayout {
            Layout.fillWidth: true
            Text {
                text: "Agent 工作台"
                color: Theme.text
                font.pixelSize: 24
                font.bold: true
                Layout.fillWidth: true
            }
            Text {
                visible: page.detail !== null
                text: page.detail ? ("运行 " + page.detail.run.run_id.slice(0, 8)) : ""
                color: Theme.textFaint
                font.pixelSize: 12
            }
        }

        Card {
            Layout.fillWidth: true
            title: "要我做什么"
            subtitle: "说清目标和结果，它会自己决定步骤"

            ScrollView {
                Layout.fillWidth: true
                Layout.preferredHeight: 92
                TextArea {
                    id: goalArea
                    objectName: "agent-goal"
                    placeholderText: "例如：把这个项目里的测试跑一遍，把失败的整理成一份报告"
                    wrapMode: TextArea.Wrap
                    color: Theme.text
                    font.pixelSize: 14
                    background: Rectangle {
                        color: Theme.bg
                        radius: 8
                        border.width: 1
                        border.color: goalArea.activeFocus ? Theme.borderFocus : Theme.border
                    }
                    onTextChanged: page.goal = text
                }
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 10
                Button {
                    objectName: "agent-start"
                    text: page.running ? "执行中…" : "开始执行"
                    enabled: !page.running
                    onClicked: page.start()
                }
                Button {
                    text: "取消"
                    visible: page.running
                    onClicked: page.cancel()
                }
                Text {
                    Layout.fillWidth: true
                    text: page.progressText
                    color: page.running ? Theme.accent : Theme.textFaint
                    font.pixelSize: 12
                    elide: Text.ElideRight
                }
            }

            Text {
                Layout.fillWidth: true
                visible: page.errorText !== ""
                text: page.errorText
                color: Theme.danger
                font.pixelSize: 13
                wrapMode: Text.WordWrap
            }
        }

        // ------------------------------------------------------------------ 运行状态 --
        Card {
            Layout.fillWidth: true
            visible: page.outcome !== null
            title: "运行状态"

            RowLayout {
                Layout.fillWidth: true
                spacing: 10
                Rectangle {
                    width: 10; height: 10; radius: 5
                    color: page.outcome ? page.statusColor(page.outcome.status) : Theme.textMuted
                }
                Text {
                    text: page.outcome ? page.statusWord(page.outcome.status) : ""
                    color: Theme.text
                    font.pixelSize: 15
                    font.bold: true
                }
                Text {
                    Layout.fillWidth: true
                    text: page.outcome
                          ? ("结束原因：" + page.outcome.stopped_reason
                             + "　用了 " + page.outcome.steps + " 步")
                          : ""
                    color: Theme.textMuted
                    font.pixelSize: 12
                }
            }

            Text {
                Layout.fillWidth: true
                visible: page.outcome !== null && page.outcome.status === "INCOMPLETE"
                wrapMode: Text.WordWrap
                color: Theme.warn
                font.pixelSize: 12
                text: "它没有把这件事做完。下面的步骤和工具调用是它做到哪一步的记录，"
                      + "不是失败报告 —— 可以据此接着做，或把目标写得更具体再试一次。"
            }
        }

        // ------------------------------------------------------- 规划 / 步骤 / 结果 --
        RowLayout {
            Layout.fillWidth: true
            spacing: 16
            visible: page.detail !== null

            ColumnLayout {
                Layout.fillWidth: true
                Layout.preferredWidth: 1
                Layout.alignment: Qt.AlignTop
                spacing: 16

                Card {
                    Layout.fillWidth: true
                    title: "任务规划"
                    subtitle: page.plan.length === 0
                              ? "这次运行没有留下规划"
                              : (page.plan.length + " 个步骤")

                    Repeater {
                        model: page.plan
                        delegate: RowLayout {
                            required property var modelData
                            required property int index
                            Layout.fillWidth: true
                            spacing: 8
                            Text {
                                text: (index + 1) + "."
                                color: Theme.textFaint
                                font.pixelSize: 12
                            }
                            Text {
                                text: typeof modelData === "string"
                                      ? modelData
                                      : (modelData.description ?? modelData.title ?? JSON.stringify(modelData))
                                color: Theme.text
                                font.pixelSize: 13
                                Layout.fillWidth: true
                                wrapMode: Text.WordWrap
                            }
                        }
                    }
                }

                Card {
                    Layout.fillWidth: true
                    title: "工具调用"
                    subtitle: page.calls.length === 0
                              ? "这次运行没有调用工具"
                              : (page.calls.length + " 次调用")

                    Repeater {
                        model: page.calls
                        delegate: Rectangle {
                            required property var modelData
                            Layout.fillWidth: true
                            implicitHeight: callColumn.implicitHeight + 16
                            radius: 8
                            color: "transparent"
                            border.width: 1
                            border.color: modelData.status === "UNKNOWN" ? Theme.warn
                                          : modelData.status === "FAILED" ? Theme.danger
                                          : Theme.border

                            ColumnLayout {
                                id: callColumn
                                anchors.left: parent.left
                                anchors.right: parent.right
                                anchors.verticalCenter: parent.verticalCenter
                                anchors.margins: 8
                                spacing: 4

                                RowLayout {
                                    Layout.fillWidth: true
                                    spacing: 8
                                    Text {
                                        text: "#" + modelData.step_index + " " + modelData.tool_name
                                        color: Theme.text
                                        font.pixelSize: 13
                                        font.bold: true
                                    }
                                    Text {
                                        Layout.fillWidth: true
                                        text: modelData.status
                                        color: page.statusColor(modelData.status)
                                        font.pixelSize: 11
                                    }
                                    Text {
                                        text: modelData.latency_ms + " ms"
                                        color: Theme.textFaint
                                        font.pixelSize: 11
                                    }
                                }
                                Text {
                                    Layout.fillWidth: true
                                    visible: modelData.error_message !== ""
                                    text: modelData.error_message
                                    color: Theme.danger
                                    font.pixelSize: 11
                                    wrapMode: Text.WrapAnywhere
                                }
                                Text {
                                    Layout.fillWidth: true
                                    text: JSON.stringify(modelData.arguments)
                                    color: Theme.textFaint
                                    font.family: "Consolas"
                                    font.pixelSize: 11
                                    elide: Text.ElideRight
                                }
                            }
                        }
                    }
                }
            }

            ColumnLayout {
                Layout.fillWidth: true
                Layout.preferredWidth: 1
                Layout.alignment: Qt.AlignTop
                spacing: 16

                Card {
                    Layout.fillWidth: true
                    title: "结果"
                    subtitle: page.outcome ? "执行总结" : ""

                    Text {
                        Layout.fillWidth: true
                        wrapMode: Text.WordWrap
                        color: Theme.text
                        font.pixelSize: 13
                        text: {
                            if (page.detail && page.detail.final_answer) return page.detail.final_answer
                            if (page.outcome && page.outcome.final_answer) return page.outcome.final_answer
                            return page.running ? "正在执行…" : "还没有结果。"
                        }
                    }
                }

                Card {
                    Layout.fillWidth: true
                    title: "产物"
                    subtitle: page.artifacts.length === 0
                              ? "这次运行没有写出文件"
                              : (page.artifacts.length + " 个位置")

                    Repeater {
                        model: page.artifacts
                        delegate: Rectangle {
                            required property var modelData
                            Layout.fillWidth: true
                            implicitHeight: 46
                            radius: 8
                            color: artMouse.containsMouse ? Theme.bgCardHover : "transparent"
                            border.width: artMouse.containsMouse ? 1 : 0
                            border.color: Theme.border

                            ColumnLayout {
                                anchors.left: parent.left
                                anchors.right: parent.right
                                anchors.verticalCenter: parent.verticalCenter
                                anchors.leftMargin: 8
                                anchors.rightMargin: 8
                                spacing: 2
                                Text {
                                    Layout.fillWidth: true
                                    text: modelData.path
                                    color: Theme.text
                                    font.pixelSize: 12
                                    elide: Text.ElideMiddle
                                }
                                Text {
                                    text: modelData.tool
                                    color: Theme.textFaint
                                    font.pixelSize: 10
                                }
                            }

                            MouseArea {
                                id: artMouse
                                anchors.fill: parent
                                hoverEnabled: true
                                cursorShape: Qt.PointingHandCursor
                                Accessible.role: Accessible.Button
                                Accessible.name: "在文件夹中显示 " + modelData.path
                                onClicked: {
                                    var id = "reveal-" + Date.now()
                                    shell.request("打开位置", id, function () {
                                        shell.toast("info", "已在文件夹中打开")
                                    })
                                    bridge.revealPath(id, modelData.path)
                                }
                            }
                        }
                    }
                }

                Card {
                    Layout.fillWidth: true
                    visible: page.unresolved.length > 0
                    title: "结果未知的调用"
                    subtitle: "这些调用可能改动了东西，也可能没有。这里不会重跑它们。"

                    Repeater {
                        model: page.unresolved
                        delegate: Text {
                            required property var modelData
                            Layout.fillWidth: true
                            text: "#" + modelData.step_index + " " + modelData.tool_name
                            color: Theme.warn
                            font.pixelSize: 12
                            wrapMode: Text.WrapAnywhere
                        }
                    }
                }

                Card {
                    Layout.fillWidth: true
                    title: "历史运行"
                    subtitle: "最近几次"

                    Button {
                        Layout.fillWidth: true
                        text: "查看全部运行记录"
                        onClicked: page.shell.activePage = "runs"
                    }
                }
            }
        }

        Item { Layout.preferredHeight: 24 }
    }
}
