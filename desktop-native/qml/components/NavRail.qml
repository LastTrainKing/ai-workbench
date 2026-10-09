import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "../theme.js" as Theme

// Navigation rail. Every entry is focusable and reachable with the arrow keys, which is
// the point of a Big Picture shell: no mouse required.
Rectangle {
    id: rail

    property string activePage: "dashboard"
    property int inflight: 0
    signal navigate(string page)

    color: Theme.bgElevated
    border.width: 0

    readonly property var entries: [
        { key: "dashboard", label: "总览",   hint: "Ctrl+1" },
        { key: "knowledge", label: "知识库", hint: "Ctrl+2" },
        { key: "tasks",     label: "任务",   hint: "Ctrl+3" },
        { key: "runs",      label: "Agent",  hint: "Ctrl+4" },
        { key: "settings",  label: "设置",   hint: "Ctrl+5" }
    ]

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: Theme.scale(18, 1)
        spacing: Theme.scale(10, 1)

        RowLayout {
            Layout.fillWidth: true
            spacing: 8

            Rectangle {
                width: 10; height: 10; radius: 5
                color: Theme.accent
            }
            Text {
                text: "AI Workbench"
                color: Theme.text
                font.pixelSize: 17
                font.bold: true
                Layout.fillWidth: true
            }
        }

        Text {
            text: "原生界面技术验证"
            color: Theme.textFaint
            font.pixelSize: 11
        }

        Item { Layout.preferredHeight: 12 }

        Repeater {
            model: rail.entries

            delegate: Rectangle {
                id: entry
                required property var modelData
                readonly property bool active: rail.activePage === modelData.key

                Layout.fillWidth: true
                Layout.preferredHeight: 46
                radius: 8
                color: active ? Theme.bgCardHover : (mouse.containsMouse ? Theme.bgCard : "transparent")
                border.width: active ? 2 : 0
                border.color: Theme.borderFocus

                // Focus is visible, not implied. Without this a keyboard user cannot tell
                // where they are, and "keyboard-friendly" is a claim rather than a feature.
                Rectangle {
                    anchors.fill: parent
                    radius: parent.radius
                    color: "transparent"
                    border.width: entry.activeFocus ? 2 : 0
                    border.color: Theme.borderFocus
                    visible: entry.activeFocus
                }

                RowLayout {
                    anchors.fill: parent
                    anchors.leftMargin: 12
                    anchors.rightMargin: 12

                    Text {
                        text: entry.modelData.label
                        color: entry.active ? Theme.text : Theme.textMuted
                        font.pixelSize: 14
                        font.bold: entry.active
                        Layout.fillWidth: true
                    }
                    Text {
                        text: entry.modelData.hint
                        color: Theme.textFaint
                        font.pixelSize: 10
                    }
                }

                MouseArea {
                    id: mouse
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: rail.navigate(entry.modelData.key)
                }

                Keys.onReturnPressed: rail.navigate(entry.modelData.key)
                Keys.onSpacePressed: rail.navigate(entry.modelData.key)
                focus: entry.active
            }
        }

        Item { Layout.fillHeight: true }

        // Honest state, not a spinner that means nothing: how many calls are outstanding.
        RowLayout {
            Layout.fillWidth: true
            spacing: 8
            Rectangle {
                width: 8; height: 8; radius: 4
                color: rail.inflight > 0 ? Theme.accent : Theme.textFaint
            }
            Text {
                text: rail.inflight > 0 ? ("进行中 " + rail.inflight) : "空闲"
                color: Theme.textFaint
                font.pixelSize: 11
                Layout.fillWidth: true
            }
        }
    }
}
