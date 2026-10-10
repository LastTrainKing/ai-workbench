import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "../components"
import "../theme.js" as Theme

// Projects. A real page against a real table: it reads the account's projects and creates
// one, and the create is a database write — "the UI works" here means a row appeared, not
// that a button produced a toast.
//
// Accessibility is part of the page rather than a later pass. Every control that a person
// operates has an `Accessible.name` and a `role`; the decorative rectangles are marked
// `ignored` so a screen reader does not announce them. `verify_native_shell.py` walks this
// page's accessible tree and fails if an interactive control has no name — because an
// unnamed button is a button a screen-reader user cannot find.
Flickable {
    id: page
    property var shell

    //: Raised when a row is activated. The shell turns it into a page change, so this
    //: page does not need to know the name of the page that shows a project.
    signal openProject(string projectId, string projectName)

    //: Set by the shell when another page sent the user here to create something.
    property bool autoCreate: false
    onAutoCreateChanged: if (autoCreate) create()

    contentWidth: width
    contentHeight: column.implicitHeight
    clip: true

    property var items: []
    property string newName: ""
    property string newDescription: ""
    property bool busy: false

    function load() {
        var id = "projects-" + Date.now()
        busy = true
        shell.request("项目列表", id, function (payload) {
            page.items = payload.items ?? []
            page.busy = false
        })
        bridge.loadProjects(id)
    }

    function create() {
        var name = newName.trim()
        if (name === "") {
            shell.toast("warn", "项目名不能为空")
            nameField.forceActiveFocus()
            return
        }
        var id = "project-create-" + Date.now()
        busy = true
        shell.request("新建项目", id, function (payload) {
            page.newName = ""
            page.newDescription = ""
            page.busy = false
            shell.toast("info", "已创建：" + (payload.item ? payload.item.name : name))
            page.load()
        })
        bridge.createProject(id, name, newDescription)
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
                text: "项目"
                color: Theme.text
                font.pixelSize: 24
                font.bold: true
                Layout.fillWidth: true
                Accessible.role: Accessible.Heading
                Accessible.name: "项目"
            }
            Text {
                text: page.items.length + " 个"
                color: Theme.textMuted
                font.pixelSize: 12
                Accessible.role: Accessible.StaticText
                Accessible.name: "共 " + page.items.length + " 个项目"
            }
        }

        Card {
            Layout.fillWidth: true
            title: "新建项目"
            subtitle: "写入数据库，然后重新读取列表"

            ColumnLayout {
                Layout.fillWidth: true
                spacing: 10

                TextField {
                    id: nameField
                    objectName: "project-name"
                    readonly property string a11yName: "项目名称"
                    readonly property string a11yRole: "EditableText"
                    Layout.fillWidth: true
                    placeholderText: "项目名称"
                    text: page.newName
                    onTextChanged: page.newName = text
                    color: Theme.text
                    font.pixelSize: 14
                    Accessible.role: Accessible.EditableText
                    Accessible.name: nameField.a11yName
                    Accessible.description: "新项目的名称，必填"
                    Keys.onReturnPressed: page.create()

                    background: Rectangle {
                        radius: 8
                        color: Theme.bgElevated
                        border.width: nameField.activeFocus ? 2 : 1
                        border.color: nameField.activeFocus ? Theme.borderFocus : Theme.border
                    }
                }

                TextField {
                    id: descriptionField
                    objectName: "project-description"
                    readonly property string a11yName: "项目描述"
                    readonly property string a11yRole: "EditableText"
                    Layout.fillWidth: true
                    placeholderText: "描述（可选）"
                    text: page.newDescription
                    onTextChanged: page.newDescription = text
                    color: Theme.text
                    font.pixelSize: 14
                    Accessible.role: Accessible.EditableText
                    Accessible.name: descriptionField.a11yName
                    Accessible.description: "可选"

                    background: Rectangle {
                        radius: 8
                        color: Theme.bgElevated
                        border.width: descriptionField.activeFocus ? 2 : 1
                        border.color: descriptionField.activeFocus ? Theme.borderFocus : Theme.border
                    }
                }

                RowLayout {
                    Layout.fillWidth: true
                    Item { Layout.fillWidth: true }
                    Button {
                        id: createButton
                        objectName: "project-create"
                        readonly property string a11yName: "创建项目"
                        readonly property string a11yRole: "Button"
                        text: page.busy ? "处理中…" : "创建项目"
                        enabled: !page.busy
                        onClicked: page.create()
                        Accessible.name: createButton.a11yName
                        Accessible.description: "把填写的名称和描述写入数据库"
                    }
                }
            }
        }

        Repeater {
            model: page.items

            delegate: Rectangle {
                required property var modelData
                Layout.fillWidth: true
                Layout.preferredHeight: 68
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
                        color: Theme.accent
                        Accessible.ignored: true
                    }

                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 2
                        Text {
                            text: modelData.name
                            color: Theme.text
                            font.pixelSize: 14
                            elide: Text.ElideRight
                            Layout.fillWidth: true
                            Accessible.ignored: true
                        }
                        Text {
                            text: modelData.description || "（无描述）"
                            color: Theme.textFaint
                            font.pixelSize: 11
                            elide: Text.ElideRight
                            Layout.fillWidth: true
                            Accessible.ignored: true
                        }
                    }

                    Text {
                        text: modelData.status ?? ""
                        color: Theme.textMuted
                        font.pixelSize: 11
                        Accessible.ignored: true
                    }
                }

                Accessible.role: Accessible.ListItem
                Accessible.name: modelData.name
                Accessible.description: (modelData.description || "无描述") + "，" + (modelData.status ?? "")

                MouseArea {
                    anchors.fill: parent
                    cursorShape: Qt.PointingHandCursor
                    onClicked: page.openProject(modelData.id, modelData.name)
                }
            }
        }

        Text {
            visible: page.items.length === 0 && !page.busy
            text: "还没有项目。上面填一个名称试试。"
            color: Theme.textFaint
            font.pixelSize: 13
            Accessible.role: Accessible.StaticText
            Accessible.name: text
        }

        Item { Layout.preferredHeight: 24 }
    }
}
