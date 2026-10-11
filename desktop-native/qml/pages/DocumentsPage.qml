import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import QtQuick.Dialogs
import "../components"
import "../theme.js" as Theme

// Documents inside one knowledge base, and the import that puts them there.
//
// The import goes through the product's real pipeline — save_upload → register_upload →
// ingest — so the file is hashed, deduplicated, parsed and embedded by the same code the
// HTTP route uses. A UI that wrote rows itself would prove nothing about the product.
Flickable {
    id: page
    property var shell
    property string kbId: ""
    property string kbName: ""

    contentWidth: width
    contentHeight: column.implicitHeight
    clip: true

    property var items: []
    property bool busy: false
    property string statusText: ""
    property string errorText: ""

    signal back()
    signal ask(string kbId, string kbName)

    function load() {
        if (kbId === "") return
        var id = "docs-" + Date.now()
        busy = true
        shell.request("资料列表", id, function (payload) {
            page.items = payload.items ?? []
            page.busy = false
        })
        bridge.loadDocuments(id, kbId)
    }

    function importPath(path) {
        if (path === "" || kbId === "") return
        var id = "imp-" + Date.now()
        busy = true
        statusText = "正在导入…"
        shell.request("导入资料", id, function (payload) {
            page.busy = false
            page.statusText = payload.duplicate
                ? payload.message
                : ("已导入：" + (payload.chunks ?? 0) + " 个片段，"
                   + (payload.embedded ?? 0) + " 个已向量化（" + (payload.model ?? "") + "）")
            page.load()
        })
        bridge.importDocument(id, kbId, path)
    }

    Connections {
        target: bridge
        function onProgress(id, message) {
            if (page.busy && message !== "") page.statusText = message
        }
    }

    function _firstLoad() {
        if (!shell || kbId === "") return
        load()
    }

    onKbIdChanged: _firstLoad()
    Component.onCompleted: _firstLoad()

    FileDialog {
        id: picker
        title: "选择要导入的资料"
        fileMode: FileDialog.OpenFile
        nameFilters: ["支持的文档 (*.md *.markdown *.txt *.pdf)", "所有文件 (*)"]
        onAccepted: {
            var path = decodeURIComponent(String(selectedFile).replace(/^file:\/\/\//, ""))
            page.importPath(path)
        }
    }

    ColumnLayout {
        id: column
        width: page.width
        spacing: 16

        RowLayout {
            Layout.fillWidth: true
            Button { text: "← 返回"; variant: "ghost"; onClicked: page.back() }
            Text {
                text: page.kbName === "" ? "资料" : page.kbName
                color: Theme.text
                font.pixelSize: 22
                font.bold: true
                Layout.fillWidth: true
                elide: Text.ElideRight
            }
            Button {
                text: "提问"
                    variant: "primary"
                enabled: page.items.length > 0
                onClicked: page.ask(page.kbId, page.kbName)
            }
        }

        Card {
            Layout.fillWidth: true
            title: "导入资料"
            subtitle: "走的是产品自己的上传流程：哈希、去重、解析、向量化"

            RowLayout {
                Layout.fillWidth: true
                spacing: 10
                Button {
                    text: page.busy ? "处理中…" : "选择文件…"
                    enabled: !page.busy
                    onClicked: picker.open()
                }
                Text {
                    Layout.fillWidth: true
                    text: page.statusText
                    color: Theme.accent
                    font.pixelSize: 12
                    elide: Text.ElideRight
                    wrapMode: Text.WrapAnywhere
                }
            }
        }

        Repeater {
            model: page.items

            delegate: Rectangle {
                required property var modelData
                Layout.fillWidth: true
                Layout.preferredHeight: 66
                radius: 10
                color: Theme.bgCard
                border.width: 1
                border.color: Theme.border

                RowLayout {
                    anchors.fill: parent
                    anchors.margins: 14
                    spacing: 12

                    Rectangle {
                        width: 4
                        Layout.fillHeight: true
                        radius: 2
                        color: modelData.status === "COMPLETED" ? Theme.success
                             : modelData.status === "FAILED" ? Theme.danger
                             : Theme.warn
                    }

                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 2
                        Text {
                            text: modelData.title
                            color: Theme.text
                            font.pixelSize: 14
                            elide: Text.ElideRight
                            Layout.fillWidth: true
                        }
                        Text {
                            color: Theme.textFaint
                            font.pixelSize: 11
                            text: modelData.status + "  ·  "
                                  + modelData.embedded + "/" + modelData.chunks + " 片段已向量化"
                                  + (modelData.error ? "  ·  " + modelData.error : "")
                            elide: Text.ElideRight
                            Layout.fillWidth: true
                        }
                    }
                }
            }
        }

        Text {
            visible: page.items.length === 0 && !page.busy
            text: "这个知识库还没有资料。用上面的「选择文件…」导入一份。"
            color: Theme.textFaint
            font.pixelSize: 13
        }

        Item { Layout.preferredHeight: 24 }
    }
}
