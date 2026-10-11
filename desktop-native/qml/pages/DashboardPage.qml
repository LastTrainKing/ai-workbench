import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "../components"
import "../theme.js" as Theme

// The first thing the application shows, and the screen the whole redesign is about.
//
// It answers three questions, in that order, and nothing else:
//
//   1. What can I ask the AI to do?      → the input at the top
//   2. What am I in the middle of?       → recent projects, tasks and runs
//   3. How do I carry on where I stopped? → "继续" on an unfinished run, which hands the
//                                           original goal back to the Agent
//
// Everything below is a real query — `StatsService.dashboard` for the counts and the
// provider state, `ProjectRepository` for the projects, `loadRuns` for the history. No
// panel invents a number to fill space.
//
// **Three requests, three states.** They used to be two requests and one string,
// "读取中…", which stayed on screen after a failure — so a broken panel and a slow one
// looked the same, and neither ever offered a way out. Each panel now reports its own
// loading / empty / error and can retry on its own.
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
    property string dashState: "loading"
    property string dashError: ""
    property string dashErrorHint: ""
    property string dashErrorDetail: ""

    property var projects: []
    property string projectsState: "loading"
    property string projectsError: ""
    property string projectsErrorHint: ""
    property string projectsErrorDetail: ""

    property var runs: []
    property int runsTotal: 0
    property string runsState: "loading"
    property string runsError: ""
    property string runsErrorHint: ""
    property string runsErrorDetail: ""

    //: What each panel should draw, given both the call's state and whether the call
    //: returned anything. "Loaded and empty" and "still loading" are different sentences
    //: and the panel has to be able to tell them apart.
    readonly property string projectsViewState:
        projectsState === "ready" && projects.length === 0 ? "empty" : projectsState
    readonly property string todoViewState:
        dashState === "ready" && todo.length === 0 ? "empty" : dashState
    readonly property string runsViewState:
        runsState === "ready" && runs.length === 0 ? "empty" : runsState
    readonly property string knowledgeViewState:
        dashState === "ready" && (knowledge.documents ?? 0) === 0 ? "empty" : dashState

    //: What the user is about to ask the AI.
    property string goalText: ""

    //: What a panel says while its data is on the way. While the local stack is still
    //: coming up, "读取中…" is not true — the request has not been sent, it is waiting for
    //: a database to finish starting.
    readonly property string busyText:
        (bridge && (bridge.startupState === "starting" || bridge.startupState === "idle"))
        ? "等待本地环境…" : "读取中…"

    readonly property var tasks: summary ? (summary.tasks ?? {}) : {}
    readonly property var knowledge: summary ? (summary.knowledge ?? {}) : {}
    readonly property var llm: (summary && summary.providers) ? (summary.providers.llm ?? {}) : {}
    readonly property var embedding: (summary && summary.providers) ? (summary.providers.embedding ?? {}) : {}

    //: Due today or flagged high priority, in that order, without duplicates.
    readonly property var todo: {
        if (!summary) return []
        var seen = {}
        var out = []
        var source = (tasks.due_today ?? []).concat(tasks.high_priority ?? [])
        for (var i = 0; i < source.length; i++) {
            var t = source[i]
            if (t && t.id && !seen[t.id]) { seen[t.id] = true; out.push(t) }
        }
        return out
    }

    //: Runs that stopped without finishing *and* know what they were asked to do. The
    //: second half matters: without the goal there is nothing to continue, and a button
    //: that cannot do what it says is worse than no button.
    readonly property var resumable: {
        var out = []
        for (var i = 0; i < runs.length; i++) {
            var r = runs[i]
            if (!r || !r.goal) continue
            if (r.status === "INCOMPLETE" || r.status === "INTERRUPTED" || r.status === "FAILED") {
                out.push(r)
            }
        }
        return out
    }

    function ago(iso) {
        if (!iso) return ""
        var then = Date.parse(iso)
        if (isNaN(then)) return ""
        var minutes = Math.round((Date.now() - then) / 60000)
        if (minutes < 1) return "刚刚"
        if (minutes < 60) return minutes + " 分钟前"
        var hours = Math.round(minutes / 60)
        if (hours < 24) return hours + " 小时前"
        return Math.round(hours / 24) + " 天前"
    }

    // ------------------------------------------------------------------ loading --
    function load() {
        loadSummary()
        loadProjects()
        loadRuns()
    }

    function loadSummary() {
        page.dashState = "loading"
        page.dashError = ""
        var id = "wb-summary-" + Date.now()
        shell.request("工作台", id, function (payload) {
            if (!payload) {
                page.dashState = "error"
                page.dashError = "服务没有返回数据"
                return
            }
            page.summary = payload
            page.dashState = "ready"
        }, function (message, code, human) {
            // `message` is already a sentence in the reader's terms — the shell ran it
            // through `Errors.human`. The hint and the exception come along too, so the
            // panel can offer the next step and keep the raw text one toggle away.
            page.dashState = "error"
            page.dashError = message
            page.dashErrorHint = human ? human.hint : ""
            page.dashErrorDetail = human ? human.raw : ""
        })
        bridge.loadDashboard(id)
    }

    function loadProjects() {
        page.projectsState = "loading"
        page.projectsError = ""
        var id = "wb-projects-" + Date.now()
        shell.request("最近项目", id, function (payload) {
            // `payload` itself can be null when the call failed; `??` guards the field
            // and not the object, and the error then reads as a broken binding.
            page.projects = (payload && payload.items ? payload.items : []).slice(0, 5)
            page.projectsState = "ready"
        }, function (message, code, human) {
            // `message` is already a sentence in the reader's terms — the shell ran it
            // through `Errors.human`. The hint and the exception come along too, so the
            // panel can offer the next step and keep the raw text one toggle away.
            page.projectsState = "error"
            page.projectsError = message
            page.projectsErrorHint = human ? human.hint : ""
            page.projectsErrorDetail = human ? human.raw : ""
        })
        bridge.loadProjects(id)
    }

    function loadRuns() {
        page.runsState = "loading"
        page.runsError = ""
        var id = "wb-runs-" + Date.now()
        shell.request("最近运行", id, function (payload) {
            page.runs = (payload && payload.items ? payload.items : []).slice(0, 8)
            page.runsTotal = (payload && payload.total) ? payload.total : page.runs.length
            page.runsState = "ready"
        }, function (message, code, human) {
            // `message` is already a sentence in the reader's terms — the shell ran it
            // through `Errors.human`. The hint and the exception come along too, so the
            // panel can offer the next step and keep the raw text one toggle away.
            page.runsState = "error"
            page.runsError = message
            page.runsErrorHint = human ? human.hint : ""
            page.runsErrorDetail = human ? human.raw : ""
        })
        bridge.loadRuns(id, 20)
    }

    // ---------------------------------------------------------------- navigation --
    function go(name, params) {
        // The shell's one navigation door: it clears stale page parameters and marks the
        // requests this page still has in flight as belonging to a page that is leaving.
        shell.navigate(name, params)
    }

    // The quick actions take the user to the page that owns the action *and* ask it to open
    // its own dialog. A button that only navigates makes the user find the same button again.
    function createTask() { go("tasks", { autoCreate: true }) }
    function createProject() { go("projects", { autoCreate: true }) }
    function importDocument() { go("knowledge", { autoCreate: true }) }

    //: Hand the goal to the Agent and let it run. The Agent page is the only thing in the
    //: product that executes a task; this screen does not grow a second executor.
    function executeGoal() {
        var text = goalArea.text.trim()
        if (text === "") {
            shell.toast("warn", "先写下你想让 AI 做什么")
            goalArea.forceActiveFocus()
            return
        }
        go("agent", { goal: text, autoStart: true })
    }

    //: The same goal, but as a question — the multi-turn conversation, which answers
    //: rather than does.
    function askGoal() {
        var text = goalArea.text.trim()
        if (text === "") {
            shell.toast("warn", "先写下你想问什么")
            goalArea.forceActiveFocus()
            return
        }
        go("conversation", { prompt: text })
    }

    function resumeRun(run) {
        go("agent", { goal: run.goal, autoStart: true })
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
        spacing: Theme.space4

        // ================================================================ header ==
        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.space3

            Text {
                text: "工作台"
                color: Theme.text
                font.pixelSize: Theme.fontDisplay
                font.bold: true
                Layout.fillWidth: true
            }
            Text {
                text: page.dashState === "ready" ? "已更新"
                     : page.dashState === "error" ? "读取失败" : page.busyText
                color: Theme.textFaint
                font.pixelSize: Theme.fontSmall
            }
            Button {
                text: "刷新"
                variant: "ghost"
                onClicked: page.load()
            }
        }

        // ============================================================ AI 输入区 ==
        //
        // The first thing on the screen, because it is the first thing the product is for.
        // It was four small tiles at the bottom of the page before, below a panel of
        // counters — which answered "what happened" and not "what do I do now".
        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: hero.implicitHeight + Theme.space5 * 2
            radius: Theme.radiusLg
            color: Theme.bgCard

            ColumnLayout {
                id: hero
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                anchors.margins: Theme.space5
                spacing: Theme.space3

                Text {
                    text: "让 AI 做什么"
                    color: Theme.text
                    font.pixelSize: Theme.fontTitle
                    font.bold: true
                    Accessible.role: Accessible.Heading
                    Accessible.name: "让 AI 做什么"
                }

                Rectangle {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 84
                    radius: Theme.radiusMd
                    color: Theme.bgInput
                    border.width: 1
                    border.color: goalArea.activeFocus ? Theme.borderFocus : Theme.divider

                    TextArea {
                        id: goalArea
                        objectName: "goal-input"
                        anchors.fill: parent
                        anchors.margins: 10
                        placeholderText: "例如：根据知识库里的资料做一份产品介绍 PPT"
                        color: Theme.text
                        placeholderTextColor: Theme.textFaint
                        font.pixelSize: Theme.fontBody
                        wrapMode: TextArea.Wrap
                        background: null
                        selectByMouse: true
                        Accessible.name: "描述你想让 AI 完成的事"
                        Keys.onPressed: function (event) {
                            // Ctrl+Enter submits. Enter alone has to keep making newlines:
                            // a goal is often more than one line.
                            if ((event.key === Qt.Key_Return || event.key === Qt.Key_Enter)
                                    && (event.modifiers & Qt.ControlModifier)) {
                                page.executeGoal()
                                event.accepted = true
                            }
                        }
                    }
                }

                Flow {
                    Layout.fillWidth: true
                    spacing: Theme.space2

                    Repeater {
                        model: [
                            "根据资料做一份 PPT",
                            "总结项目里的文档",
                            "写一个代码项目",
                            "根据知识库回答问题"
                        ]
                        delegate: Rectangle {
                            required property var modelData
                            height: 26
                            width: chipLabel.implicitWidth + Theme.space3 * 2
                            radius: 13
                            color: chipMouse.containsMouse ? Theme.bgCardHover : "transparent"
                            border.width: 1
                            border.color: Theme.divider

                            Text {
                                id: chipLabel
                                anchors.centerIn: parent
                                text: modelData
                                color: Theme.textMuted
                                font.pixelSize: Theme.fontSmall
                            }
                            MouseArea {
                                id: chipMouse
                                anchors.fill: parent
                                hoverEnabled: true
                                cursorShape: Qt.PointingHandCursor
                                Accessible.role: Accessible.Button
                                Accessible.name: "示例：" + modelData
                                onClicked: {
                                    goalArea.text = modelData
                                    goalArea.forceActiveFocus()
                                }
                            }
                        }
                    }
                }

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme.space2

                    Text {
                        Layout.fillWidth: true
                        text: "Ctrl+Enter 执行"
                        color: Theme.textFaint
                        font.pixelSize: Theme.fontTiny
                    }
                    Button {
                        objectName: "hero-ask"
                        text: "直接提问"
                        variant: "secondary"
                        onClicked: page.askGoal()
                    }
                    Button {
                        objectName: "hero-execute"
                        text: "交给 Agent 执行"
                        variant: "primary"
                        onClicked: page.executeGoal()
                    }
                }
            }
        }

        // =========================================================== 最近工作 ==
        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.space4

            // ------------------------------------------------------ 最近项目 --
            Card {
                Layout.fillWidth: true
                Layout.preferredWidth: 1
                Layout.alignment: Qt.AlignTop
                title: "最近项目"
                // `page.summary` guarded: it is null until the call comes back, and
                // `page.summary.workspace` on null is "Value is null and could not be
                // converted to an object" — the error this pass is about, in miniature.
                subtitle: page.projectsState === "error" ? ""
                          : (page.projectsState === "loading" ? page.busyText
                          : ((page.summary ? (page.summary.workspace ?? {}).projects : 0) ?? 0)
                            + " 个项目")

                StateBlock {
                    Layout.fillWidth: true
                    state: page.projectsViewState
                    busyText: page.busyText
                    errorText: page.projectsError
                    errorHint: page.projectsErrorHint
                    errorDetail: page.projectsErrorDetail
                    emptyText: "还没有项目。项目把相关的任务和产物放在一起。"
                    onRetry: page.loadProjects()
                }

                Repeater {
                    model: page.projects
                    delegate: Rectangle {
                        required property var modelData
                        Layout.fillWidth: true
                        implicitHeight: 38
                        radius: Theme.radiusSm
                        color: rowMouse.containsMouse ? Theme.bgCardHover : "transparent"

                        RowLayout {
                            anchors.fill: parent
                            anchors.leftMargin: Theme.space2
                            anchors.rightMargin: Theme.space2
                            spacing: Theme.space2
                            Text {
                                text: modelData.name
                                color: Theme.text
                                font.pixelSize: Theme.fontBody
                                Layout.fillWidth: true
                                elide: Text.ElideRight
                            }
                            Text {
                                text: modelData.status ?? ""
                                color: Theme.textFaint
                                font.pixelSize: Theme.fontTiny
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

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme.space2
                    visible: page.projectsState === "ready"
                    Button {
                        // Text, not a box. The one primary action on this screen is the
                        // goal box at the top; everything here is a way to go somewhere
                        // else, and nine filled buttons in a band is what made the page
                        // read as a toolbar.
                        text: "新建项目"
                        variant: "ghost"
                        onClicked: page.createProject()
                    }
                    Button {
                        text: "全部项目"
                        variant: "ghost"
                        onClicked: page.go("projects")
                    }
                }
            }

            // ------------------------------------------------------ 待办任务 --
            Card {
                Layout.fillWidth: true
                Layout.preferredWidth: 1
                Layout.alignment: Qt.AlignTop
                title: "待办任务"
                subtitle: page.dashState === "error" ? ""
                          : (page.dashState === "loading" ? page.busyText
                          : ((page.tasks.open ?? 0) + " 件进行中，"
                             + page.todo.length + " 件今天要看"))

                StateBlock {
                    Layout.fillWidth: true
                    state: page.todoViewState
                    busyText: page.busyText
                    errorText: page.dashError
                    errorHint: page.dashErrorHint
                    errorDetail: page.dashErrorDetail
                    emptyText: (page.tasks.total ?? 0) === 0
                               ? "还没有任务。用「新建任务」写下第一件。"
                               : "今天没有到期或高优先级的任务。"
                    onRetry: page.loadSummary()
                }

                Repeater {
                    model: page.dashState === "ready" ? page.todo.slice(0, 6) : []
                    delegate: RowLayout {
                        required property var modelData
                        Layout.fillWidth: true
                        spacing: Theme.space2
                        Rectangle {
                            width: 6; height: 6; radius: 3
                            color: (modelData.priority === "HIGH" || modelData.priority === "URGENT")
                                   ? Theme.warn : Theme.accent
                            Layout.alignment: Qt.AlignVCenter
                        }
                        Text {
                            text: modelData.title
                            color: Theme.text
                            font.pixelSize: Theme.fontBody
                            Layout.fillWidth: true
                            elide: Text.ElideRight
                        }
                        Text {
                            text: Theme.taskWord(modelData.status)
                            color: Theme.textFaint
                            font.pixelSize: Theme.fontTiny
                        }
                    }
                }

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme.space2
                    visible: page.dashState === "ready"
                    Button {
                        text: "新建任务"
                        variant: "ghost"
                        onClicked: page.createTask()
                    }
                    Button {
                        text: "全部任务"
                        variant: "ghost"
                        onClicked: page.go("tasks")
                    }
                }
            }
        }

        // ==================================================== 运行 / 知识与资料 ==
        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.space4

            // ------------------------------------------------------ 最近运行 --
            Card {
                Layout.fillWidth: true
                Layout.preferredWidth: 1
                Layout.alignment: Qt.AlignTop
                title: "最近运行"
                subtitle: page.runsState !== "ready"
                          ? (page.runsState === "error" ? "" : page.busyText)
                          : (page.runsTotal > 0
                             ? ("共 " + page.runsTotal + " 条 Agent 运行记录")
                             : "还没有运行记录")

                StateBlock {
                    Layout.fillWidth: true
                    state: page.runsViewState
                    busyText: page.busyText
                    errorText: page.runsError
                    errorHint: page.runsErrorHint
                    errorDetail: page.runsErrorDetail
                    emptyText: "还没有让 Agent 做过事。在上面写下目标，交给它执行。"
                    onRetry: page.loadRuns()
                }

                Repeater {
                    model: page.runsState === "ready" ? page.runs.slice(0, 4) : []
                    delegate: Rectangle {
                        required property var modelData
                        readonly property bool unfinished:
                            modelData.status === "INCOMPLETE"
                            || modelData.status === "INTERRUPTED"
                            || modelData.status === "FAILED"

                        Layout.fillWidth: true
                        implicitHeight: 40
                        radius: Theme.radiusSm
                        color: runMouse.containsMouse ? Theme.bgCardHover : "transparent"

                        RowLayout {
                            anchors.fill: parent
                            anchors.leftMargin: Theme.space2
                            anchors.rightMargin: Theme.space2
                            spacing: Theme.space2

                            Rectangle {
                                width: 6; height: 6; radius: 3
                                color: Theme.runTone(modelData.status)
                                Layout.alignment: Qt.AlignVCenter
                            }
                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 0
                                Text {
                                    Layout.fillWidth: true
                                    text: modelData.goal !== ""
                                          ? modelData.goal
                                          : (modelData.model || "Agent 运行")
                                    color: Theme.text
                                    font.pixelSize: Theme.fontBody
                                    elide: Text.ElideRight
                                }
                                Text {
                                    Layout.fillWidth: true
                                    text: Theme.runWord(modelData.status) + " · "
                                          + page.ago(modelData.created_at)
                                    color: Theme.textFaint
                                    font.pixelSize: Theme.fontTiny
                                    elide: Text.ElideRight
                                }
                            }
                            Button {
                                objectName: "resume-run"
                                visible: modelData.goal !== ""
                                         && (modelData.status === "INCOMPLETE"
                                             || modelData.status === "INTERRUPTED"
                                             || modelData.status === "FAILED")
                                text: "继续"
                                variant: "ghost"
                                onClicked: page.resumeRun(modelData)
                            }
                        }

                        MouseArea {
                            id: runMouse
                            anchors.fill: parent
                            hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            // Sits under the 继续 button so the button still wins the click.
                            z: -1
                            Accessible.role: Accessible.Button
                            Accessible.name: "查看运行记录"
                            onClicked: page.go("runs")
                        }
                    }
                }

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme.space2
                    visible: page.runsState === "ready"
                    Button {
                        text: "全部运行记录"
                        variant: "ghost"
                        onClicked: page.go("runs")
                    }
                    Button {
                        text: "让 Agent 做事"
                        variant: "ghost"
                        onClicked: page.go("agent")
                    }
                }
            }

            // --------------------------------------------------- 知识与资料 --
            Card {
                Layout.fillWidth: true
                Layout.preferredWidth: 1
                Layout.alignment: Qt.AlignTop
                title: "知识与资料"
                subtitle: page.dashState === "error" ? ""
                          : (page.dashState === "loading" ? page.busyText
                          : ((page.knowledge.knowledge_bases ?? 0) + " 个知识库，"
                             + (page.knowledge.documents ?? 0) + " 份资料，"
                             + (page.knowledge.chunks ?? 0) + " 个片段"))

                StateBlock {
                    Layout.fillWidth: true
                    state: page.knowledgeViewState
                    busyText: page.busyText
                    errorText: page.dashError
                    errorHint: page.dashErrorHint
                    errorDetail: page.dashErrorDetail
                    emptyText: "还没有资料。导入一份文件，AI 就能引用它回答问题。"
                    onRetry: page.loadSummary()
                }

                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: Theme.space2
                    visible: page.dashState === "ready"

                    Text {
                        Layout.fillWidth: true
                        color: Theme.textMuted
                        font.pixelSize: Theme.fontSmall
                        wrapMode: Text.WordWrap
                        text: (page.knowledge.documents ?? 0) === 0
                              ? "导入资料 → 建立知识库 → 提问 → 让 Agent 用这些资料干活。"
                              : "AI 回答时会引用这些资料，答案后面会标出出处。"
                    }
                }

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme.space2
                    visible: page.dashState === "ready"
                    Button {
                        objectName: "hero-import"
                        text: "导入资料"
                        variant: "secondary"
                        Layout.fillWidth: true
                        onClicked: page.importDocument()
                    }
                    Button {
                        text: "知识库"
                        variant: "ghost"
                        onClicked: page.go("knowledge")
                    }
                    Button {
                        text: "问答"
                        variant: "ghost"
                        onClicked: page.go("ask")
                    }
                }
            }
        }

        // ============================================================ 系统状态 ==
        //
        // A strip, not a panel. Whether a key is configured is worth knowing and is not
        // worth a third of the screen — which is what it had.
        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: statusRow.implicitHeight + Theme.space3 * 2
            radius: Theme.radiusMd
            color: Theme.surface

            RowLayout {
                id: statusRow
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                anchors.margins: Theme.space3
                spacing: Theme.space3

                Rectangle {
                    width: 7; height: 7; radius: 3.5
                    color: page.dashState !== "ready" ? Theme.textFaint
                         : page.llm.api_key_set ? Theme.success : Theme.warn
                    Layout.alignment: Qt.AlignVCenter
                    Accessible.ignored: true
                }
                Text {
                    Layout.fillWidth: true
                    color: Theme.textMuted
                    font.pixelSize: Theme.fontSmall
                    elide: Text.ElideRight
                    text: page.dashState === "error"
                          ? "本地服务不可用，AI 状态暂时读不到。"
                          : page.dashState !== "ready"
                          ? page.busyText
                          : (page.llm.api_key_set
                             ? "AI 服务：" + (page.llm.model || "已配置") + "，可以调用云端模型。"
                             : "AI 服务：未配置 API Key，当前用内置离线引擎。功能可用，回答质量不如云端模型。")
                }
                Text {
                    visible: page.dashState === "ready" && page.embedding.degraded === true
                    color: Theme.textFaint
                    font.pixelSize: Theme.fontTiny
                    text: "语义检索未启用，关键词检索正常"
                }
                Button {
                    text: page.llm.api_key_set ? "AI 设置" : "填写 API Key"
                    variant: page.llm.api_key_set ? "ghost" : "secondary"
                    onClicked: page.go("settings")
                }
            }
        }

        Item { Layout.preferredHeight: Theme.space5 }
    }
}
