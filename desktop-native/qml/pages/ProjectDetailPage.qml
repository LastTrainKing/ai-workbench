import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "../components"
import "../theme.js" as Theme

// One project, and everything filed under it.
//
// Reached from the project list and from the workbench. The three lists are the three
// questions worth asking about a project: what is still to do, what has the agent already
// done, and what came out. All of it is filtered by `project_id` in the query.
Flickable {
    id: page
    property var shell

    // Set by the shell from `pageParams` when the page is loaded.
    property string projectId: ""
    property string projectName: ""

    contentWidth: width
    contentHeight: column.implicitHeight
    clip: true

    property var detail: null
    property string errorText: ""

    readonly property var project: detail ? (detail.project ?? {}) : {}
    readonly property var tasks: detail ? (detail.tasks ?? []) : []
    readonly property var runs: detail ? (detail.runs ?? []) : []
    readonly property var documents: detail ? (detail.documents ?? []) : []

    function load() {
        if (!projectId) return
        var id = "project-detail-" + Date.now()
        shell.request("项目详情", id, function (payload) {
            page.detail = payload
            page.errorText = ""
        })
        bridge.loadProjectDetail(id, projectId)
    }

    function statusWord(status) {
        switch (status) {
        case "ACTIVE":    return "进行中"
        case "PAUSED":    return "已暂停"
        case "COMPLETED": return "已完成"
        case "ARCHIVED":  return "已归档"
        case "SUCCEEDED": return "已完成"
        case "FAILED":    return "失败"
        case "CANCELLED": return "已取消"
        case "INCOMPLETE": return "未完成"
        case "EXECUTING": return "执行中"
        case "DONE":      return "已完成"
        case "PENDING":   return "待办"
        case "TODO":      return "待办"
        case "IN_PROGRESS": return "进行中"
        default:          return status || "未知"
        }
    }

    onProjectIdChanged: load()
    onShellChanged: if (shell && projectId) load()

    ColumnLayout {
        id: column
        width: page.width
        spacing: 16

        RowLayout {
            Layout.fillWidth: true
            Button {
                text: "← 项目"
                onClicked: page.shell.navigate("projects")
            }
            Text {
                Layout.fillWidth: true
                text: page.project.name || page.projectName || "项目"
                color: Theme.text
                font.pixelSize: 24
                font.bold: true
                elide: Text.ElideRight
            }
            Text {
                visible: page.detail !== null
                text: page.statusWord(page.project.status)
                color: Theme.accent
                font.pixelSize: 13
            }
        }

        Text {
            Layout.fillWidth: true
            visible: page.errorText !== ""
            text: page.errorText
            color: Theme.danger
            font.pixelSize: 13
            wrapMode: Text.WordWrap
        }

        Text {
            Layout.fillWidth: true
            visible: page.detail !== null && (page.project.description || "") !== ""
            text: page.project.description || ""
            color: Theme.textMuted
            font.pixelSize: 13
            wrapMode: Text.WordWrap
        }

        // -------------------------------------------------------------- quick actions --
        RowLayout {
            Layout.fillWidth: true
            spacing: 10
            Button {
                text: "让 Agent 继续这个项目"
                onClicked: {
                    shell.pageParams = { goal: "继续推进项目「" + (page.project.name || "") + "」：" }
                    shell.navigate("agent")
                }
            }
            Button {
                text: "新建任务"
                onClicked: {
                    shell.pageParams = { autoCreate: true, projectId: page.projectId }
                    shell.navigate("tasks")
                }
            }
            Button { text: "刷新"; onClicked: page.load() }
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: 16

            Card {
                Layout.fillWidth: true
                Layout.preferredWidth: 1
                Layout.alignment: Qt.AlignTop
                title: "任务"
                subtitle: page.tasks.length === 0 ? "这个项目还没有任务"
                          : (page.tasks.length + " 个任务")

                Repeater {
                    model: page.tasks
                    delegate: RowLayout {
                        required property var modelData
                        Layout.fillWidth: true
                        spacing: 8
                        Rectangle {
                            width: 6; height: 6; radius: 3
                            color: modelData.status === "DONE" ? Theme.success
                                  : modelData.priority === "high" ? Theme.warn : Theme.accent
                        }
                        Text {
                            Layout.fillWidth: true
                            text: modelData.title
                            color: Theme.text
                            font.pixelSize: 13
                            elide: Text.ElideRight
                        }
                        Text {
                            text: page.statusWord(modelData.status)
                            color: Theme.textFaint
                            font.pixelSize: 11
                        }
                    }
                }
            }

            Card {
                Layout.fillWidth: true
                Layout.preferredWidth: 1
                Layout.alignment: Qt.AlignTop
                title: "资料"
                subtitle: page.documents.length === 0 ? "这个项目还没有资料"
                          : (page.documents.length + " 份资料")

                Repeater {
                    model: page.documents
                    delegate: RowLayout {
                        required property var modelData
                        Layout.fillWidth: true
                        spacing: 8
                        Text {
                            Layout.fillWidth: true
                            text: modelData.title
                            color: Theme.text
                            font.pixelSize: 13
                            elide: Text.ElideRight
                        }
                        Text {
                            text: modelData.status ?? ""
                            color: Theme.textFaint
                            font.pixelSize: 11
                        }
                    }
                }

                Button {
                    Layout.fillWidth: true
                    text: "导入资料"
                    onClicked: {
                        shell.pageParams = { autoCreate: true }
                        shell.navigate("knowledge")
                    }
                }
            }
        }

        Card {
            Layout.fillWidth: true
            title: "Agent 运行记录"
            subtitle: page.runs.length === 0 ? "这个项目还没有 Agent 运行记录"
                      : (page.runs.length + " 次运行")

            Repeater {
                model: page.runs
                delegate: Rectangle {
                    required property var modelData
                    Layout.fillWidth: true
                    implicitHeight: 48
                    radius: 8
                    color: runMouse.containsMouse ? Theme.bgCardHover : "transparent"
                    border.width: runMouse.containsMouse ? 1 : 0
                    border.color: Theme.border

                    RowLayout {
                        anchors.fill: parent
                        anchors.leftMargin: 8
                        anchors.rightMargin: 8
                        spacing: 10
                        Text {
                            text: (modelData.run_id || "").slice(0, 8)
                            color: Theme.textFaint
                            font.family: "Consolas"
                            font.pixelSize: 11
                        }
                        Text {
                            Layout.fillWidth: true
                            text: page.statusWord(modelData.status)
                            color: Theme.text
                            font.pixelSize: 13
                        }
                        Text {
                            text: (modelData.tool_call_count ?? 0) + " 次工具调用"
                            color: Theme.textFaint
                            font.pixelSize: 11
                        }
                    }

                    MouseArea {
                        id: runMouse
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        Accessible.role: Accessible.Button
                        Accessible.name: "查看运行 " + (modelData.run_id || "").slice(0, 8)
                        onClicked: page.shell.navigate("runs")
                    }
                }
            }

            Button {
                Layout.fillWidth: true
                text: "查看全部运行记录"
                onClicked: page.shell.navigate("runs")
            }
        }

        Item { Layout.preferredHeight: 24 }
    }
}
