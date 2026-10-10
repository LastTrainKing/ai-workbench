import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "../theme.js" as Theme

// Navigation rail. Every entry is focusable and reachable with the arrow keys, which is
// the point of a Big Picture shell: no mouse required.
//
// The arrow keys were **claimed** here before they worked. The comment said so, the
// entries were focusable, and there was no handler for Up or Down — so pressing an arrow
// moved nothing, and the claim was a comment rather than a feature. The handlers below are
// the feature; `verify_native_shell.py` presses the keys and checks where focus landed.
Rectangle {
    id: rail

    property string activePage: "dashboard"
    property int inflight: 0
    signal navigate(string page)

    color: Theme.bgElevated
    border.width: 0

    readonly property var entries: [
        { key: "dashboard",    label: "总览",   hint: Theme.shortcutFor("dashboard") },
        { key: "knowledge",    label: "知识库", hint: Theme.shortcutFor("knowledge") },
        { key: "documents",    label: "文档",   hint: Theme.shortcutFor("documents") },
        { key: "ask",          label: "问答",   hint: Theme.shortcutFor("ask") },
        { key: "conversation", label: "对话",   hint: Theme.shortcutFor("conversation") },
        { key: "permissions",  label: "权限",   hint: Theme.shortcutFor("permissions") },
        { key: "tasks",        label: "任务",   hint: Theme.shortcutFor("tasks") },
        { key: "projects",     label: "项目",   hint: Theme.shortcutFor("projects") },
        { key: "notes",        label: "笔记",   hint: Theme.shortcutFor("notes") },
        { key: "runs",         label: "Agent",  hint: Theme.shortcutFor("runs") },
        { key: "settings",     label: "设置",   hint: Theme.shortcutFor("settings") }
    ]

    // ------------------------------------------------------------- keyboard --
    // Moving focus is what makes the rail usable without a mouse. Activating is a
    // separate key, so arrowing past an entry does not navigate to it — which is the
    // difference between a list you can browse and a list that fires as you scroll.
    function moveFocus(from, delta) {
        var keys = rail.entries.map(function (e) { return e.key })
        var index = keys.indexOf(from)
        if (index < 0) return
        var next = index + delta
        if (next < 0 || next >= keys.length) return
        rail.focusEntry(keys[next])
    }

    function focusEntry(key) {
        for (var i = 0; i < entryRepeater.count; i++) {
            var item = entryRepeater.itemAt(i)
            if (item && item.entryKey === key) {
                item.forceActiveFocus()
                return true
            }
        }
        return false
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: Theme.scale(18, 1)
        spacing: Theme.scale(6, 1)

        RowLayout {
            Layout.fillWidth: true
            spacing: 8

            Rectangle {
                width: 10; height: 10; radius: 5
                color: Theme.accent
                Accessible.ignored: true
            }
            Text {
                text: "AI Workbench"
                color: Theme.text
                font.pixelSize: 17
                font.bold: true
                Layout.fillWidth: true
                Accessible.role: Accessible.Heading
                Accessible.name: "AI Workbench"
            }
        }

        Text {
            text: "原生界面技术验证"
            color: Theme.textFaint
            font.pixelSize: 11
            Accessible.ignored: true
        }

        Item { Layout.preferredHeight: 10 }

        Repeater {
            id: entryRepeater
            model: rail.entries

            delegate: Rectangle {
                id: entry
                required property var modelData
                required property int index
                readonly property bool active: rail.activePage === modelData.key
                //: Exposed so the rail can move focus by key rather than by index. The
                //: delegate's own `index` is not addressable from outside the Repeater.
                readonly property string entryKey: modelData.key

                Layout.fillWidth: true
                Layout.preferredHeight: 42
                radius: 8
                color: active ? Theme.bgCardHover : (mouse.containsMouse ? Theme.bgCard : "transparent")
                border.width: active ? 2 : 0
                border.color: Theme.borderFocus

                //: The identity the verification harness uses to find this entry. Qt
                //: exposes `objectName` to `findChild`, which is how a keyboard test can
                //: assert *which* entry received focus rather than only that focus moved.
                objectName: "nav-" + modelData.key

                //: The accessible text, mirrored onto ordinary properties so a test can
                //: read the value the screen reader will be given. `Accessible.name` is an
                //: attached property with no Python accessor; binding it to a property on
                //: the item means the check reads the same string the accessibility tree
                //: carries, instead of a copy that could drift away from it.
                readonly property string a11yName: modelData.label
                readonly property string a11yRole: "PageTab"
                readonly property string a11yDescription: active
                    ? (modelData.hint ? modelData.hint + "，当前页面" : "当前页面")
                    : (modelData.hint
                        ? "切换到" + modelData.label + "，" + modelData.hint
                        : "切换到" + modelData.label)

                // Focus is visible, not implied. Without this a keyboard user cannot tell
                // where they are, and "keyboard-friendly" is a claim rather than a feature.
                Rectangle {
                    anchors.fill: parent
                    radius: parent.radius
                    color: "transparent"
                    border.width: entry.activeFocus ? 2 : 0
                    border.color: Theme.borderFocus
                    visible: entry.activeFocus
                    Accessible.ignored: true
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
                        Accessible.ignored: true
                    }
                    Text {
                        text: entry.modelData.hint
                        color: Theme.textFaint
                        font.pixelSize: 10
                        Accessible.ignored: true
                    }
                }

                // The accessible node is the entry itself, not the two labels inside it.
                // A screen reader should hear "知识库, 页签, 当前页" once — not the title,
                // then the shortcut, then the title again.
                Accessible.role: Accessible.PageTab
                Accessible.name: entry.a11yName
                Accessible.description: entry.a11yDescription
                Accessible.focusable: true
                Accessible.selected: entry.active

                MouseArea {
                    id: mouse
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: rail.navigate(entry.modelData.key)
                }

                // One handler for every key, rather than `Keys.onUpPressed`,
                // `Keys.onHomePressed`, … The named signals differ between Qt versions —
                // `Keys.onEndPressed` does not exist in this one, and the component failed
                // to load with "Cannot assign to non-existent property", which took the
                // whole window down with it. Dispatching on `event.key` depends on nothing
                // but the key codes, and it puts the whole keyboard map in one readable
                // place instead of spread across six handlers.
                Keys.onPressed: function (event) {
                    switch (event.key) {
                    case Qt.Key_Up:
                        rail.moveFocus(entry.modelData.key, -1)
                        break
                    case Qt.Key_Down:
                        rail.moveFocus(entry.modelData.key, 1)
                        break
                    case Qt.Key_Home:
                        rail.focusEntry(rail.entries[0].key)
                        break
                    case Qt.Key_End:
                        rail.focusEntry(rail.entries[rail.entries.length - 1].key)
                        break
                    case Qt.Key_Return:
                    case Qt.Key_Enter:
                    case Qt.Key_Space:
                        rail.navigate(entry.modelData.key)
                        break
                    default:
                        return
                    }
                    event.accepted = true
                }
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
                Accessible.ignored: true
            }
            Text {
                text: rail.inflight > 0 ? ("进行中 " + rail.inflight) : "空闲"
                color: Theme.textFaint
                font.pixelSize: 11
                Layout.fillWidth: true
                //: Named and mirrored so the verification harness can read the state as
                //: *text*. Status must not be carried by colour alone, and the only way to
                //: check that is to look for the words.
                objectName: "rail-state"
                readonly property string a11yName: text
                readonly property string a11yRole: "StaticText"
                Accessible.role: Accessible.StaticText
                Accessible.name: text
            }
        }
    }
}
