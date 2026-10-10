import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "../components"
import "../theme.js" as Theme

// Knowledge bases: what exists, how much is in each, and which model built it.
//
// The counts come from the database on every load, not from a cached field, so a base with
// documents but no vectors looks different from one that is ready. That distinction is the
// difference between "my import did nothing" and "my import worked", and it is the thing a
// list of names cannot tell you.
Flickable {
    id: page
    property var shell

    //: Set by the shell when another page sent the user here to create something.
    property bool autoCreate: false
    onAutoCreateChanged: if (autoCreate) create()

    contentWidth: width
    contentHeight: column.implicitHeight
    clip: true

    property var items: []
    property string newName: ""
    property string errorText: ""
    property bool busy: false

    signal openDocuments(string kbId, string kbName)

    function load() {
        var id = "kb-" + Date.now()
        busy = true
        shell.request("知识库列表", id, function (payload) {
            page.items = payload.items ?? []
            page.busy = false
        })
        bridge.loadKnowledgeBases(id)
    }

    function create() {
        var name = newName.trim()
        if (name === "") {
            shell.toast("warn", "名称不能为空")
            return
        }
        var id = "kb-new-" + Date.now()
        busy = true
        shell.request("新建知识库", id, function (payload) {
            page.newName = ""
            page.busy = false
            shell.toast("info", "已创建：" + payload.name)
            page.load()
        })
        bridge.createKnowledgeBase(id, name)
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
                text: "知识库"
                color: Theme.text
                font.pixelSize: 24
                font.bold: true
                Layout.fillWidth: true
            }
            Text {
                text: page.items.length + " 个"
                color: Theme.textMuted
                font.pixelSize: 12
            }
            Button { text: "刷新"; onClicked: page.load() }
        }

        Card {
            Layout.fillWidth: true
            title: "新建知识库"
            subtitle: "建好之后导入资料，就可以带着引用提问"

            RowLayout {
                Layout.fillWidth: true
                spacing: 10
                TextField {
                    id: nameField
                    Layout.fillWidth: true
                    placeholderText: "知识库名称"
                    text: page.newName
                    onTextChanged: page.newName = text
                    color: Theme.text
                    Keys.onReturnPressed: page.create()
                    background: Rectangle {
                        radius: 8
                        color: Theme.bgElevated
                        border.width: nameField.activeFocus ? 2 : 1
                        border.color: nameField.activeFocus ? Theme.borderFocus : Theme.border
                    }
                }
                Button {
                    text: page.busy ? "处理中…" : "创建"
                    enabled: !page.busy
                    onClicked: page.create()
                }
            }
        }

        Repeater {
            model: page.items

            delegate: Rectangle {
                required property var modelData
                Layout.fillWidth: true
                Layout.preferredHeight: 84
                radius: 10
                color: Theme.bgCard
                border.width: 1
                border.color: Theme.border

                RowLayout {
                    anchors.fill: parent
                    anchors.margins: 14
                    spacing: 14

                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 3
                        Text {
                            text: modelData.name
                            color: Theme.text
                            font.pixelSize: 15
                            font.bold: true
                            elide: Text.ElideRight
                            Layout.fillWidth: true
                        }
                        Text {
                            color: Theme.textMuted
                            font.pixelSize: 12
                            text: modelData.documents + " 份资料  ·  "
                                  + modelData.embedded_chunks + " 个已向量化片段"
                                  + (modelData.embedding_model
                                     ? "  ·  " + modelData.embedding_model : "  ·  尚未向量化")
                        }
                    }

                    Button {
                        text: "打开"
                        onClicked: page.openDocuments(modelData.id, modelData.name)
                    }
                }
            }
        }

        Text {
            visible: page.items.length === 0 && !page.busy
            text: "还没有知识库。上面创建一个，然后导入一份资料试试。"
            color: Theme.textFaint
            font.pixelSize: 13
        }

        Item { Layout.preferredHeight: 24 }
    }
}
