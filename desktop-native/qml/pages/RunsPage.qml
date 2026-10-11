import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "../components"
import "../theme.js" as Theme
import "../errors.js" as Errors

// Run history, and the record of what actually happened.
//
// Three things were wrong with the previous version, and all three came from the same
// habit of putting a fixed box around content:
//
//   * **Rows overlapped.** A row was `Layout.preferredHeight: 44` and the model's reply was
//     rendered inside it. A reply contains newlines, a `Text` draws every line it is given,
//     and nothing clipped — so the second line drew over the next row. A row is now sized
//     by its content and shows exactly one line per field.
//   * **Everything sat at the top with a screen of nothing under it.** The list is now the
//     part of the page that fills the window, so a short history still occupies the page and
//     a long one scrolls inside it.
//   * **The raw exception was the summary.** `UnicodeEncodeError: 'ascii' codec can't
//     encode character …` was the most prominent text on the page. The row shows the goal
//     and a one-line outcome; the exception is in the expanded record, behind
//     「技术详情」.
//
// The record itself opens *in place* rather than in a second card below the list, which is
// what made the page feel like a form on top of a table.
ColumnLayout {
    id: page
    property var shell

    spacing: Theme.space3

    property var runs: []
    property var detail: null
    property var reconcileReport: null
    property bool loadingRuns: false
    property bool loadingDetail: false
    property bool reconciling: false

    property string runsError: ""
    property string runsErrorHint: ""
    property string runsErrorDetail: ""

    //: Which record is open. One at a time: the page is a list, not an accordion.
    property string openId: ""
    property bool _detailRawOpen: false

    property bool _loaded: false

    function load() {
        loadingRuns = true
        runsError = ""
        runsErrorHint = ""
        runsErrorDetail = ""
        var id = "runs-" + Date.now()
        shell.request("运行历史", id, function (payload) {
            page.runs = (payload && payload.items) ? payload.items : []
            page.loadingRuns = false
        }, function (message, code, human) {
            page.loadingRuns = false
            page.runsError = message
            page.runsErrorHint = human ? human.hint : ""
            page.runsErrorDetail = human ? human.raw : ""
        })
        bridge.loadRuns(id, 50)
    }

    function openRun(runId) {
        if (openId === runId) {
            openId = ""
            detail = null
            return
        }
        openId = runId
        _detailRawOpen = false
        loadingDetail = true
        reconcileReport = null
        detail = null
        var id = "rundetail-" + Date.now()
        shell.request("运行详情", id, function (payload) {
            page.detail = payload
            page.loadingDetail = false
        }, function (message, code, human) {
            page.loadingDetail = false
            page.runsError = message
            page.runsErrorHint = human ? human.hint : ""
            page.runsErrorDetail = human ? human.raw : ""
        })
        bridge.loadRunDetail(id, runId)
    }

    function reconcile() {
        if (!detail) return
        var runId = detail.run.run_id
        reconciling = true
        var id = "reconcile-" + Date.now()
        shell.request("对账", id, function (payload) {
            page.reconcileReport = payload
            page.reconciling = false
            shell.toast("info", "对账完成：" + payload.decided + " 条有结论，未重跑任何调用")
            // Reload the record in place. `openRun` toggles, so close first — otherwise
            // the record we just reconciled would close instead of refreshing.
            page.openId = ""
            page.openRun(runId)
        })
        bridge.reconcileRun(id, runId)
    }

    function statusWord(status) {
        return Theme.runWord(status)
    }

    //: The one line a row shows for an outcome. Never multi-line: that is what drew over
    //: the next row.
    function summaryOf(run) {
        var preview = Errors.firstLine(run.response_preview || "", 90)
        if (preview !== "") return preview
        if (run.error_message) return Errors.human(run.error_message, run.error_code).title
        return "（没有输出）"
    }

    function ago(iso) {
        if (!iso) return ""
        var then = Date.parse(iso)
        if (isNaN(then)) return String(iso).substring(0, 16).replace("T", " ")
        var minutes = Math.round((Date.now() - then) / 60000)
        if (minutes < 1) return "刚刚"
        if (minutes < 60) return minutes + " 分钟前"
        var hours = Math.round(minutes / 60)
        if (hours < 24) return hours + " 小时前"
        return Math.round(hours / 24) + " 天前"
    }

    Component.onCompleted: load()

    // ================================================================ header ==
    RowLayout {
        Layout.fillWidth: true
        spacing: Theme.space3

        Text {
            text: "运行记录"
            color: Theme.text
            font.pixelSize: Theme.fontDisplay
            font.bold: true
        }
        Text {
            Layout.fillWidth: true
            // Three states, three sentences. "0 次运行" over a failed request is a claim
            // the page cannot support, and it is the same shape of lie as a panel that
            // stays on "读取中…" after the call has already failed.
            text: page.loadingRuns ? "读取中…"
                                   : (page.runsError !== "" ? "读取失败"
                                   : (page.runs.length + " 次运行"))
            color: Theme.textFaint
            font.pixelSize: Theme.fontSmall
        }
        Button {
            text: "刷新"
            variant: "ghost"
            onClicked: page.load()
            Accessible.name: "刷新运行历史"
        }
        Button {
            text: "去 Agent 执行"
            variant: "primary"
            onClicked: shell.navigate("agent")
            Accessible.name: "去 Agent 工作台执行新的目标"
        }
    }

    // ================================================================== list ==
    //
    // `fillHeight` is the fix for "content piled at the top, half the window empty": the
    // list owns the rest of the page whether it holds two records or two hundred.
    Flickable {
        id: list
        Layout.fillWidth: true
        Layout.fillHeight: true
        contentWidth: width
        contentHeight: listColumn.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds

        ColumnLayout {
            id: listColumn
            width: list.width
            spacing: Theme.space1

            StateBlock {
                Layout.fillWidth: true
                state: page.loadingRuns ? "loading"
                       : (page.runsError !== "" ? "error"
                       : (page.runs.length === 0 ? "empty" : "ready"))
                busyText: "读取运行历史…"
                errorText: page.runsError
                errorHint: page.runsErrorHint
                errorDetail: page.runsErrorDetail
                emptyText: "还没有让 Agent 做过事。在 Agent 页写下目标，交给它执行。"
                onRetry: page.load()
            }

            Repeater {
                model: page.runs

                delegate: ColumnLayout {
                    id: runRow
                    required property var modelData
                    required property int index
                    readonly property bool open: page.openId === modelData.run_id

                    Layout.fillWidth: true
                    spacing: 0

                    Rectangle {
                        Layout.fillWidth: true
                        // Sized by its content. There is no height a run has to fit into.
                        implicitHeight: rowLayout.implicitHeight + Theme.space3 * 2
                        radius: Theme.radiusSm
                        color: runRow.open ? Theme.surface
                             : rowMouse.containsMouse ? Theme.surfaceHover
                             : "transparent"

                        objectName: "run-row-" + runRow.modelData.run_id
                        Accessible.role: Accessible.Button
                        Accessible.name: (runRow.modelData.goal || runRow.modelData.run_id)
                                         + "，" + Theme.runWord(runRow.modelData.status)
                        Accessible.description: runRow.open ? "收起详情" : "展开详情"

                        MouseArea {
                            id: rowMouse
                            anchors.fill: parent
                            hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            onClicked: page.openRun(runRow.modelData.run_id)
                        }

                        RowLayout {
                            id: rowLayout
                            anchors.left: parent.left
                            anchors.right: parent.right
                            anchors.verticalCenter: parent.verticalCenter
                            anchors.leftMargin: Theme.space3
                            anchors.rightMargin: Theme.space3
                            spacing: Theme.space3

                            Rectangle {
                                width: 7; height: 7; radius: 3.5
                                color: Theme.runTone(runRow.modelData.status)
                                Layout.alignment: Qt.AlignTop
                                Layout.topMargin: 6
                                Accessible.ignored: true
                            }

                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 2

                                Text {
                                    Layout.fillWidth: true
                                    text: runRow.modelData.goal !== ""
                                          ? runRow.modelData.goal
                                          : "（没有记录目标）"
                                    color: Theme.text
                                    font.pixelSize: Theme.fontBody
                                    // One line, always. The goal is the name of the task;
                                    // the whole of it is in the record.
                                    elide: Text.ElideRight
                                    maximumLineCount: 1
                                }
                                Text {
                                    Layout.fillWidth: true
                                    text: Theme.runWord(runRow.modelData.status) + " · "
                                          + page.summaryOf(runRow.modelData)
                                    color: Theme.textMuted
                                    font.pixelSize: Theme.fontSmall
                                    elide: Text.ElideRight
                                    maximumLineCount: 1
                                }
                            }

                            Text {
                                text: page.ago(runRow.modelData.created_at)
                                color: Theme.textFaint
                                font.pixelSize: Theme.fontTiny
                                Layout.alignment: Qt.AlignVCenter
                            }

                            NavIcon {
                                name: "chevron"
                                size: 14
                                color: Theme.textFaint
                                rotation: runRow.open ? 90 : 0
                                Layout.alignment: Qt.AlignVCenter
                                Accessible.ignored: true
                            }
                        }
                    }

                    // A hairline between rows. The rows have no fill of their own — that
                    // is what stops the page being a wall of boxes — and without a line
                    // eight of them read as one paragraph.
                    Rectangle {
                        Layout.fillWidth: true
                        Layout.leftMargin: Theme.space3
                        Layout.rightMargin: Theme.space3
                        implicitHeight: 1
                        color: Theme.divider
                        visible: runRow.index < page.runs.length - 1 && !runRow.open
                        Accessible.ignored: true
                    }

                    // ------------------------------------------------------ record --
                    Panel {
                        Layout.fillWidth: true
                        Layout.topMargin: Theme.space1
                        Layout.bottomMargin: Theme.space3
                        visible: runRow.open
                        padding: Theme.space4

                        StateBlock {
                            Layout.fillWidth: true
                            state: page.loadingDetail ? "loading" : "ready"
                            busyText: "读取运行详情…"
                        }

                        // meta
                        RowLayout {
                            Layout.fillWidth: true
                            spacing: Theme.space3
                            visible: page.detail !== null

                            StatusPill { status: page.detail ? page.detail.run.status : "" }
                            Text {
                                Layout.fillWidth: true
                                color: Theme.textMuted
                                font.pixelSize: Theme.fontSmall
                                elide: Text.ElideRight
                                text: page.detail
                                      ? ("步骤 " + page.detail.steps_used
                                         + " · " + page.detail.run.total_tokens + " tokens"
                                         + " · " + page.detail.run.tool_call_count + " 次工具调用")
                                      : ""
                            }
                            Text {
                                color: Theme.textFaint
                                font.pixelSize: Theme.fontTiny
                                text: page.detail && page.detail.run.created_at
                                      ? page.detail.run.created_at.substring(0, 19).replace("T", " ")
                                      : ""
                            }
                        }

                        // goal
                        Text {
                            Layout.fillWidth: true
                            visible: page.detail !== null && page.detail.goal !== ""
                            wrapMode: Text.WordWrap
                            color: Theme.text
                            font.pixelSize: Theme.fontBody
                            text: page.detail ? page.detail.goal : ""
                        }

                        // failure, in the reader's terms, with the exception one toggle away
                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: Theme.space1
                            visible: page.detail !== null && page.detail.run.error_message !== ""

                            Text {
                                Layout.fillWidth: true
                                wrapMode: Text.WordWrap
                                color: Theme.danger
                                font.pixelSize: Theme.fontSmall
                                font.bold: true
                                text: page.detail
                                      ? Errors.human(page.detail.run.error_message,
                                                     page.detail.run.error_code).title
                                      : ""
                            }
                            Text {
                                Layout.fillWidth: true
                                wrapMode: Text.WordWrap
                                color: Theme.textMuted
                                font.pixelSize: Theme.fontSmall
                                text: page.detail
                                      ? Errors.human(page.detail.run.error_message,
                                                     page.detail.run.error_code).hint
                                      : ""
                            }
                            Button {
                                variant: "ghost"
                                text: page._detailRawOpen ? "收起技术详情" : "技术详情"
                                onClicked: page._detailRawOpen = !page._detailRawOpen
                            }
                            Flickable {
                                Layout.fillWidth: true
                                visible: page._detailRawOpen
                                implicitHeight: Math.min(rawText.implicitHeight, 140)
                                contentHeight: rawText.implicitHeight
                                clip: true
                                boundsBehavior: Flickable.StopAtBounds
                                Text {
                                    id: rawText
                                    width: parent.width
                                    text: page.detail ? page.detail.run.error_message : ""
                                    color: Theme.textFaint
                                    font.pixelSize: Theme.fontTiny
                                    font.family: "Consolas"
                                    wrapMode: Text.WrapAnywhere
                                    textFormat: Text.PlainText
                                }
                            }
                        }

                        // result
                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: Theme.space1
                            visible: page.detail !== null && page.detail.final_answer !== ""

                            Text {
                                Layout.fillWidth: true
                                text: "结果"
                                color: Theme.textFaint
                                font.pixelSize: Theme.fontTiny
                                font.bold: true
                                font.letterSpacing: 0.8
                            }
                            // Capped and scrollable: an Agent's answer can be thousands of
                            // words, and it must not push everything else off the page.
                            Flickable {
                                Layout.fillWidth: true
                                implicitHeight: Math.min(answerText.implicitHeight, 220)
                                contentHeight: answerText.implicitHeight
                                clip: true
                                boundsBehavior: Flickable.StopAtBounds
                                Text {
                                    id: answerText
                                    width: parent.width
                                    text: page.detail ? page.detail.final_answer : ""
                                    color: Theme.text
                                    font.pixelSize: Theme.fontBody
                                    wrapMode: Text.WordWrap
                                }
                            }
                        }

                        // unresolved calls
                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: Theme.space2
                            visible: page.detail !== null && (page.detail.unresolved || []).length > 0

                            Text {
                                Layout.fillWidth: true
                                text: "无法确认是否已产生效果（"
                                      + (page.detail ? page.detail.unresolved.length : 0) + "）"
                                color: Theme.warn
                                font.pixelSize: Theme.fontBody
                                font.bold: true
                            }
                            Text {
                                Layout.fillWidth: true
                                wrapMode: Text.WordWrap
                                color: Theme.textMuted
                                font.pixelSize: Theme.fontSmall
                                text: "进程在工具执行期间终止，没有留下结果记录。「对账」会去核对"
                                      + "目标（文件内容、目录、git 索引）并给出结论，不会重跑任何调用。"
                            }
                            Repeater {
                                model: page.detail ? page.detail.unresolved : []
                                delegate: Text {
                                    required property var modelData
                                    Layout.fillWidth: true
                                    wrapMode: Text.WrapAnywhere
                                    color: Theme.textMuted
                                    font.pixelSize: Theme.fontTiny
                                    font.family: "Consolas"
                                    text: "第 " + modelData.step_index + " 步  " + modelData.tool_name
                                          + "  " + JSON.stringify(modelData.arguments)
                                }
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                spacing: Theme.space2
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
                                    font.pixelSize: Theme.fontTiny
                                    text: page.reconcileReport
                                          ? ("已核对 " + page.reconcileReport.checked + " 条，"
                                             + page.reconcileReport.decided + " 条有结论")
                                          : ""
                                }
                            }
                        }

                        // tool calls
                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: Theme.space2
                            visible: page.detail !== null && (page.detail.tool_calls || []).length > 0

                            Text {
                                Layout.fillWidth: true
                                text: "工具调用"
                                color: Theme.textFaint
                                font.pixelSize: Theme.fontTiny
                                font.bold: true
                                font.letterSpacing: 0.8
                            }
                            Repeater {
                                model: page.detail ? page.detail.tool_calls : []
                                delegate: ColumnLayout {
                                    required property var modelData
                                    Layout.fillWidth: true
                                    spacing: Theme.space1

                                    RowLayout {
                                        Layout.fillWidth: true
                                        spacing: Theme.space2
                                        Text {
                                            color: Theme.textFaint
                                            font.pixelSize: Theme.fontTiny
                                            text: "#" + modelData.step_index
                                        }
                                        Text {
                                            Layout.fillWidth: true
                                            color: Theme.text
                                            font.pixelSize: Theme.fontSmall
                                            font.family: "Consolas"
                                            elide: Text.ElideRight
                                            text: modelData.tool_name
                                        }
                                        StatusPill { status: modelData.status }
                                        Text {
                                            color: Theme.textFaint
                                            font.pixelSize: Theme.fontTiny
                                            text: modelData.latency_ms + " ms"
                                        }
                                    }
                                    Text {
                                        Layout.fillWidth: true
                                        wrapMode: Text.WrapAnywhere
                                        color: Theme.textFaint
                                        font.pixelSize: Theme.fontTiny
                                        font.family: "Consolas"
                                        text: JSON.stringify(modelData.arguments)
                                    }
                                    Text {
                                        Layout.fillWidth: true
                                        visible: modelData.error_message !== ""
                                        wrapMode: Text.WrapAnywhere
                                        color: Theme.warn
                                        font.pixelSize: Theme.fontTiny
                                        text: modelData.error_message
                                    }
                                    Rectangle {
                                        Layout.fillWidth: true
                                        implicitHeight: 1
                                        color: Theme.divider
                                        Accessible.ignored: true
                                    }
                                }
                            }
                        }
                    }
                }
            }

            Item { Layout.preferredHeight: Theme.space5 }
        }
    }
}
