import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "../components"
import "../theme.js" as Theme

// Long task, live progress, and a cancel that means something.
//
// The run is driven through `AgentExecutor`, the same executor the WebView build used, so
// what this page proves is the *transport*, not a mock: progress arrives while the work is
// happening, and cancel is a request the runner honours at a step boundary.
Flickable {
    id: page
    property var shell

    contentWidth: width
    contentHeight: column.implicitHeight
    clip: true

    property string goal: "把当前项目里未完成的任务列出来"
    property bool running: false
    property string progressText: ""
    property var result: null
    property string errorText: ""
    property bool _loaded: true

    function start() {
        if (goal.trim() === "") {
            shell.toast("warn", "先写一个目标")
            return
        }
        running = true
        result = null
        errorText = ""
        progressText = "已提交…"
        var id = "agent-" + Date.now()
        shell.request("Agent 运行", id, function (payload) {
            page.result = payload
            page.running = false
            page.progressText = ""
            var level = payload.status === "SUCCEEDED" ? "info" : "warn"
            shell.toast(level, "结束：" + payload.status + "（" + payload.stopped_reason + "）")
        })
        bridge.startAgentRun(id, goal)
    }

    Connections {
        target: bridge
        function onProgress(id, message) {
            if (page.running) page.progressText = message
        }
    }

    ColumnLayout {
        id: column
        width: page.width
        spacing: 16

        Text {
            text: "Agent"
            color: Theme.text
            font.pixelSize: 24
            font.bold: true
        }

        Card {
            Layout.fillWidth: true
            title: "运行一个目标"
            subtitle: "没有配置 API Key 时走离线引擎；结果会如实标为 INCOMPLETE，而不是假装成功"

            TextField {
                id: goalField
                Layout.fillWidth: true
                text: page.goal
                onTextChanged: page.goal = text
                color: Theme.text
                font.pixelSize: 14
                placeholderText: "想让 Agent 做什么"
                background: Rectangle {
                    radius: 8
                    color: Theme.bgElevated
                    border.width: goalField.activeFocus ? 2 : 1
                    border.color: goalField.activeFocus ? Theme.borderFocus : Theme.border
                }
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 10

                Button {
                    text: page.running ? "运行中…" : "开始"
                    enabled: !page.running
                    onClicked: page.start()
                }
                Button {
                    text: "取消"
                    enabled: page.running
                    onClicked: bridge.cancelAgentRun()
                }

                Text {
                    Layout.fillWidth: true
                    text: page.progressText
                    color: Theme.accent
                    font.pixelSize: 12
                    elide: Text.ElideRight
                }
            }

            // Indeterminate on purpose: the executor does not report a percentage, and a
            // progress bar that invents one is worse than no progress bar.
            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: 4
                radius: 2
                color: Theme.bgElevated
                visible: page.running

                Rectangle {
                    id: slider
                    width: parent.width * 0.3
                    height: parent.height
                    radius: 2
                    color: Theme.accent

                    NumberAnimation on x {
                        running: page.running
                        from: 0
                        to: slider.parent.width - slider.width
                        duration: 1200
                        loops: Animation.Infinite
                    }
                }
            }
        }

        Card {
            Layout.fillWidth: true
            visible: page.result !== null
            title: "结果"

            RowLayout {
                Layout.fillWidth: true
                spacing: 10
                StatusPill { status: page.result ? page.result.status : "" }
                Text {
                    Layout.fillWidth: true
                    color: Theme.textMuted
                    font.pixelSize: 12
                    text: page.result
                          ? ("停止原因：" + page.result.stopped_reason + "  ·  步骤 " + page.result.steps)
                          : ""
                }
            }

            Text {
                Layout.fillWidth: true
                wrapMode: Text.WordWrap
                color: Theme.text
                font.pixelSize: 13
                text: page.result ? (page.result.final_answer ?? "") : ""
            }

            Text {
                Layout.fillWidth: true
                wrapMode: Text.WrapAnywhere
                color: Theme.textFaint
                font.pixelSize: 11
                font.family: "Consolas"
                text: page.result ? ("run_id " + page.result.run_id) : ""
            }
        }

        Item { Layout.preferredHeight: 24 }
    }
}
