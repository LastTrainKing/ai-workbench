import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "../components"
import "../theme.js" as Theme

// Say what you want done, watch it happen, read what came out.
//
// The three regions are the three things a person asks about a run: what it decided to do
// (plan), what it actually did (steps and tool calls), and what they got (result). All of it
// comes from the executor that already exists — `AgentExecutor` writes the run, the plan and
// every invocation to `ai_runs` / `agent_runs` / `tool_calls`, and this page reads them back
// through `loadRunDetail`. There is no second execution path here, and there is deliberately
// no way to re-run a step from this screen: a tool call that changed something cannot be
// safely repeated, and a button that offers to would be a lie about that.
Flickable {
    id: page
    property var shell

    contentWidth: width
    contentHeight: column.implicitHeight
    clip: true

    property string goal: ""
    property bool running: false
    property string progressText: ""
    property var outcome: null
    property var detail: null
    property string errorText: ""

    //: 显示多少由 shell 决定并持久化，页面只读它。专家模式才给步骤、工具调用、token。
    readonly property bool expertMode: shell ? shell.expertMode : false

    //: 谁来干这件事。
    //:
    //: **这是这一页缺掉的那一环。** 页面原先只有一个目标输入框，运行走的是通用 agent
    //: 路径 —— 也就是说，员工在名册里存在、能显示、能收窄权限，但从这个页面发起的任务
    //: 根本没有经过任何一个员工。任务书 §12 的验收是"提出需求 → AI 员工执行"，中间那个
    //: 词在这里是断的。
    property var employees: []
    property string employeeId: ""

    //: 走员工路径时，被派出去的那次构建运行。
    property string buildRunId: ""
    property var buildRun: null

    //: 运行结束后的成果。**这一页自己回答"我拿到了什么"** —— §8.2 明确不要用户再去
    //: 「交付物」页搜一遍数据库记录。形状由后端 `run_outcome.build_run_outcome` 定义，
    //: 这里只渲染，不再自己从运行列表里拼。
    property var outcomeData: null

    //: 预览。文本类的产物在应用内直接读出来；其他格式不假装能读。
    property var preview: null
    property bool previewBusy: false

    readonly property var plan: detail ? (detail.plan ?? []) : []
    readonly property var calls: detail ? (detail.tool_calls ?? []) : []
    readonly property var unresolved: detail ? (detail.unresolved ?? []) : []

    // Files the run actually wrote, taken from the tool calls' own arguments. Only calls that
    // succeeded, because a failed write has no file at the end of it.
    readonly property var artifacts: {
        var seen = {}
        var out = []
        if (!detail) return out
        for (var i = 0; i < calls.length; i++) {
            var call = calls[i]
            if (call.status !== "SUCCEEDED") continue
            var args = call.arguments || {}
            var path = args.path || args.directory || ""
            if (!path || seen[path]) continue
            seen[path] = true
            out.push({ path: path, tool: call.tool_name })
        }
        return out
    }

    // A goal handed over by another page (a project saying "continue this", or the home
    // screen's input). `autoStart` is what makes the home screen's button an action rather
    // than a navigation: the user already said what they want, so arriving on this page
    // with the box filled and the button still to press would be asking twice.
    property bool autoStart: false
    property bool _autoStarted: false

    //: 名册还没回来之前不能自动开跑。
    //:
    //: 首页的「交给员工执行」是把目标和 autoStart 一起丢过来的，页面一挂载就会开始 ——
    //: 而 `loadEmployees` 是异步的，员工 id 那时还是空的。不等这一下，用户按的是"交给
    //: 员工"，跑起来的却是不带员工的通用路径，而且看起来完全正常。
    property bool _employeesLoaded: false

    function _maybeAutoStart() {
        if (_autoStarted || !autoStart || goal === "" || running) return
        if (!_employeesLoaded) return
        _autoStarted = true
        start()
    }

    onGoalChanged: {
        if (goal !== goalArea.text) goalArea.text = goal
        _maybeAutoStart()
    }
    onAutoStartChanged: _maybeAutoStart()

    Component.onCompleted: _loadRosterOnce()
    onShellChanged: _loadRosterOnce()

    // 名册只在拿到 shell 之后拉一次。跟本页其他数据不同，这个是在挂载时就发出去的 ——
    // 而 `shell` 是外部注入的，没有它 `request` 就是一个未定义的调用。
    property bool _rosterLoaded: false
    function _loadRosterOnce() {
        if (_rosterLoaded || !shell) return
        _rosterLoaded = true
        loadEmployees()
    }

    function start() {
        var text = goal.trim()
        if (text === "") {
            shell.toast("warn", "先写下你想完成什么")
            goalArea.forceActiveFocus()
            return
        }
        if (running) return

        // 选了员工就走员工路径：任务带着员工身份下发，权限按那份合同收窄，产出登记成
        // 交付物。没选（一个员工都没有）就走通用路径 —— 那条路一直都在，也仍然可用。
        if (employeeId !== "") {
            startWithEmployee(text)
            return
        }

        running = true
        progressText = "正在启动…"
        outcome = null
        detail = null
        errorText = ""
        var id = "agent-run-" + Date.now()
        shell.request("Agent 运行", id, function (payload) {
            page.outcome = payload
            page.running = false
            page.progressText = ""
            if (!payload) {
                page.errorText = "运行没有返回结果，请查看运行记录。"
                return
            }
            shell.toast(payload.status === "SUCCEEDED" ? "info" : "warn",
                        "结束：" + payload.status + "（" + payload.stopped_reason + "）")
            page.loadDetail(payload.run_id)
        })
        bridge.startAgentRun(id, text)
    }

    // ------------------------------------------------------------------ 员工 --
    function loadEmployees() {
        var id = "agent-employees-" + Date.now()
        shell.request("我的员工", id, function (payload) {
            var list = (payload && payload.items) ? payload.items : []
            // 只留能接活的。把"已归档"和"上次失败"排进可选列表，等于让用户选一个不会
            // 干活的员工，然后纳闷为什么没动静。
            var usable = []
            for (var i = 0; i < list.length; i++) {
                if (Theme.employeeIsActive(list[i].status)) usable.push(list[i])
            }
            page.employees = usable
            if (page.employeeId === "" && usable.length > 0) page.employeeId = usable[0].id
            page._employeesLoaded = true
            page._maybeAutoStart()
        }, function () {
            page.employees = []
            // 读不到名册也要放行自动开跑：走通用路径总比按钮按下去什么都不发生好。
            page._employeesLoaded = true
            page._maybeAutoStart()
        })
        bridge.loadEmployees(id)
    }

    function employeeName() {
        for (var i = 0; i < employees.length; i++) {
            if (employees[i].id === employeeId) return employees[i].name
        }
        return ""
    }

    //: 状态词与颜色统一走 theme.js。原先这一页自带一份 `buildRunWord`，交付物页
    //: 若也要说同一件事就得再抄一遍 —— 两个副本迟早给出两个答案。
    function buildRunWord(status) { return Theme.buildWord(status) }

    function startWithEmployee(text) {
        running = true
        progressText = "已交给" + employeeName() + "…"
        outcome = null
        detail = null
        errorText = ""
        buildRunId = ""
        buildRun = null
        outcomeData = null
        preview = null

        var id = "agent-build-" + Date.now()
        shell.request("交给员工", id, function (payload) {
            page.buildRunId = (payload && payload.build_run_id) ? payload.build_run_id : ""
            if (page.buildRunId === "") {
                page.running = false
                page.progressText = ""
                page.errorText = "没能启动这次运行。"
                return
            }
            // 交给后台了。进度靠轮询，不靠这一页等 —— 员工可能跑几分钟，而窗口不该
            // 因此卡住或者假装知道下一秒会发生什么。
            pollTimer.start()
        }, function (title) {
            page.running = false
            page.progressText = ""
            page.errorText = title
        })
        bridge.startBuildRun(id, text, employeeId)
    }

    function pollBuildRun() {
        if (buildRunId === "") return
        var id = "agent-poll-" + Date.now()
        shell.request("运行进度", id, function (payload) {
            var list = (payload && payload.items) ? payload.items : []
            var found = null
            for (var i = 0; i < list.length; i++) {
                if (list[i].id === page.buildRunId) found = list[i]
            }
            if (!found) return
            page.buildRun = found
            page.progressText = page.buildRunWord(found.status)
                + (found.steps_total > 0
                   ? "　" + found.steps_done + "/" + found.steps_total + " 步" : "")
            if (["COMPLETED", "PARTIAL", "FAILED", "CANCELLED", "LIMIT_REACHED"]
                    .indexOf(found.status) >= 0) {
                pollTimer.stop()
                page.running = false
                page.progressText = ""
                shell.toast(found.status === "COMPLETED" ? "info" : "warn",
                            "结束：" + page.buildRunWord(found.status))
                page.loadOutcome()
                // 再读一次。运行的状态先写、`run_state` 的戳记后写（`_stamp_run_state`
                // 在 `set_status` 之后），所以紧接着读到的证据里可能还缺那一格。
                // 与其在这里猜它什么时候写完，不如让它自己再刷新一次。
                outcomeRefresh.start()
            }
        })
        bridge.loadBuildRuns(id, "10")
    }

    Timer {
        id: pollTimer
        interval: 4000
        repeat: true
        onTriggered: page.pollBuildRun()
    }

    Timer {
        id: outcomeRefresh
        interval: 1500
        repeat: false
        onTriggered: page.loadOutcome()
    }

    // --------------------------------------------------------------- 成果 (§8.2) --
    function loadOutcome() {
        if (buildRunId === "") return
        var id = "agent-outcome-" + Date.now()
        shell.request("本次成果", id, function (payload) { page.outcomeData = payload })
        bridge.loadRunOutcome(id, buildRunId)
    }

    //: 一条产物对应的验证证据。后端把 `verification[]` 与 `artifacts[]` 平行返回，
    //: 按 id 对齐 —— 顺序对齐是巧合，不是契约。
    function verificationFor(artifactId) {
        if (!outcomeData) return null
        var list = outcomeData.verification ?? []
        for (var i = 0; i < list.length; i++) {
            if (list[i].artifact_id === artifactId) return list[i]
        }
        return null
    }

    function openPreview(artifact) {
        previewBusy = true
        preview = null
        var id = "agent-preview-" + Date.now()
        shell.request("预览", id, function (payload) {
            page.previewBusy = false
            page.preview = payload
        }, function (title) {
            page.previewBusy = false
            page.preview = { reason: title, text: "" }
        })
        bridge.readDeliverableText(id, artifact.id)
    }

    //: 一个按钮一个动作。**每个都对应一个真实存在的能力** —— 「导出」不在列表里，
    //: 因为这个构建没有文档转换器，做一个转不了格式的按钮就是做一个假功能。
    function act(artifact, actionId) {
        switch (actionId) {
        case "preview":  page.openPreview(artifact); break
        case "open":     bridge.openPath("open-" + Date.now(), artifact.path); break
        case "reveal":   bridge.revealPath("reveal-" + Date.now(), artifact.path); break
        case "continue":
            shell.navigate("agent", {
                goal: "在《" + (artifact.title || artifact.path) + "》的基础上继续修改：",
                autoStart: false
            })
            break
        case "regenerate": page.regenerate(); break
        }
    }

    function regenerate() {
        var goal = outcomeData ? (outcomeData.goal ?? "") : ""
        if (!goal) {
            shell.toast("warn", "找不到当初那次请求，无法原样重跑")
            return
        }
        var id = "agent-regen-" + Date.now()
        shell.request("重新生成", id, function (payload) {
            page.buildRunId = (payload && payload.build_run_id) ? payload.build_run_id : ""
            page.outcomeData = null
            page.preview = null
            page.running = true
            page.progressText = "已交给" + page.employeeName() + "…"
            pollTimer.start()
        }, function (title) {
            shell.toast("error", title)
        })
        bridge.startBuildRun(id, goal, employeeId)
    }

    //: 运行级的后续动作。逐条对应 `next_actions[].id`；不认识的 id 什么都不做，
    //: 而不是随便挑一个动作执行。
    function runNextAction(actionId) {
        switch (actionId) {
        case "verify":            page.verifyAll(); break
        case "open_deliverables": page.shell.navigate("deliverables"); break
        }
    }

    property int _verifyPending: 0

    //: 「重新验证全部产出」。每条各发一次请求，全部回来后刷新一次成果 —— 一次请求
    //: 只能带一个产物 id，所以在客户端数数，而不是假装有一个批量接口。
    function verifyAll() {
        var list = outcomeData ? (outcomeData.artifacts ?? []) : []
        if (list.length === 0) return
        _verifyPending = list.length
        for (var i = 0; i < list.length; i++) {
            var id = "agent-verify-" + i + "-" + Date.now()
            shell.request("重新验证", id, function () {
                page._verifyPending -= 1
                if (page._verifyPending <= 0) {
                    shell.toast("info", "已重新验证")
                    page.loadOutcome()
                }
            }, function (title) {
                page._verifyPending -= 1
                shell.toast("error", title)
                if (page._verifyPending <= 0) page.loadOutcome()
            })
            bridge.verifyDeliverable(id, list[i].id)
        }
    }

    function loadDetail(runId) {
        if (!runId) return
        var id = "agent-detail-" + Date.now()
        shell.request("运行详情", id, function (payload) { page.detail = payload })
        bridge.loadRunDetail(id, runId)
    }

    function cancel() {
        bridge.cancelAgentRun()
        progressText = "已请求取消；当前步骤结束后停止"
    }

    function statusWord(status) {
        switch (status) {
        case "SUCCEEDED":  return "已完成"
        case "FAILED":     return "失败"
        case "CANCELLED":  return "已取消"
        case "INCOMPLETE": return "未完成"
        case "EXECUTING":  return "执行中"
        case "PENDING":    return "等待中"
        default:           return status || "未知"
        }
    }

    function statusColor(status) {
        switch (status) {
        case "SUCCEEDED":  return Theme.success
        case "FAILED":     return Theme.danger
        case "UNKNOWN":    return Theme.warn
        case "CANCELLED":
        case "INCOMPLETE": return Theme.warn
        default:           return Theme.textMuted
        }
    }

    Connections {
        target: bridge
        function onProgress(id, text) {
            if (page.running) page.progressText = text
        }
        function onFailed(id, kind, message) {
            if (!page.running) return
            page.running = false
            page.progressText = ""
            page.errorText = kind + "：" + message
        }
    }

    ColumnLayout {
        id: column
        width: page.width
        spacing: 16

        RowLayout {
            Layout.fillWidth: true
            Text {
                text: "Agent 工作台"
                color: Theme.text
                font.pixelSize: 24
                font.bold: true
                Layout.fillWidth: true
            }
            Text {
                visible: page.detail !== null
                text: page.detail ? ("运行 " + page.detail.run.run_id.slice(0, 8)) : ""
                color: Theme.textFaint
                font.pixelSize: 12
            }
        }

        Card {
            Layout.fillWidth: true
            title: "要我做什么"
            subtitle: "说清目标和结果，它会自己决定步骤"

            ScrollView {
                Layout.fillWidth: true
                Layout.preferredHeight: 92
                TextArea {
                    id: goalArea
                    objectName: "agent-goal"
                    placeholderText: "例如：把这个项目里的测试跑一遍，把失败的整理成一份报告"
                    wrapMode: TextArea.Wrap
                    color: Theme.text
                    font.pixelSize: 14
                    background: Rectangle {
                        color: Theme.bgInput
                        radius: Theme.radiusMd
                        border.width: 1
                        border.color: goalArea.activeFocus ? Theme.borderFocus : Theme.divider
                    }
                    onTextChanged: page.goal = text
                }
            }

            // 谁来干。名册为空时这一行不出现 —— 一个没有选项的选择器只是在占地方。
            RowLayout {
                Layout.fillWidth: true
                visible: page.employees.length > 0
                spacing: Theme.space2

                Text {
                    text: "交给"
                    color: Theme.textFaint
                    font.pixelSize: Theme.fontSmall
                }
                Flow {
                    Layout.fillWidth: true
                    spacing: Theme.space2

                    Repeater {
                        model: page.employees
                        delegate: Rectangle {
                            required property var modelData

                            height: 26
                            width: pickLabel.implicitWidth + Theme.space3 * 2
                            radius: 13
                            color: page.employeeId === modelData.id
                                   ? Theme.bgCardHover
                                   : (pickMouse.containsMouse ? Theme.bgCard : "transparent")
                            border.width: 1
                            border.color: page.employeeId === modelData.id
                                          ? Theme.accent : Theme.divider

                            Text {
                                id: pickLabel
                                anchors.centerIn: parent
                                text: modelData.name
                                color: page.employeeId === modelData.id
                                       ? Theme.text : Theme.textMuted
                                font.pixelSize: Theme.fontSmall
                            }
                            MouseArea {
                                id: pickMouse
                                anchors.fill: parent
                                hoverEnabled: true
                                cursorShape: Qt.PointingHandCursor
                                Accessible.role: Accessible.RadioButton
                                Accessible.name: "交给 " + modelData.name
                                onClicked: page.employeeId = modelData.id
                            }
                        }
                    }
                }
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 10
                Button {
                    objectName: "agent-start"
                    text: page.running ? "执行中…" : "开始执行"
                    // The one thing this page is for. Everything else on it is a way to
                    // look at a run that already happened.
                    variant: "primary"
                    enabled: !page.running
                    onClicked: page.start()
                }
                Button {
                    text: "取消"
                    visible: page.running
                    onClicked: page.cancel()
                }
                Text {
                    Layout.fillWidth: true
                    text: page.progressText
                    color: page.running ? Theme.accent : Theme.textFaint
                    font.pixelSize: 12
                    elide: Text.ElideRight
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
        }

        // ------------------------------------------------- 员工在做（§5 的任务/员工/状态）--
        Card {
            Layout.fillWidth: true
            visible: page.buildRun !== null
            title: "员工在做"
            subtitle: page.employeeName() === "" ? "" : ("交给 " + page.employeeName())

            RowLayout {
                Layout.fillWidth: true
                spacing: 10
                Rectangle {
                    width: 10; height: 10; radius: 5
                    color: page.buildRun && page.buildRun.status === "COMPLETED"
                           ? Theme.success
                           : page.buildRun && page.buildRun.status === "FAILED"
                             ? Theme.danger : Theme.accent
                }
                Text {
                    text: page.buildRun ? page.buildRunWord(page.buildRun.status) : ""
                    color: Theme.text
                    font.pixelSize: 15
                    font.bold: true
                }
                Text {
                    Layout.fillWidth: true
                    text: page.buildRun && page.buildRun.steps_total > 0
                          ? (page.buildRun.steps_done + "/" + page.buildRun.steps_total + " 步")
                          : ""
                    color: Theme.textMuted
                    font.pixelSize: 12
                }
            }

            Text {
                Layout.fillWidth: true
                visible: page.buildRun !== null && (page.buildRun.result_summary ?? "") !== ""
                wrapMode: Text.WordWrap
                color: Theme.textMuted
                font.pixelSize: 12
                text: page.buildRun ? (page.buildRun.result_summary ?? "") : ""
            }

            RowLayout {
                Layout.fillWidth: true
                // 成果已经在下面那张卡里了，这里就不再指路 —— 指路是给"看不到成果"
                // 的页面用的。
                visible: page.outcomeData === null
                spacing: Theme.space2
                Text {
                    Layout.fillWidth: true
                    text: "完成后，产出会直接显示在下面，不用去别的页面找。"
                    color: Theme.textFaint
                    font.pixelSize: Theme.fontTiny
                }
                Button {
                    text: "查看全部交付物"
                    variant: "secondary"
                    onClicked: page.shell.navigate("deliverables")
                }
            }
        }

        // ------------------------------------------------------ 这次的成果（§8.2）--
        //
        // 运行结束的那一刻，用户要的东西就在这里 —— 文件名、类型、大小、验证证据、
        // 以及能真的按下去的按钮。原先这一页只说"产出在交付物页里"，把"我拿到了什么"
        // 这个问题推给了另一页。
        Card {
            Layout.fillWidth: true
            visible: page.outcomeData !== null
            title: "这次的成果"
            subtitle: page.outcomeData ? (page.outcomeData.artifacts ?? []).length + " 个文件" : ""

            RowLayout {
                Layout.fillWidth: true
                spacing: 10
                Rectangle {
                    width: 10; height: 10; radius: 5
                    color: page.outcomeData
                           ? Theme.buildTone(page.outcomeData.status) : Theme.textMuted
                }
                Text {
                    text: page.outcomeData ? Theme.buildWord(page.outcomeData.status) : ""
                    color: Theme.text
                    font.pixelSize: 15
                    font.bold: true
                }
                Text {
                    Layout.fillWidth: true
                    text: page.outcomeData ? (page.outcomeData.summary ?? "") : ""
                    color: Theme.textMuted
                    font.pixelSize: 12
                    wrapMode: Text.WordWrap
                }
            }

            // 没有产出时明说。空白卡片看起来像"还在加载"。
            Text {
                Layout.fillWidth: true
                visible: page.outcomeData && (page.outcomeData.artifacts ?? []).length === 0
                text: "这次运行没有写出文件。如果它本来就该产出一个文件，可以在专家模式里看"
                      + "它做了什么。"
                color: Theme.textFaint
                font.pixelSize: 12
                wrapMode: Text.WordWrap
            }

            Repeater {
                model: page.outcomeData ? (page.outcomeData.artifacts ?? []) : []

                delegate: DeliverableCard {
                    required property var modelData
                    artifact: modelData
                    verification: page.verificationFor(modelData.id)
                    onActionRequested: function (actionId) {
                        page.act(modelData, actionId)
                    }
                }
            }

            // ------------------------------------------------------------- 预览 --
            Rectangle {
                Layout.fillWidth: true
                visible: page.preview !== null || page.previewBusy
                implicitHeight: Math.min(360, previewText.implicitHeight + 40)
                radius: 8
                color: Theme.bgInput
                border.width: 1
                border.color: Theme.border

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 10
                    spacing: 6

                    Text {
                        Layout.fillWidth: true
                        text: page.preview
                              ? (page.preview.title + "　" + Theme.humanBytes(page.preview.size_bytes))
                              : "正在读取…"
                        color: Theme.textMuted
                        font.pixelSize: 11
                    }

                    Text {
                        Layout.fillWidth: true
                        visible: page.preview !== null
                                 && (page.preview.reason ?? "") !== ""
                        text: page.preview ? page.preview.reason : ""
                        color: Theme.warn
                        font.pixelSize: 12
                        wrapMode: Text.WordWrap
                    }

                    ScrollView {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        visible: page.preview !== null && (page.preview.text ?? "") !== ""
                        clip: true

                        TextArea {
                            id: previewText
                            readOnly: true
                            text: page.preview ? page.preview.text : ""
                            color: Theme.text
                            font.pixelSize: 12
                            font.family: "Consolas, monospace"
                            wrapMode: TextArea.Wrap
                            background: null
                        }
                    }

                    Text {
                        Layout.fillWidth: true
                        visible: page.preview !== null && page.preview.truncated === true
                        text: "内容较长，只显示前面一部分。完整内容请用系统程序打开。"
                        color: Theme.textFaint
                        font.pixelSize: 10
                    }
                }
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 8
                visible: page.outcomeData !== null

                Repeater {
                    model: page.outcomeData ? (page.outcomeData.next_actions ?? []) : []

                    delegate: Button {
                        required property var modelData
                        text: modelData.label
                        enabled: modelData.enabled
                        variant: "ghost"
                        onClicked: page.runNextAction(modelData.id)
                    }
                }
                Item { Layout.fillWidth: true }
            }
        }

        // ------------------------------------------------------------------ 运行状态 --
        Card {
            Layout.fillWidth: true
            visible: page.outcome !== null
            title: "运行状态"

            RowLayout {
                Layout.fillWidth: true
                spacing: 10
                Rectangle {
                    width: 10; height: 10; radius: 5
                    color: page.outcome ? page.statusColor(page.outcome.status) : Theme.textMuted
                }
                Text {
                    text: page.outcome ? page.statusWord(page.outcome.status) : ""
                    color: Theme.text
                    font.pixelSize: 15
                    font.bold: true
                }
                Text {
                    Layout.fillWidth: true
                    // 结束原因和步数是专家模式的东西。普通模式下这一行空着，由下面那句
                    // 人话接手 —— 留着它只会让"已完成"旁边挂一串 stopped_reason。
                    visible: page.expertMode
                    text: page.outcome
                          ? ("结束原因：" + page.outcome.stopped_reason
                             + "　用了 " + page.outcome.steps + " 步")
                          : ""
                    color: Theme.textMuted
                    font.pixelSize: 12
                }
                Text {
                    Layout.fillWidth: true
                    visible: !page.expertMode
                    text: page.outcome && page.outcome.status === "SUCCEEDED"
                          ? "它把这件事做完了。产出在下面的「产物」里。"
                          : ""
                    color: Theme.textMuted
                    font.pixelSize: 12
                }
            }

            Text {
                Layout.fillWidth: true
                visible: page.outcome !== null && page.outcome.status === "INCOMPLETE"
                wrapMode: Text.WordWrap
                color: Theme.warn
                font.pixelSize: 12
                text: "它没有把这件事做完。下面的步骤和工具调用是它做到哪一步的记录，"
                      + "不是失败报告 —— 可以据此接着做，或把目标写得更具体再试一次。"
            }
        }

        // ------------------------------------------------------- 规划 / 步骤 / 结果 --
        //
        // **专家模式专属。** 这是任务书 §7 的分界线：普通模式给"任务、状态、结果"，
        // 专家模式再加上步骤、工具调用、token 和延迟。把它整块藏起来，而不是让它变淡，
        // 是因为读它的人本来就会去开专家模式 —— 一个半透明的技术面板只会让普通用户
        // 以为自己漏看了什么。
        RowLayout {
            Layout.fillWidth: true
            spacing: 16
            visible: page.detail !== null && page.expertMode

            ColumnLayout {
                Layout.fillWidth: true
                Layout.preferredWidth: 1
                Layout.alignment: Qt.AlignTop
                spacing: 16

                Card {
                    Layout.fillWidth: true
                    title: "任务规划"
                    subtitle: page.plan.length === 0
                              ? "这次运行没有留下规划"
                              : (page.plan.length + " 个步骤")

                    Repeater {
                        model: page.plan
                        delegate: RowLayout {
                            required property var modelData
                            required property int index
                            Layout.fillWidth: true
                            spacing: 8
                            Text {
                                text: (index + 1) + "."
                                color: Theme.textFaint
                                font.pixelSize: 12
                            }
                            Text {
                                text: typeof modelData === "string"
                                      ? modelData
                                      : (modelData.description ?? modelData.title ?? JSON.stringify(modelData))
                                color: Theme.text
                                font.pixelSize: 13
                                Layout.fillWidth: true
                                wrapMode: Text.WordWrap
                            }
                        }
                    }
                }

                Card {
                    Layout.fillWidth: true
                    title: "工具调用"
                    subtitle: page.calls.length === 0
                              ? "这次运行没有调用工具"
                              : (page.calls.length + " 次调用")

                    Repeater {
                        model: page.calls
                        delegate: Rectangle {
                            required property var modelData
                            Layout.fillWidth: true
                            implicitHeight: callColumn.implicitHeight + 16
                            radius: 8
                            color: "transparent"
                            border.width: 1
                            border.color: modelData.status === "UNKNOWN" ? Theme.warn
                                          : modelData.status === "FAILED" ? Theme.danger
                                          : Theme.border

                            ColumnLayout {
                                id: callColumn
                                anchors.left: parent.left
                                anchors.right: parent.right
                                anchors.verticalCenter: parent.verticalCenter
                                anchors.margins: 8
                                spacing: 4

                                RowLayout {
                                    Layout.fillWidth: true
                                    spacing: 8
                                    Text {
                                        text: "#" + modelData.step_index + " " + modelData.tool_name
                                        color: Theme.text
                                        font.pixelSize: 13
                                        font.bold: true
                                    }
                                    Text {
                                        Layout.fillWidth: true
                                        text: modelData.status
                                        color: page.statusColor(modelData.status)
                                        font.pixelSize: 11
                                    }
                                    Text {
                                        text: modelData.latency_ms + " ms"
                                        color: Theme.textFaint
                                        font.pixelSize: 11
                                    }
                                }
                                Text {
                                    Layout.fillWidth: true
                                    visible: modelData.error_message !== ""
                                    text: modelData.error_message
                                    color: Theme.danger
                                    font.pixelSize: 11
                                    wrapMode: Text.WrapAnywhere
                                }
                                Text {
                                    Layout.fillWidth: true
                                    text: JSON.stringify(modelData.arguments)
                                    color: Theme.textFaint
                                    font.family: "Consolas"
                                    font.pixelSize: 11
                                    elide: Text.ElideRight
                                }
                            }
                        }
                    }
                }
            }

            ColumnLayout {
                Layout.fillWidth: true
                Layout.preferredWidth: 1
                Layout.alignment: Qt.AlignTop
                spacing: 16

                Card {
                    Layout.fillWidth: true
                    title: "结果"
                    subtitle: page.outcome ? "执行总结" : ""

                    Text {
                        Layout.fillWidth: true
                        wrapMode: Text.WordWrap
                        color: Theme.text
                        font.pixelSize: 13
                        text: {
                            if (page.detail && page.detail.final_answer) return page.detail.final_answer
                            if (page.outcome && page.outcome.final_answer) return page.outcome.final_answer
                            return page.running ? "正在执行…" : "还没有结果。"
                        }
                    }
                }

                Card {
                    Layout.fillWidth: true
                    title: "产物"
                    subtitle: page.artifacts.length === 0
                              ? "这次运行没有写出文件"
                              : (page.artifacts.length + " 个位置")

                    Repeater {
                        model: page.artifacts
                        delegate: Rectangle {
                            required property var modelData
                            Layout.fillWidth: true
                            implicitHeight: 46
                            radius: 8
                            color: artMouse.containsMouse ? Theme.bgCardHover : "transparent"
                            border.width: artMouse.containsMouse ? 1 : 0
                            border.color: Theme.border

                            ColumnLayout {
                                anchors.left: parent.left
                                anchors.right: parent.right
                                anchors.verticalCenter: parent.verticalCenter
                                anchors.leftMargin: 8
                                anchors.rightMargin: 8
                                spacing: 2
                                Text {
                                    Layout.fillWidth: true
                                    text: modelData.path
                                    color: Theme.text
                                    font.pixelSize: 12
                                    elide: Text.ElideMiddle
                                }
                                Text {
                                    text: modelData.tool
                                    color: Theme.textFaint
                                    font.pixelSize: 10
                                }
                            }

                            MouseArea {
                                id: artMouse
                                anchors.fill: parent
                                hoverEnabled: true
                                cursorShape: Qt.PointingHandCursor
                                Accessible.role: Accessible.Button
                                Accessible.name: "在文件夹中显示 " + modelData.path
                                onClicked: {
                                    var id = "reveal-" + Date.now()
                                    shell.request("打开位置", id, function () {
                                        shell.toast("info", "已在文件夹中打开")
                                    })
                                    bridge.revealPath(id, modelData.path)
                                }
                            }
                        }
                    }
                }

                Card {
                    Layout.fillWidth: true
                    visible: page.unresolved.length > 0
                    title: "结果未知的调用"
                    subtitle: "这些调用可能改动了东西，也可能没有。这里不会重跑它们。"

                    Repeater {
                        model: page.unresolved
                        delegate: Text {
                            required property var modelData
                            Layout.fillWidth: true
                            text: "#" + modelData.step_index + " " + modelData.tool_name
                            color: Theme.warn
                            font.pixelSize: 12
                            wrapMode: Text.WrapAnywhere
                        }
                    }
                }

                Card {
                    Layout.fillWidth: true
                    title: "历史运行"
                    subtitle: "最近几次"

                    Button {
                        Layout.fillWidth: true
                        text: "查看全部运行记录"
                        variant: "ghost"
                        onClicked: page.shell.navigate("runs")
                    }
                }
            }
        }

        Item { Layout.preferredHeight: 24 }
    }
}
