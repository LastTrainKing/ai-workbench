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
    //: 显示多少。由 shell 持有并持久化，这里只负责画和发信号。
    property bool expertMode: false
    signal navigate(string page)
    signal toggleExpertMode()

    color: Theme.bgElevated

    //: The flat order, which is what the arrow keys move through. A different list from
    //: `navGroups` only in shape — it is derived from it in `theme.js`.
    readonly property var entries: Theme.pagesFor(rail.expertMode)

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
        id: railScroll
        // **Ends above the status row, not behind it.** Padding the content was the first
        // attempt and it only moved the problem: at 800px the list is still taller than the
        // space, so the last entries scrolled under the row that says whether anything is
        // running — "设置" sat on top of "空闲". The list gets the space that is left and
        // scrolls inside it.
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.bottom: railMode.top
        anchors.bottomMargin: Theme.space2
        contentWidth: width
        contentHeight: railColumn.implicitHeight + Theme.space4 * 2
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
                //: 普通模式只列日常入口；开发细节在专家模式里，仍在同一个导航栏。
                model: Theme.navGroupsFor(rail.expertMode)

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
                        Layout.topMargin: Theme.space2
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
                            Layout.preferredHeight: 32
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
                                ? (Theme.shortcutFor(modelData.key, rail.expertMode)
                                   ? Theme.shortcutFor(modelData.key, rail.expertMode) + "，当前页面"
                                   : "当前页面")
                                : (Theme.shortcutFor(modelData.key, rail.expertMode)
                                   ? "切换到" + modelData.label + "，" + Theme.shortcutFor(modelData.key, rail.expertMode)
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
                                    text: Theme.shortcutFor(entry.modelData.key, rail.expertMode)
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

    // **The rail is taller than the window now, and this is what says so.**
    //
    // Nineteen entries and six group headings do not fit 800px, so the list scrolls —
    // which the `Flickable` above was built for. What it did not do was *look* like it
    // scrolled: the row at the boundary was cut through the middle of its text and sat
    // directly on the status row below, so the last entry read as broken rather than as
    // "there is more". A fade costs one rectangle and turns a defect into a cue.
    Rectangle {
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: railScroll.bottom
        height: Theme.space5
        visible: railScroll.contentHeight > railScroll.height
        gradient: Gradient {
            GradientStop { position: 0.0; color: Qt.rgba(0, 0, 0, 0) }
            GradientStop { position: 1.0; color: Theme.bg }
        }
        Accessible.ignored: true
    }

    // 显示模式。**两个都写出来**，而不是一个开关：开关只说明"现在是哪边"，用户得先
    // 知道另一边叫什么，才能决定要不要按。两种模式各有名字，就把名字给出来。
    RowLayout {
        id: railMode
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: railStatus.top
        anchors.leftMargin: Theme.space4
        anchors.rightMargin: Theme.space4
        anchors.bottomMargin: Theme.space2
        spacing: Theme.space1

        Repeater {
            model: [
                { label: "普通", value: false, hint: "只看任务和结果" },
                { label: "专家", value: true, hint: "加上步骤、工具调用、token 和延迟" }
            ]
            delegate: Rectangle {
                required property var modelData

                Layout.fillWidth: true
                implicitHeight: 24
                radius: Theme.radiusSm
                color: rail.expertMode === modelData.value
                       ? Theme.bgCardHover
                       : (modeMouse.containsMouse ? Theme.bgCard : "transparent")
                border.width: 1
                border.color: rail.expertMode === modelData.value
                              ? Theme.accent : Theme.divider

                Text {
                    anchors.centerIn: parent
                    text: modelData.label
                    color: rail.expertMode === modelData.value ? Theme.text : Theme.textFaint
                    font.pixelSize: Theme.fontTiny
                }
                MouseArea {
                    id: modeMouse
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    Accessible.role: Accessible.RadioButton
                    Accessible.name: modelData.label + "模式：" + modelData.hint
                    onClicked: {
                        // 只在真的换了才发信号，否则点当前那一档会白白写一次磁盘。
                        if (rail.expertMode !== modelData.value) rail.toggleExpertMode()
                    }
                }
            }
        }
    }

    // Honest state, not a spinner that means nothing: how many calls are outstanding.
    RowLayout {
        id: railStatus
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
