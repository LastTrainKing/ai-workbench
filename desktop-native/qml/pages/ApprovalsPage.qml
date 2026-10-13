import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "../components"
import "../theme.js" as Theme

// The approvals queue.
//
// `ConfirmationBroker` has been parking high-risk tool calls since before this release —
// the run stops, the future waits, and the audit log records the verdict. What was missing
// was the question. A run that reached `WAITING_CONFIRMATION` simply stopped, and the only
// way to find out why was to read `native-startup.log`.
//
// The panel leads with the *reason* rather than the tool name, because the reason is what
// the user needs in order to answer. `impact` and `detail` are there because "delete file"
// is not enough information to approve a deletion — the path is.
Flickable {
    id: page
    property var shell

    contentWidth: width
    contentHeight: column.implicitHeight
    clip: true

    property var items: []
    property string state: "loading"
    property string errorText: ""

    //: The queue is empty most of the time and the whole point is noticing when it is not,
    //: so it polls — slowly, and only while this page is on screen.
    property bool live: false

    function load() {
        var id = "confirmations-" + Date.now()
        if (items.length === 0) state = "loading"
        shell.request("待审批", id, function (payload) {
            page.items = payload.items ?? []
            page.state = "ready"
        }, function (title) {
            page.state = "error"
            page.errorText = title
        })
        bridge.loadConfirmations(id)
    }

    function answer(item, approve) {
        var id = "answer-" + Date.now()
        shell.request("处理审批", id, function (payload) {
            shell.toast(payload.resolved ? "info" : "warn",
                        payload.resolved ? (approve ? "已批准，任务继续" : "已拒绝，任务将停止该操作")
                                         : "这项审批已经不在队列里了")
            page.load()
        }, function (title) {
            shell.toast("error", title)
        })
        bridge.resolveConfirmation(id, item.id, approve)
    }

    property bool _loaded: false
    function _firstLoad() {
        if (_loaded || !shell) return
        _loaded = true
        load()
    }
    onShellChanged: _firstLoad()
    Component.onCompleted: _firstLoad()

    Timer {
        // 4s: long enough not to hammer the bridge, short enough that a run blocked on a
        // question does not sit there for a minute unnoticed.
        interval: 4000
        running: page.live && page.visible
        repeat: true
        onTriggered: page.load()
    }

    ColumnLayout {
        id: column
        width: page.width
        spacing: 16

        RowLayout {
            Layout.fillWidth: true
            Text {
                text: "待审批"
                color: Theme.text
                font.pixelSize: 24
                font.bold: true
                Layout.fillWidth: true
            }
            Text {
                visible: page.items.length > 0
                text: page.items.length + " 项在等你"
                color: Theme.warn
                font.pixelSize: 12
            }
            Switch {
                text: "自动刷新"
                checked: page.live
                onToggled: page.live = checked
            }
            Button {
                text: "刷新"
                onClicked: page.load()
            }
        }

        Text {
            Layout.fillWidth: true
            visible: page.items.length === 0 && page.state === "ready"
            text: "没有待处理的审批。有风险的操作用户没批准之前，员工不会自己执行——"
                  + "它会停在这里等你。"
            color: Theme.textFaint
            font.pixelSize: 13
            wrapMode: Text.WordWrap
        }

        StateBlock {
            Layout.fillWidth: true
            state: page.items.length > 0 ? "ready" : page.state
            busyText: "正在读取待审批…"
            emptyText: "没有待处理的审批。"
            errorText: page.errorText
            onRetry: page.load()
        }

        Repeater {
            model: page.items

            delegate: Rectangle {
                required property var modelData
                Layout.fillWidth: true
                Layout.preferredHeight: itemColumn.implicitHeight + 26
                radius: 10
                color: Theme.bgCard
                border.width: 2
                border.color: modelData.risk === "critical" || modelData.risk === "high"
                              ? Theme.danger : Theme.warn

                ColumnLayout {
                    id: itemColumn
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.top: parent.top
                    anchors.margins: 14
                    spacing: 8

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 10

                        NavIcon { name: "alert"; size: 18; color: Theme.warn }

                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 2
                            Text {
                                Layout.fillWidth: true
                                // The reason first: it is the sentence that says whether to
                                // say yes.
                                text: modelData.reason || modelData.operation || modelData.tool
                                color: Theme.text
                                font.pixelSize: 14
                                font.bold: true
                                wrapMode: Text.WordWrap
                            }
                            Text {
                                text: modelData.tool + " · 风险 " + (modelData.risk ?? "?")
                                      + ((modelData.target ?? "") !== ""
                                         ? " · 目标 " + modelData.target : "")
                                color: Theme.textFaint
                                font.pixelSize: 11
                                elide: Text.ElideMiddle
                                Layout.fillWidth: true
                            }
                        }
                    }

                    Text {
                        Layout.fillWidth: true
                        visible: (modelData.impact ?? "") !== ""
                        text: "影响：" + modelData.impact
                        color: Theme.textMuted
                        font.pixelSize: 12
                        wrapMode: Text.WordWrap
                    }

                    Rectangle {
                        Layout.fillWidth: true
                        visible: (modelData.detail ?? "") !== ""
                        Layout.preferredHeight: detailText.implicitHeight + 18
                        radius: 8
                        color: Theme.surface
                        border.width: 1
                        border.color: Theme.border

                        Text {
                            id: detailText
                            anchors.left: parent.left
                            anchors.right: parent.right
                            anchors.verticalCenter: parent.verticalCenter
                            anchors.margins: 9
                            text: modelData.detail
                            color: Theme.textMuted
                            font.pixelSize: 11
                            font.family: "Consolas, monospace"
                            wrapMode: Text.WordWrap
                        }
                    }

                    Text {
                        Layout.fillWidth: true
                        text: "等待 " + Number(modelData.age_seconds ?? 0).toFixed(0) + " 秒"
                        color: Theme.textFaint
                        font.pixelSize: 11
                    }

                    RowLayout {
                        spacing: 8
                        Button {
                            text: "批准这一次"
                            variant: "primary"
                            onClicked: page.answer(modelData, true)
                        }
                        Button {
                            text: "拒绝"
                            variant: "danger"
                            onClicked: page.answer(modelData, false)
                        }
                        Item { Layout.fillWidth: true }
                    }
                }
            }
        }

        Item { Layout.preferredHeight: 24 }
    }
}
