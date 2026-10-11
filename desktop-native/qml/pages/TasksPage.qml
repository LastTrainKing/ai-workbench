import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "../components"
import "../theme.js" as Theme

// A real business page: it reads the account's tasks, creates one, and completes one.
// The create and the complete are real writes to the database, so "the UI works" here
// means rows changed — not that a click produced a toast.
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
    property string newTitle: ""
    property string errorText: ""
    property bool busy: false

    function load() {
        var id = "tasks-" + Date.now()
        busy = true
        shell.request("任务列表", id, function (payload) {
            page.items = payload.items ?? []
            page.busy = false
        })
        bridge.loadTasks(id)
    }

    function create() {
        var title = newTitle.trim()
        if (title === "") {
            shell.toast("warn", "标题不能为空")
            return
        }
        var id = "create-" + Date.now()
        busy = true
        shell.request("新建任务", id, function (payload) {
            page.newTitle = ""
            page.busy = false
            shell.toast("info", "已创建：" + payload.title)
            page.load()
        })
        bridge.createTask(id, title, "MEDIUM")
    }

    function complete(taskId, title) {
        var id = "done-" + Date.now()
        busy = true
        shell.request("完成任务", id, function () {
            page.busy = false
            shell.toast("info", "已完成：" + title)
            page.load()
        })
        bridge.completeTask(id, taskId)
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
                text: "任务"
                color: Theme.text
                font.pixelSize: 24
                font.bold: true
                Layout.fillWidth: true
            }
            Text {
                text: page.items.length + " 项"
                color: Theme.textMuted
                font.pixelSize: 12
            }
        }

        Card {
            Layout.fillWidth: true
            title: "新建任务"
            subtitle: "写入数据库，然后重新读取列表"

            RowLayout {
                Layout.fillWidth: true
                spacing: 10

                TextField {
                    id: titleField
                    Layout.fillWidth: true
                    placeholderText: "任务标题"
                    text: page.newTitle
                    onTextChanged: page.newTitle = text
                    color: Theme.text
                    font.pixelSize: 14
                    Keys.onReturnPressed: page.create()

                    background: Rectangle {
                        radius: 8
                        color: Theme.surface
                        border.width: titleField.activeFocus ? 2 : 1
                        border.color: titleField.activeFocus ? Theme.borderFocus : Theme.border
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
                Layout.preferredHeight: 64
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
                        color: Theme.taskTone(modelData.status)
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
                            text: modelData.status + "  ·  " + (modelData.priority ?? "")
                            color: Theme.textFaint
                            font.pixelSize: 11
                        }
                    }

                    Button {
                        text: "完成"
                        visible: modelData.status !== "DONE"
                        onClicked: page.complete(modelData.id, modelData.title)
                    }
                }
            }
        }

        Text {
            visible: page.items.length === 0 && !page.busy
            text: "还没有任务。上面输入一个标题试试。"
            color: Theme.textFaint
            font.pixelSize: 13
        }

        Item { Layout.preferredHeight: 24 }
    }
}
