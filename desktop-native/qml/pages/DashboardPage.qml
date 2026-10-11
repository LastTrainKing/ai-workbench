import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "../components"
import "../theme.js" as Theme

// The first thing the application shows.
//
// What was here before printed the dashboard payload as JSON and explained that the page
// talked to `StatsService` without an HTTP hop. Both statements were true and neither
// belonged on the home screen: a person opening the product wants to know what needs doing
// today, not how the page is wired.
//
// Everything below is still a real query — `StatsService.dashboard` for the counts and the
// provider state, `ProjectRepository` for the recent projects. Nothing is a placeholder, and
// nothing explains itself to a developer.
Flickable {
    id: page
    property var shell

    contentWidth: width
    contentHeight: column.implicitHeight
    clip: true

    // `summary`, not `data`: `data` is a built-in `Item` property — the default property
    // that owns the item's children — and declaring it again shadows the engine's own
    // member.
    property var summary: null
    property var projects: []
    property string errorText: ""

    //: What a panel says while its data is on the way. While the local stack is still
    //: coming up, "读取中…" is not true — the request has not been sent, it is waiting for
    //: a database to finish starting. Saying so is the difference between a screen that
    //: looks stuck and one that is visibly getting ready.
    readonly property string waitingText:
        (bridge.startupState === "starting" || bridge.startupState === "idle")
        ? "等待本地环境…" : "读取中…"

    function load() {
        var id = "workbench-" + Date.now()
        shell.request("工作台", id, function (payload) {
            page.summary = payload
            page.errorText = ""
        })
        bridge.loadDashboard(id)

        var pid = "workbench-projects-" + Date.now()
        shell.request("最近项目", pid, function (payload) {
            // `payload` itself can be null when the call failed; `??` guards the field
            // and not the object, and the error then reads as a broken binding.
            page.projects = (payload && payload.items ? payload.items : []).slice(0, 4)
        })
        bridge.loadProjects(pid)
    }

    function go(name, params) {
        if (params) shell.pageParams = params
        shell.activePage = name
    }

    // The quick actions take the user to the page that owns the action *and* ask it to open
    // its own dialog. A button that only navigates makes the user find the same button again.
    function createTask() { go("tasks", { autoCreate: true }) }
    function createProject() { go("projects", { autoCreate: true }) }
    function importDocument() { go("knowledge", { autoCreate: true }) }
    function startChat() { go("conversation") }

    property bool _loaded: false

    function _firstLoad() {
        if (_loaded || !shell) return
        _loaded = true
        load()
    }

    onShellChanged: _firstLoad()
    Component.onCompleted: _firstLoad()

    readonly property var tasks: summary ? (summary.tasks ?? {}) : {}
    readonly property var knowledge: summary ? (summary.knowledge ?? {}) : {}
    readonly property var llm: (summary && summary.providers) ? (summary.providers.llm ?? {}) : {}
    readonly property var embedding: (summary && summary.providers) ? (summary.providers.embedding ?? {}) : {}

    ColumnLayout {
        id: column
        width: page.width
        spacing: 16

        RowLayout {
            Layout.fillWidth: true
            Text {
                text: "工作台"
                color: Theme.text
                font.pixelSize: 24
                font.bold: true
                Layout.fillWidth: true
            }
            Text {
                text: page.summary ? "已更新" : page.waitingText
                color: Theme.textFaint
                font.pixelSize: 12
            }
            Button { text: "刷新"; onClicked: page.load() }
        }

        Text {
            visible: page.errorText !== ""
            text: page.errorText
            color: Theme.danger
            font.pixelSize: 13
            Layout.fillWidth: true
            wrapMode: Text.WordWrap
        }

        // ------------------------------------------------------------ quick actions --
        RowLayout {
            Layout.fillWidth: true
            spacing: 12
            Repeater {
                model: [
                    { label: "新建任务", hint: "写下一件要做的事", act: "task" },
                    { label: "新建项目", hint: "把相关的任务放在一起", act: "project" },
                    { label: "导入资料", hint: "让 AI 读你的文件", act: "import" },
                    { label: "开始对话", hint: "直接提问", act: "chat" }
                ]
                delegate: Rectangle {
                    required property var modelData
                    Layout.fillWidth: true
                    Layout.preferredHeight: 78
                    radius: 12
                    color: mouse.containsMouse ? Theme.bgCardHover : Theme.bgCard
                    border.width: 1
                    border.color: Theme.border

                    ColumnLayout {
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.verticalCenter: parent.verticalCenter
                        anchors.leftMargin: 14
                        anchors.rightMargin: 14
                        spacing: 3
                        Text {
                            text: modelData.label
                            color: Theme.text
                            font.pixelSize: 15
                            font.bold: true
                        }
                        Text {
                            text: modelData.hint
                            color: Theme.textFaint
                            font.pixelSize: 11
                            Layout.fillWidth: true
                            elide: Text.ElideRight
                        }
                    }

                    MouseArea {
                        id: mouse
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        Accessible.role: Accessible.Button
                        Accessible.name: modelData.label
                        Accessible.description: modelData.hint
                        onClicked: {
                            switch (modelData.act) {
                            case "task":    page.createTask(); break
                            case "project": page.createProject(); break
                            case "import":  page.importDocument(); break
                            case "chat":    page.startChat(); break
                            }
                        }
                    }
                }
            }
        }

        // ---------------------------------------------------------------- two columns --
        RowLayout {
            Layout.fillWidth: true
            spacing: 16

            // ------------------------------------------------------------ 今日任务 --
            Card {
                Layout.fillWidth: true
                Layout.preferredWidth: 1
                Layout.alignment: Qt.AlignTop
                title: "今日任务"
                subtitle: page.summary
                          ? ((page.tasks.due_today ?? []).length + " 件今天到期，"
                             + (page.tasks.high_priority ?? []).length + " 件高优先级")
                          : page.waitingText

                Text {
                    Layout.fillWidth: true
                    visible: page.summary !== null && (page.tasks.due_today ?? []).length === 0
                             && (page.tasks.high_priority ?? []).length === 0
                    color: Theme.textFaint
                    font.pixelSize: 13
                    wrapMode: Text.WordWrap
                    text: (page.tasks.total ?? 0) === 0
                          ? "还没有任务。用上面的「新建任务」写第一件。"
                          : "今天没有到期的任务，也没有高优先级的。"
                }

                Repeater {
                    model: page.summary
                           ? (page.tasks.due_today ?? []).concat(page.tasks.high_priority ?? []).slice(0, 6)
                           : []
                    delegate: RowLayout {
                        required property var modelData
                        Layout.fillWidth: true
                        spacing: 8
                        Rectangle {
                            width: 6; height: 6; radius: 3
                            color: (modelData.priority === "high" || modelData.priority === "urgent")
                                   ? Theme.warn : Theme.accent
                        }
                        Text {
                            text: modelData.title
                            color: Theme.text
                            font.pixelSize: 13
                            Layout.fillWidth: true
                            elide: Text.ElideRight
                        }
                        Text {
                            text: modelData.status ?? ""
                            color: Theme.textFaint
                            font.pixelSize: 11
                        }
                    }
                }

                RowLayout {
                    Layout.fillWidth: true
                    spacing: 18
                    visible: page.summary !== null
                    Repeater {
                        model: page.summary ? [
                            { n: page.tasks.open ?? 0, label: "进行中" },
                            { n: page.tasks.overdue ?? 0, label: "已逾期" },
                            { n: page.tasks.completed_this_week ?? 0, label: "本周完成" }
                        ] : []
                        delegate: ColumnLayout {
                            required property var modelData
                            spacing: 2
                            Text {
                                text: modelData.n
                                color: modelData.label === "已逾期" && modelData.n > 0
                                       ? Theme.danger : Theme.text
                                font.pixelSize: 20
                                font.bold: true
                            }
                            Text {
                                text: modelData.label
                                color: Theme.textFaint
                                font.pixelSize: 11
                            }
                        }
                    }
                }

                Button {
                    text: "查看全部任务"
                    Layout.fillWidth: true
                    onClicked: page.go("tasks")
                }
            }

            // ---------------------------------------------------------- 最近项目 --
            Card {
                Layout.fillWidth: true
                Layout.preferredWidth: 1
                Layout.alignment: Qt.AlignTop
                title: "最近项目"
                subtitle: page.summary
                          ? ((page.summary.workspace ?? {}).projects ?? 0) + " 个进行中的项目"
                          : page.waitingText

                Text {
                    Layout.fillWidth: true
                    visible: page.projects.length === 0
                    color: Theme.textFaint
                    font.pixelSize: 13
                    wrapMode: Text.WordWrap
                    text: "还没有项目。项目把相关的任务和产物放在一起。"
                }

                Repeater {
                    model: page.projects
                    delegate: Rectangle {
                        required property var modelData
                        Layout.fillWidth: true
                        implicitHeight: 44
                        radius: 8
                        color: rowMouse.containsMouse ? Theme.bgCardHover : "transparent"
                        border.width: rowMouse.containsMouse ? 1 : 0
                        border.color: Theme.border

                        RowLayout {
                            anchors.fill: parent
                            anchors.leftMargin: 8
                            anchors.rightMargin: 8
                            spacing: 8
                            Text {
                                text: modelData.name
                                color: Theme.text
                                font.pixelSize: 13
                                Layout.fillWidth: true
                                elide: Text.ElideRight
                            }
                            Text {
                                text: modelData.status ?? ""
                                color: Theme.textFaint
                                font.pixelSize: 11
                            }
                        }

                        MouseArea {
                            id: rowMouse
                            anchors.fill: parent
                            hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            Accessible.role: Accessible.Button
                            Accessible.name: "打开项目 " + modelData.name
                            onClicked: page.go("project", { projectId: modelData.id,
                                                            projectName: modelData.name })
                        }
                    }
                }

                Button {
                    text: "查看全部项目"
                    Layout.fillWidth: true
                    onClicked: page.go("projects")
                }
            }
        }

        // ---------------------------------------------------------------- two columns --
        RowLayout {
            Layout.fillWidth: true
            spacing: 16

            // ---------------------------------------------------------- 知识库状态 --
            Card {
                Layout.fillWidth: true
                Layout.preferredWidth: 1
                Layout.alignment: Qt.AlignTop
                title: "知识库"
                subtitle: page.summary
                          ? ((page.knowledge.knowledge_bases ?? 0) + " 个知识库，"
                             + (page.knowledge.documents ?? 0) + " 份资料")
                          : page.waitingText

                GridLayout {
                    Layout.fillWidth: true
                    columns: 2
                    columnSpacing: 16
                    rowSpacing: 8
                    Repeater {
                        model: page.summary ? [
                            { label: "文档片段", value: page.knowledge.chunks ?? 0 },
                            { label: "已提取概念", value: page.knowledge.concepts ?? 0 }
                        ] : []
                        delegate: ColumnLayout {
                            required property var modelData
                            spacing: 2
                            Text {
                                text: modelData.value
                                color: Theme.accent
                                font.pixelSize: 20
                                font.bold: true
                            }
                            Text {
                                text: modelData.label
                                color: Theme.textFaint
                                font.pixelSize: 11
                            }
                        }
                    }
                }

                Text {
                    Layout.fillWidth: true
                    visible: page.summary !== null && (page.knowledge.documents ?? 0) === 0
                    color: Theme.textFaint
                    font.pixelSize: 13
                    wrapMode: Text.WordWrap
                    text: "还没有资料。导入一份文件，AI 就能引用它回答问题。"
                }

                Button {
                    text: "打开知识库"
                    Layout.fillWidth: true
                    onClicked: page.go("knowledge")
                }
            }

            // ---------------------------------------------------------- AI 状态 --
            Card {
                Layout.fillWidth: true
                Layout.preferredWidth: 1
                Layout.alignment: Qt.AlignTop
                title: "AI 服务"
                subtitle: page.summary ? (page.llm.model || "未配置") : page.waitingText

                RowLayout {
                    Layout.fillWidth: true
                    spacing: 8
                    Rectangle {
                        width: 8; height: 8; radius: 4
                        color: page.llm.api_key_set ? Theme.success : Theme.warn
                    }
                    Text {
                        Layout.fillWidth: true
                        wrapMode: Text.WordWrap
                        color: Theme.text
                        font.pixelSize: 13
                        text: page.summary
                              ? (page.llm.api_key_set
                                 ? "已配置 API Key，可以调用云端模型。"
                                 : "还没有填 API Key，当前用内置的离线引擎。功能可用，"
                                   + "但回答质量不如云端模型。")
                              : page.waitingText
                    }
                }

                Text {
                    Layout.fillWidth: true
                    visible: page.summary !== null && page.embedding.degraded === true
                    wrapMode: Text.WordWrap
                    color: Theme.textFaint
                    font.pixelSize: 11
                    text: "语义检索未启用（" + (page.embedding.reason || "没有 embedding 服务")
                          + "）。关键词检索正常。"
                }

                Button {
                    text: page.llm.api_key_set ? "AI 设置" : "填写 API Key"
                    Layout.fillWidth: true
                    onClicked: page.go("settings")
                }
            }
        }

        Item { Layout.preferredHeight: 24 }
    }
}
