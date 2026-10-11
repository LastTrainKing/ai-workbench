import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "../theme.js" as Theme

// Navigation rail.
//
// **Grouped, not stacked.** Fifteen destinations were one flat list, which reads as a
// developer console: everything is equally important, so nothing is. They are now five
// groups in the order a person works — what you are working on, what the AI is doing,
// what it knows, what is planned, and where the settings live.
//
// The entries and their order come from `theme.js`, which is also what numbers the
// `Ctrl+N` shortcuts. One list, so the hint printed here cannot disagree with the key that
// actually fires.
//
// Every entry is focusable and reachable with the arrow keys. The arrow keys were *claimed*
// here once before they worked — the comment said so, the entries were focusable, and
// nothing handled Up or Down. The handlers below are the feature;
// `verify_native_shell.py` presses the keys and checks where focus landed.
Rectangle {
    id: rail

    property string activePage: "dashboard"
    property int inflight: 0
    signal navigate(string page)

    color: Theme.bgElevated

    //: The flat order, which is what the arrow keys move through. A different list from
    //: `navGroups` only in shape — it is derived from it in `theme.js`.
    readonly property var entries: Theme.pages

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

    //: The entries, by key. **Registered by each entry rather than looked up through the
    //: Repeater.** The entries live inside a Repeater nested in another Repeater — one
    //: per group — and an id declared inside a delegate is scoped to that delegate, so
    //: `entryRepeater` resolved to a single group's instance from out here. Focus moved
    //: within the first group and nowhere else, which is exactly what the keyboard check
    //: reported: fifteen presses of Down, fifteen times `nav-dashboard`.
    property var _items: ({})

    function registerEntry(key, item) {
        rail._items[key] = item
    }

    function unregisterEntry(key) {
        delete rail._items[key]
    }

    function focusEntry(key) {
        var item = rail._items[key]
        if (item) {
            item.forceActiveFocus()
            return true
        }
        return false
    }

    Flickable {
        anchors.fill: parent
        contentWidth: width
        // The extra 40 is the status row pinned at the bottom. Without it the last entries
        // scroll *under* it: at the 640px minimum window height the rail is taller than the
        // space, and "图谱" ended up behind the word "空闲".
        contentHeight: railColumn.implicitHeight + Theme.space4 * 2 + 40
        clip: true
        boundsBehavior: Flickable.StopAtBounds

        ColumnLayout {
            id: railColumn
            x: Theme.space4
            y: Theme.space4
            width: parent.width - Theme.space4 * 2
            spacing: Theme.space1

            // ------------------------------------------------------------- brand --
            RowLayout {
                Layout.fillWidth: true
                Layout.bottomMargin: Theme.space4
                spacing: Theme.space2

                Rectangle {
                    width: 22; height: 22; radius: 6
                    color: Theme.accent
                    Accessible.ignored: true
                    Text {
                        anchors.centerIn: parent
                        text: "A"
                        color: "#04222a"
                        font.pixelSize: 13
                        font.bold: true
                    }
                }
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 0
                    Text {
                        text: "AI Workbench"
                        color: Theme.text
                        font.pixelSize: Theme.fontHeading
                        font.bold: true
                        Layout.fillWidth: true
                        Accessible.role: Accessible.Heading
                        Accessible.name: "AI Workbench"
                    }
                    Text {
                        text: "本地 AI 工作台"
                        color: Theme.textFaint
                        font.pixelSize: Theme.fontTiny
                        Accessible.ignored: true
                    }
                }
            }

            // ------------------------------------------------------------ groups --
            Repeater {
                model: Theme.navGroups

                delegate: ColumnLayout {
                    id: group
                    required property var modelData
                    Layout.fillWidth: true
                    spacing: Theme.space1

                    Text {
                        text: group.modelData.title
                        color: Theme.textFaint
                        font.pixelSize: Theme.fontTiny
                        font.bold: true
                        font.letterSpacing: 1.1
                        Layout.fillWidth: true
                        Layout.leftMargin: Theme.space3
                        Layout.topMargin: Theme.space3
                        Layout.bottomMargin: Theme.space1
                        Accessible.role: Accessible.StaticText
                        Accessible.name: group.modelData.title + "分组"
                    }

                    Repeater {
                        id: entryRepeater
                        model: group.modelData.entries

                        delegate: Rectangle {
                            id: entry
                            required property var modelData
                            required property int index
                            readonly property bool active: rail.activePage === modelData.key
                            //: Exposed so the rail can move focus by key rather than by
                            //: index. The delegate's own `index` is not addressable from
                            //: outside the Repeater.
                            readonly property string entryKey: modelData.key

                            Layout.fillWidth: true
                            Layout.preferredHeight: 34
                            radius: Theme.radiusSm
                            //: A selected row is a filled surface plus an accent edge.
                            //: Colour alone is not the signal: the label changes weight too,
                            //: and the accessibility tree carries `selected`.
                            color: active ? Theme.accentSoft
                                 : mouse.containsMouse ? Theme.bgCard
                                 : "transparent"

                            //: The identity the verification harness uses to find this
                            //: entry. Qt exposes `objectName` to `findChild`, which is how
                            //: a keyboard test can assert *which* entry received focus
                            //: rather than only that focus moved.
                            objectName: "nav-" + modelData.key

                            //: The accessible text, mirrored onto ordinary properties so a
                            //: test can read the value the screen reader will be given.
                            //: `Accessible.name` is an attached property with no Python
                            //: accessor; binding it to a property on the item means the
                            //: check reads the same string the accessibility tree carries.
                            readonly property string a11yName: modelData.label
                            readonly property string a11yRole: "PageTab"
                            readonly property string a11yDescription: active
                                ? (Theme.shortcutFor(modelData.key)
                                   ? Theme.shortcutFor(modelData.key) + "，当前页面"
                                   : "当前页面")
                                : (Theme.shortcutFor(modelData.key)
                                   ? "切换到" + modelData.label + "，" + Theme.shortcutFor(modelData.key)
                                   : "切换到" + modelData.label)

                            Rectangle {
                                visible: entry.active
                                width: 3
                                height: 18
                                radius: 1.5
                                color: Theme.accent
                                anchors.left: parent.left
                                anchors.leftMargin: 2
                                anchors.verticalCenter: parent.verticalCenter
                                Accessible.ignored: true
                            }

                            // Focus is visible, not implied. Without this a keyboard user
                            // cannot tell where they are, and "keyboard-friendly" is a
                            // claim rather than a feature.
                            Rectangle {
                                anchors.fill: parent
                                anchors.margins: -2
                                radius: Theme.radiusSm + 2
                                color: "transparent"
                                border.width: 2
                                border.color: Theme.borderFocus
                                visible: entry.activeFocus
                                Accessible.ignored: true
                            }

                            RowLayout {
                                anchors.fill: parent
                                anchors.leftMargin: Theme.space3
                                anchors.rightMargin: Theme.space2
                                spacing: Theme.space2

                                NavIcon {
                                    name: entry.modelData.icon
                                    size: 17
                                    color: entry.active ? Theme.accent : Theme.textFaint
                                    Layout.alignment: Qt.AlignVCenter
                                }
                                Text {
                                    text: entry.modelData.label
                                    color: entry.active ? Theme.text : Theme.textMuted
                                    font.pixelSize: Theme.fontBody
                                    font.bold: entry.active
                                    Layout.fillWidth: true
                                    Layout.alignment: Qt.AlignVCenter
                                    elide: Text.ElideRight
                                    Accessible.ignored: true
                                }
                                Text {
                                    text: Theme.shortcutFor(entry.modelData.key)
                                    color: Theme.textFaint
                                    font.pixelSize: Theme.fontTiny
                                    Layout.alignment: Qt.AlignVCenter
                                    Accessible.ignored: true
                                }
                            }

                            // The accessible node is the entry itself, not the labels
                            // inside it. A screen reader should hear "知识库, 页签, 当前页"
                            // once — not the title, then the shortcut, then the title again.
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
                            // `Keys.onHomePressed`, … The named signals differ between Qt
                            // versions — `Keys.onEndPressed` does not exist in this one,
                            // and the component failed to load with "Cannot assign to
                            // non-existent property", which took the whole window down
                            // with it.
                            Component.onCompleted: rail.registerEntry(entry.entryKey, entry)
                            Component.onDestruction: rail.unregisterEntry(entry.entryKey)

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
                }
            }
        }
    }

    // Honest state, not a spinner that means nothing: how many calls are outstanding.
    RowLayout {
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        anchors.margins: Theme.space4
        spacing: Theme.space2

        Rectangle {
            width: 7; height: 7; radius: 3.5
            color: rail.inflight > 0 ? Theme.accent : Theme.textFaint
            Accessible.ignored: true
        }
        Text {
            text: rail.inflight > 0 ? ("进行中 " + rail.inflight) : "空闲"
            color: Theme.textFaint
            font.pixelSize: Theme.fontTiny
            Layout.fillWidth: true
            //: Named and mirrored so the verification harness can read the state as *text*.
            //: Status must not be carried by colour alone, and the only way to check that
            //: is to look for the words.
            objectName: "rail-state"
            readonly property string a11yName: text
            readonly property string a11yRole: "StaticText"
            Accessible.role: Accessible.StaticText
            Accessible.name: text
        }
    }
}
