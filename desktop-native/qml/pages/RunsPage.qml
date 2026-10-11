import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "../components"
import "../theme.js" as Theme

// Start a run, then read what it did — including the calls it could not finish.
//
// Three things on one page, because they are one story: you start a run, it finishes (or
// the process dies), and then you need to know what it actually changed. The middle part is
// what the WebView build never had — run history came from a live in-memory registry, so
// every run older than the current process simply was not there.
//
// The bottom card is the one that matters for the crash-safety work: invocations left in
// UNKNOWN are listed with the tool, the step and the arguments, and 「对账」 asks the system
// to go and look. The answer is one of APPLIED / NOT_APPLIED / UNDECIDABLE, and the page
// states plainly that nothing will be re-run — a user who is told "we can check" will
// otherwise assume "we can retry".
Flickable {
    id: page
    property var shell

    contentWidth: width
    contentHeight: column.implicitHeight
    clip: true

    property string errorText: ""

    property var runs: []
    property var detail: null
    property var reconcileReport: null
    property bool loadingRuns: false
    property bool loadingDetail: false
    property bool reconciling: false

    function load() {
        loadingRuns = true
        var id = "runs-" + Date.now()
        shell.request("运行历史", id, function (payload) {
            page.runs = payload.items || []
            page.loadingRuns = false
        })
        bridge.loadRuns(id, 50)
    }

    function openRun(runId) {
        loadingDetail = true
        reconcileReport = null
        var id = "rundetail-" + Date.now()
        shell.request("运行详情", id, function (payload) {
            page.detail = payload
            page.loadingDetail = false
        })
        bridge.loadRunDetail(id, runId)
    }

    function reconcile() {
        if (!detail) return
        reconciling = true
        var id = "reconcile-" + Date.now()
        shell.request("对账", id, function (payload) {
            page.reconcileReport = payload
            page.reconciling = false
            shell.toast("info", "对账完成：" + payload.decided + " 条有结论，未重跑任何调用")
            page.openRun(page.detail.run.run_id)
        })
        bridge.reconcileRun(id, detail.run.run_id)
    }

    function statusWord(status) {
        switch (status) {
        case "SUCCEEDED":   return "成功"
        case "FAILED":      return "失败"
        case "RUNNING":     return "运行中"
        case "INTERRUPTED": return "被中断"
        case "INCOMPLETE":  return "未完成"
        case "CANCELLED":   return "已取消"
        case "UNKNOWN":     return "无法确认"
        case "NOT_APPLIED": return "未生效"
        case "SKIPPED":     return "已跳过"
        default:            return status || "未知"
        }
    }

    Component.onCompleted: load()

    Connections {
        target: bridge
    }

    ColumnLayout {
        id: column
        width: page.width
        spacing: 16

        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.space3

            Text {
                text: "运行记录"
                color: Theme.text
                font.pixelSize: Theme.fontDisplay
                font.bold: true
                Layout.fillWidth: true
            }
            Text {
                text: page.runs.length + " 次运行"
                color: Theme.textFaint
                font.pixelSize: Theme.fontSmall
            }
            Button {
                text: "去 Agent 执行"
                variant: "primary"
                onClicked: shell.navigate("agent")
                Accessible.name: "去 Agent 工作台执行新的目标"
            }
        }

        Card {
            Layout.fillWidth: true
            title: "运行历史"
            subtitle: page.loadingRuns
                      ? "读取中…"
                      : (page.runs.length + " 次运行（来自数据库，重启后仍在）")

            Button {
                objectName: "runs-reload"
                text: "刷新"
                onClicked: page.load()
                Accessible.name: "刷新运行历史"
            }

            Text {
                Layout.fillWidth: true
                visible: !page.loadingRuns && page.runs.length === 0
                color: Theme.textMuted
                font.pixelSize: 13
                text: "还没有运行记录。"
            }

            Repeater {
                model: page.runs

                delegate: Rectangle {
                    required property var modelData

                    objectName: "run-row-" + modelData.run_id
                    Layout.fillWidth: true
                    Layout.preferredHeight: 44
                    radius: 8
                    color: rowMouse.containsMouse ? Theme.bgCardHover : Theme.bgElevated
                    border.width: page.detail && page.detail.run.run_id === modelData.run_id ? 2 : 1
                    border.color: page.detail && page.detail.run.run_id === modelData.run_id
                                  ? Theme.borderFocus : Theme.border

                    Accessible.role: Accessible.Button
                    Accessible.name: "运行 " + modelData.run_id + "，" + page.statusWord(modelData.status)

                    MouseArea {
                        id: rowMouse
                        anchors.fill: parent
                        hoverEnabled: true
                        onClicked: page.openRun(modelData.run_id)
                    }

                    RowLayout {
                        anchors.fill: parent
                        anchors.leftMargin: 12
                        anchors.rightMargin: 12
                        spacing: 12

                        StatusPill { status: modelData.status }

                        Text {
                            Layout.fillWidth: true
                            color: Theme.text
                            font.pixelSize: 13
                            elide: Text.ElideRight
                            text: (modelData.response_preview || modelData.error_message || "（无输出）")
                        }

                        Text {
                            color: Theme.textFaint
                            font.pixelSize: 11
                            text: modelData.tool_call_count + " 次工具调用"
                        }

                        Text {
                            color: Theme.textFaint
                            font.pixelSize: 11
                            text: modelData.created_at
                                  ? modelData.created_at.substring(0, 19).replace("T", " ")
                                  : ""
                        }
                    }
                }
            }
        }

        Card {
            Layout.fillWidth: true
            visible: page.detail !== null
            title: "运行详情"
            subtitle: page.detail ? page.detail.run.run_id : ""

            Text {
                Layout.fillWidth: true
                visible: page.loadingDetail
                color: Theme.textMuted
                font.pixelSize: 13
                text: "读取中…"
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 10
                visible: page.detail !== null
                StatusPill { status: page.detail ? page.detail.run.status : "" }
                Text {
                    Layout.fillWidth: true
                    color: Theme.textMuted
                    font.pixelSize: 12
                    text: page.detail
                          ? ("步骤 " + page.detail.steps_used + "  ·  "
                             + page.detail.run.total_tokens + " tokens  ·  "
                             + page.detail.run.tool_call_count + " 次工具调用")
                          : ""
                }
            }

            Text {
                Layout.fillWidth: true
                visible: page.detail && page.detail.goal !== ""
                wrapMode: Text.WordWrap
                color: Theme.textMuted
                font.pixelSize: 12
                text: page.detail ? ("目标：" + page.detail.goal) : ""
            }

            Text {
                Layout.fillWidth: true
                visible: page.detail && page.detail.run.error_message !== ""
                wrapMode: Text.WordWrap
                color: Theme.danger
                font.pixelSize: 12
                text: page.detail ? ("原因：" + page.detail.run.error_message) : ""
            }

            // -------------------------------------------------- unresolved calls --
            //
            // The reason this page exists in this round. A run that died mid-tool leaves
            // invocations nobody can attribute; they are listed here rather than buried,
            // and the button asks the system to check the world.
            Rectangle {
                Layout.fillWidth: true
                objectName: "unresolved-panel"
                readonly property var items: page.detail ? (page.detail.unresolved || []) : []
                readonly property string a11yName: items.length === 0
                    ? "没有无法确认的工具调用"
                    : (items.length + " 次工具调用无法确认是否已产生效果")
                visible: page.detail !== null && items.length > 0
                implicitHeight: unresolvedColumn.implicitHeight + 24
                radius: 8
                color: Theme.bgElevated
                border.width: 2
                border.color: Theme.warn

                Accessible.role: Accessible.StaticText
                Accessible.name: a11yName

                ColumnLayout {
                    id: unresolvedColumn
                    anchors.fill: parent
                    anchors.margins: 12
                    spacing: 8

                    Text {
                        Layout.fillWidth: true
                        text: "无法确认是否已产生效果（" + (page.detail ? page.detail.unresolved.length : 0) + "）"
                        color: Theme.warn
                        font.pixelSize: 14
                        font.bold: true
                    }

                    Text {
                        Layout.fillWidth: true
                        wrapMode: Text.WordWrap
                        color: Theme.textMuted
                        font.pixelSize: 12
                        text: "进程在工具执行期间终止，没有留下结果记录。「对账」会去核对目标"
                              + "（文件内容、目录、git 索引）并给出结论。不会重跑任何调用。"
                    }

                    Repeater {
                        model: page.detail ? page.detail.unresolved : []

                        delegate: Text {
                            required property var modelData
                            Layout.fillWidth: true
                            wrapMode: Text.WrapAnywhere
                            color: Theme.text
                            font.pixelSize: 12
                            font.family: "Consolas"
                            text: "第 " + modelData.step_index + " 步  " + modelData.tool_name
                                  + "  " + JSON.stringify(modelData.arguments)
                        }
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 10

                        Button {
                            objectName: "unresolved-reconcile"
                            text: page.reconciling ? "核对中…" : "对账"
                            enabled: !page.reconciling
                            onClicked: page.reconcile()
                            Accessible.name: "对账这些调用"
                        }

                        Text {
                            Layout.fillWidth: true
                            color: Theme.textFaint
                            font.pixelSize: 11
                            text: page.reconcileReport
                                  ? ("已核对 " + page.reconcileReport.checked + " 条，"
                                     + page.reconcileReport.decided + " 条有结论")
                                  : ""
                        }
                    }
                }
            }

            Text {
                Layout.fillWidth: true
                visible: page.detail !== null && (page.detail.unresolved || []).length === 0
                color: Theme.success
                font.pixelSize: 12
                text: "没有无法确认的工具调用。"
            }

            // ------------------------------------------------------- tool calls --
            Repeater {
                model: page.detail ? page.detail.tool_calls : []

                delegate: Rectangle {
                    required property var modelData

                    objectName: "toolcall-" + modelData.invocation_id
                    Layout.fillWidth: true
                    implicitHeight: callColumn.implicitHeight + 20
                    radius: 8
                    color: Theme.bgElevated
                    border.width: 1
                    border.color: modelData.status === "UNKNOWN" ? Theme.warn
                                : modelData.status === "FAILED" ? Theme.danger
                                : modelData.status === "NOT_APPLIED" ? Theme.textFaint
                                : Theme.border

                    Accessible.role: Accessible.StaticText
                    Accessible.name: "第 " + modelData.step_index + " 步 " + modelData.tool_name
                                     + " " + page.statusWord(modelData.status)

                    ColumnLayout {
                        id: callColumn
                        anchors.fill: parent
                        anchors.margins: 10
                        spacing: 4

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 10

                            Text {
                                color: Theme.textFaint
                                font.pixelSize: 11
                                text: "#" + modelData.step_index
                            }
                            Text {
                                color: Theme.text
                                font.pixelSize: 13
                                font.family: "Consolas"
                                text: modelData.tool_name
                            }
                            StatusPill { status: modelData.status }
                            Text {
                                Layout.fillWidth: true
                                color: Theme.textFaint
                                font.pixelSize: 11
                                text: modelData.latency_ms + " ms"
                                      + (modelData.mutates_state ? "  ·  会改变状态" : "")
                            }
                        }

                        Text {
                            Layout.fillWidth: true
                            wrapMode: Text.WrapAnywhere
                            color: Theme.textMuted
                            font.pixelSize: 11
                            font.family: "Consolas"
                            text: JSON.stringify(modelData.arguments)
                        }

                        Text {
                            Layout.fillWidth: true
                            visible: modelData.error_message !== ""
                            wrapMode: Text.WordWrap
                            color: Theme.warn
                            font.pixelSize: 11
                            text: modelData.error_message
                        }
                    }
                }
            }
        }

        Item { Layout.preferredHeight: 24 }
    }
}
