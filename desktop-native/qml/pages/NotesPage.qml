import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "../components"
import "../theme.js" as Theme

// Notes. The same shape as Projects — read a list, write one row — because the two pages
// are the same task, and a second layout for the same task is a second thing to keep
// working.
//
// The one difference is the editor: a note's body is multi-line, so the body field is a
// `TextArea` inside a `ScrollView` with `Tab` moving focus out rather than inserting a
// tab character. A text field that swallows Tab traps a keyboard user on the page.
Flickable {
    id: page
    property var shell

    contentWidth: width
    contentHeight: column.implicitHeight
    clip: true

    property var items: []
    property string newTitle: ""
    property string newContent: ""
    property bool busy: false

    function load() {
        var id = "notes-" + Date.now()
        busy = true
        shell.request("笔记列表", id, function (payload) {
            page.items = payload.items ?? []
            page.busy = false
        })
        bridge.loadNotes(id)
    }

    function create() {
        if (newTitle.trim() === "" && newContent.trim() === "") {
            shell.toast("warn", "标题和内容不能同时为空")
            titleField.forceActiveFocus()
            return
        }
        var id = "note-create-" + Date.now()
        busy = true
        shell.request("新建笔记", id, function (payload) {
            page.newTitle = ""
            page.newContent = ""
            page.busy = false
            shell.toast("info", "已保存：" + (payload.item ? payload.item.title : ""))
            page.load()
        })
        bridge.createNote(id, newTitle, newContent)
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
                text: "笔记"
                color: Theme.text
                font.pixelSize: 24
                font.bold: true
                Layout.fillWidth: true
                Accessible.role: Accessible.Heading
                Accessible.name: "笔记"
            }
            Text {
                text: page.items.length + " 条"
                color: Theme.textMuted
                font.pixelSize: 12
                Accessible.role: Accessible.StaticText
                Accessible.name: "共 " + page.items.length + " 条笔记"
            }
        }

        Card {
            Layout.fillWidth: true
            title: "写一条"
            subtitle: "只填内容也可以，标题会从内容里取"

            ColumnLayout {
                Layout.fillWidth: true
                spacing: 10

                TextField {
                    id: titleField
                    objectName: "note-title"
                    readonly property string a11yName: "笔记标题"
                    readonly property string a11yRole: "EditableText"
                    Layout.fillWidth: true
                    placeholderText: "标题"
                    text: page.newTitle
                    onTextChanged: page.newTitle = text
                    color: Theme.text
                    font.pixelSize: 14
                    Accessible.role: Accessible.EditableText
                    Accessible.name: titleField.a11yName
                    Accessible.description: "可以留空，留空时从内容中取前 60 个字"

                    background: Rectangle {
                        radius: 8
                        color: Theme.surface
                        border.width: titleField.activeFocus ? 2 : 1
                        border.color: titleField.activeFocus ? Theme.borderFocus : Theme.border
                    }
                }

                ScrollView {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 120
                    clip: true

                    TextArea {
                        id: contentField
                        objectName: "note-content"
                        readonly property string a11yName: "笔记正文"
                        readonly property string a11yRole: "EditableText"
                        placeholderText: "正文（Markdown）"
                        text: page.newContent
                        onTextChanged: page.newContent = text
                        color: Theme.text
                        font.pixelSize: 14
                        wrapMode: TextArea.Wrap
                        Accessible.role: Accessible.EditableText
                        Accessible.name: contentField.a11yName
                        Accessible.description: "支持 Markdown；Tab 键移动到下一个控件"

                        // `Tab` must leave the field. Qt's default in a TextArea is to
                        // insert a tab, which turns a keyboard user's only way forward into
                        // a way to type whitespace.
                        Keys.onTabPressed: nextItemInFocusChain(true).forceActiveFocus()
                        Keys.onBacktabPressed: nextItemInFocusChain(false).forceActiveFocus()
                    }
                }

                RowLayout {
                    Layout.fillWidth: true
                    Item { Layout.fillWidth: true }
                    Button {
                        id: saveButton
                        objectName: "note-save"
                        readonly property string a11yName: "保存笔记"
                        readonly property string a11yRole: "Button"
                        text: page.busy ? "保存中…" : "保存笔记"
                        enabled: !page.busy
                        onClicked: page.create()
                        Accessible.name: saveButton.a11yName
                        Accessible.description: "把标题和正文写入数据库"
                    }
                }
            }
        }

        Repeater {
            model: page.items

            delegate: Rectangle {
                required property var modelData
                Layout.fillWidth: true
                Layout.preferredHeight: Math.max(76, body.implicitHeight + 48)
                radius: 10
                color: Theme.bgCard
                border.width: 1
                border.color: Theme.border

                ColumnLayout {
                    id: body
                    anchors.fill: parent
                    anchors.margins: 14
                    spacing: 4

                    Text {
                        text: modelData.title
                        color: Theme.text
                        font.pixelSize: 14
                        font.bold: true
                        elide: Text.ElideRight
                        Layout.fillWidth: true
                        Accessible.ignored: true
                    }
                    Text {
                        text: modelData.content
                        color: Theme.textMuted
                        font.pixelSize: 12
                        wrapMode: Text.WordWrap
                        maximumLineCount: 3
                        elide: Text.ElideRight
                        Layout.fillWidth: true
                        Accessible.ignored: true
                    }
                }

                Accessible.role: Accessible.ListItem
                Accessible.name: modelData.title
                Accessible.description: modelData.content
            }
        }

        Text {
            visible: page.items.length === 0 && !page.busy
            text: "还没有笔记。上面写一条试试。"
            color: Theme.textFaint
            font.pixelSize: 13
            Accessible.role: Accessible.StaticText
            Accessible.name: text
        }

        Item { Layout.preferredHeight: 24 }
    }
}
