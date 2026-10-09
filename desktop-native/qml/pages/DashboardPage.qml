import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "../components"
import "../theme.js" as Theme

// Real numbers from a real query. `StatsService.dashboard` aggregates the account's own
// rows — nothing here is a placeholder, which is the only way a page proves the wiring.
Flickable {
    id: page
    property var shell

    contentWidth: width
    contentHeight: column.implicitHeight
    clip: true

    property var data: null
    property string errorText: ""

    function load() {
        var id = "dashboard-" + Date.now()
        shell.request("总览", id, function (payload) {
            page.data = payload
            page.errorText = ""
        })
        bridge.loadDashboard(id)
    }

    property bool _loaded: false

    function _firstLoad() {
        if (_loaded || !shell) return
        _loaded = true
        load()
    }

    onShellChanged: _firstLoad()
    Component.onCompleted: _firstLoad()

    ColumnLayout {
        id: column
        width: page.width
        spacing: 16

        RowLayout {
            Layout.fillWidth: true
            Text {
                text: "总览"
                color: Theme.text
                font.pixelSize: 24
                font.bold: true
                Layout.fillWidth: true
            }
            Button {
                text: "刷新"
                onClicked: page.load()
            }
        }

        Text {
            visible: page.errorText !== ""
            text: page.errorText
            color: Theme.danger
            font.pixelSize: 13
        }

        GridLayout {
            Layout.fillWidth: true
            columns: 3
            columnSpacing: 16
            rowSpacing: 16

            Repeater {
                model: page.data ? [
                    { label: "项目", value: page.data.projects ?? page.data.project_count ?? 0 },
                    { label: "任务", value: page.data.tasks ?? page.data.task_count ?? 0 },
                    { label: "文档", value: page.data.documents ?? page.data.document_count ?? 0 }
                ] : []

                delegate: Rectangle {
                    required property var modelData
                    Layout.fillWidth: true
                    Layout.preferredHeight: 110
                    radius: 12
                    color: Theme.bgCard
                    border.width: 1
                    border.color: Theme.border

                    ColumnLayout {
                        anchors.centerIn: parent
                        spacing: 6
                        Text {
                            text: modelData.value
                            color: Theme.accent
                            font.pixelSize: 34
                            font.bold: true
                            Layout.alignment: Qt.AlignHCenter
                        }
                        Text {
                            text: modelData.label
                            color: Theme.textMuted
                            font.pixelSize: 13
                            Layout.alignment: Qt.AlignHCenter
                        }
                    }
                }
            }
        }

        Card {
            Layout.fillWidth: true
            title: "后端连接"
            subtitle: "同一个进程，没有 localhost HTTP"

            Text {
                Layout.fillWidth: true
                wrapMode: Text.WordWrap
                color: Theme.textMuted
                font.pixelSize: 12
                text: page.data
                      ? "本页的数字来自 StatsService.dashboard(owner_id=…) 的一次真实查询。"
                        + " QML 直接调用服务层，中间没有 HTTP 监听、没有 bearer token。"
                      : "正在读取…"
            }
        }

        Card {
            Layout.fillWidth: true
            title: "原始返回"
            Text {
                Layout.fillWidth: true
                wrapMode: Text.WrapAnywhere
                color: Theme.textFaint
                font.family: "Consolas"
                font.pixelSize: 11
                text: page.data ? JSON.stringify(page.data) : "(空)"
            }
        }

        Item { Layout.preferredHeight: 24 }
    }
}
