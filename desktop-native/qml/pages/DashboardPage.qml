import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "../components"
import "../theme.js" as Theme

// 首页 —— 用户打开软件看到的第一屏。
//
// 它按顺序回答三个问题，别的都不回答：
//
//   1. 我的 AI 员工在吗？      → 问候语 + 在岗员工
//   2. 我想让它做什么？        → 输入框
//   3. 上次做出来的东西在哪？  → 最近成果，每一条都能直接打开
//
// **刻意不回答的问题**：跑到第几步、调用了哪些工具、用了多少 token。那些是专家模式的
// 答案，属于运行记录页。把它们摆在第一屏，就是把开发者视角当成了用户视角 —— 这正是
// 这个页面之前的样子：一屏计数器，讲"发生过什么"，不讲"现在能做什么"。
//
// 数据全部来自真实查询：`loadEmployees` 查员工名册，`loadDeliverables` 查交付物，
// `loadDashboard` 查模型服务状态。没有任何一个面板为了填满版面而编造数字。
//
// **三块各自独立的状态。** 它们曾经是两次请求加一个"读取中…"字符串，失败之后那行字
// 会一直留着 —— 于是坏掉的面板和慢吞吞的面板长得一模一样，而且都没有出路。现在每块
// 自己报告 加载中 / 空 / 出错，并且能自己重试。
Flickable {
    id: page
    property var shell

    contentWidth: width
    contentHeight: column.implicitHeight
    clip: true

    // `summary`，不是 `data`：`data` 是 Item 的内建属性（持有子项的默认属性），
    // 重新声明它会遮蔽引擎自己的成员。
    property var summary: null
    property string dashState: "loading"
    property string dashError: ""
    property string dashErrorHint: ""
    property string dashErrorDetail: ""

    //: `summary.providers.llm` —— 注意不是 `summary.llm`。dashboard 的返回里只有
    //: `providers`，写错名字不会报错，只会让 `api_key_set` 永远是 undefined，于是首页
    //: 对着一个配好 Key 的用户说"未配置 API Key"，并把他送去重新填一遍。
    //: 这条路径上写错字段名不产生异常，只产生一句假话 —— 抓图时才看出来。
    readonly property var llm: (summary && summary.providers && summary.providers.llm)
                               ? summary.providers.llm : ({})

    property var employees: []
    property string employeesState: "loading"
    property string employeesError: ""

    property var deliverables: []
    property int deliverablesTotal: 0
    property string deliverablesState: "loading"
    property string deliverablesError: ""
    property string deliverablesErrorHint: ""
    property string deliverablesErrorDetail: ""

    //: 在岗员工数 —— 只有真的能接活的才算（空闲 / 工作中 / 等授权）。
    readonly property int onDutyCount: {
        var n = 0
        for (var i = 0; i < employees.length; i++) {
            if (Theme.employeeIsActive(employees[i].status)) n++
        }
        return n
    }

    //: 问候语用的名字。`loadDashboard` 的返回里不一定有用户信息，所以这里不假设它
    //: 存在 —— 没有就只说"你好"，而不是显示一个 undefined。
    readonly property string userName: {
        if (!summary) return ""
        if (summary.user && summary.user.display_name) return summary.user.display_name
        if (summary.display_name) return summary.display_name
        return ""
    }

    readonly property string rosterLine: {
        if (employeesState === "loading") return "正在点名…"
        if (employeesState === "error") return "没能读到员工名册"
        if (employees.length === 0) return "还没有员工。去「我的员工」雇一个，它就能开始接活了。"
        return onDutyCount + " 位员工在岗"
    }

    //: "加载完了但是空的"和"还在加载"是两句不同的话，面板得能分辨。
    readonly property string deliverablesViewState:
        deliverablesState === "ready" && deliverables.length === 0
            ? "empty" : deliverablesState

    // ------------------------------------------------------------------ 文案 --
    //: 相对时间。写成函数而不是内联三元，是因为交付物列表和详情页要用同一套说法。
    function relativeTime(iso) {
        if (!iso) return ""
        var then = Date.parse(iso)
        if (isNaN(then)) return ""
        var minutes = Math.floor((Date.now() - then) / 60000)
        if (minutes < 1) return "刚刚"
        if (minutes < 60) return minutes + " 分钟前"
        var hours = Math.floor(minutes / 60)
        if (hours < 24) return hours + " 小时前"
        var days = Math.floor(hours / 24)
        if (days < 30) return days + " 天前"
        return Math.floor(days / 30) + " 个月前"
    }

    //: 文件类型。任务书里的样例用的是图标，这里照做 —— 一个交付物列表里，读者先要
    //: 认出"这是个 PPT 还是个表格"，文字标签反而要读。
    function kindGlyph(kind) {
        switch (kind) {
        case "pptx":  return "📊"
        case "docx":  return "📝"
        case "xlsx":  return "📈"
        case "csv":   return "📈"
        case "pdf":   return "📕"
        case "code":  return "💻"
        case "md":    return "📄"
        case "report": return "📄"
        default:      return "📄"
        }
    }

    function kindLabel(kind) {
        switch (kind) {
        case "pptx":  return "演示文稿"
        case "docx":  return "Word 文档"
        case "xlsx":  return "表格"
        case "csv":   return "数据表"
        case "pdf":   return "PDF"
        case "code":  return "代码"
        case "md":    return "Markdown"
        case "report": return "报告"
        default:      return kind || "文件"
        }
    }

    function humanSize(bytes) {
        var n = Number(bytes || 0)
        if (n <= 0) return "空文件"
        if (n < 1024) return n + " 字节"
        if (n < 1024 * 1024) return (n / 1024).toFixed(1) + " KB"
        return (n / 1048576).toFixed(1) + " MB"
    }

    //: 一个交付物此刻算什么状态。**校验和接受是两件事**，不能合成一个词：校验说的是
    //: "文件真的存在、读得回来"，接受说的是"用户认了"。合并它们就是之前那个 bug ——
    //: 一个 70 KB 的成品被标成"未校验"，因为运行失败了。
    function deliverableStatus(item) {
        if (item.accepted === true) return "已接受"
        if (item.verified === true) return "已校验"
        return "待确认"
    }

    function deliverableTone(item) {
        if (item.accepted === true) return Theme.accent
        if (item.verified === true) return Theme.info
        return Theme.warn
    }

    // ------------------------------------------------------------------ 加载 --
    function load() {
        loadSummary()
        loadEmployees()
        loadDeliverables()
    }

    function loadSummary() {
        page.dashState = "loading"
        page.dashError = ""
        var id = "home-summary-" + Date.now()
        shell.request("服务状态", id, function (payload) {
            if (!payload) {
                page.dashState = "error"
                page.dashError = "服务没有返回数据"
                return
            }
            page.summary = payload
            page.dashState = "ready"
        }, function (message, code, human) {
            // `message` 已经是读者视角的句子 —— shell 过了 `Errors.human`。提示和原始
            // 异常一起带回来，面板就能给出下一步，并把原始文本留在一键之外。
            page.dashState = "error"
            page.dashError = message
            page.dashErrorHint = human ? human.hint : ""
            page.dashErrorDetail = human ? human.raw : ""
        })
        bridge.loadDashboard(id)
    }

    function loadEmployees() {
        page.employeesState = "loading"
        page.employeesError = ""
        var id = "home-employees-" + Date.now()
        shell.request("我的员工", id, function (payload) {
            page.employees = (payload && payload.items ? payload.items : [])
            page.employeesState = "ready"
        }, function (message) {
            page.employeesState = "error"
            page.employeesError = message
        })
        bridge.loadEmployees(id)
    }

    function loadDeliverables() {
        page.deliverablesState = "loading"
        page.deliverablesError = ""
        var id = "home-deliverables-" + Date.now()
        shell.request("最近成果", id, function (payload) {
            // `payload` 本身可能是 null；`??` 护的是字段不是对象，直接用会让错误读成
            // 一个坏掉的绑定。
            var items = (payload && payload.items ? payload.items : [])
            page.deliverables = items.slice(0, 6)
            page.deliverablesTotal = (payload && payload.total) ? payload.total : items.length
            page.deliverablesState = "ready"
        }, function (message, code, human) {
            page.deliverablesState = "error"
            page.deliverablesError = message
            page.deliverablesErrorHint = human ? human.hint : ""
            page.deliverablesErrorDetail = human ? human.raw : ""
        })
        bridge.loadDeliverables(id, "20")
    }

    // ---------------------------------------------------------------- 导航 --
    function go(name, params) {
        // shell 唯一的导航入口：它会清掉过期的页面参数，并把本页还在飞的请求标记为
        // 属于一个正在离开的页面。
        shell.navigate(name, params)
    }

    //: 把目标交给员工去执行。Agent 页是全产品唯一执行任务的地方；这一屏不再长出第二个
    //: 执行器。
    function executeGoal() {
        var text = goalArea.text.trim()
        if (text === "") {
            shell.toast("warn", "先写下你想让员工做什么")
            goalArea.forceActiveFocus()
            return
        }
        go("agent", { goal: text, autoStart: true })
    }

    //: 同一个目标，但当作问题 —— 多轮对话，回答而不是执行。
    function askGoal() {
        var text = goalArea.text.trim()
        if (text === "") {
            shell.toast("warn", "先写下你想问什么")
            goalArea.forceActiveFocus()
            return
        }
        go("conversation", { prompt: text })
    }

    //: 打开成果。`openPath` 走系统默认程序；打不开时会回落成"在文件夹里显示"，
    //: 由桥接层决定，页面不重复实现一遍。
    function openDeliverable(item) {
        if (!item.path) {
            shell.toast("warn", "这条成果没有记录文件路径")
            return
        }
        bridge.openPath("open-" + Date.now(), item.path)
    }

    //: 在已有成果上继续。**不自动开跑** —— 用户点"继续修改"是想改点什么，直接把它
    //: 交给员工会跳过他正要说的那句话。
    function continueFrom(item) {
        var name = item.title || item.path || "上一版"
        go("agent", {
            goal: "在《" + name + "》的基础上继续修改：",
            autoStart: false
        })
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
        spacing: Theme.space5

        // ============================================================== 问候 ==
        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.space3

            ColumnLayout {
                Layout.fillWidth: true
                spacing: Theme.space1

                Text {
                    text: page.userName === "" ? "你好" : "你好，" + page.userName
                    color: Theme.text
                    font.pixelSize: Theme.fontDisplay
                    font.bold: true
                    Accessible.role: Accessible.Heading
                    Accessible.name: text
                }
                Text {
                    objectName: "home-roster"
                    text: page.rosterLine
                    color: page.employees.length === 0 ? Theme.warn : Theme.textMuted
                    font.pixelSize: Theme.fontBody
                }
            }

            Button {
                text: "刷新"
                variant: "ghost"
                onClicked: page.load()
            }
        }

        // ============================================================ 输入区 ==
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
                    text: "让员工做什么"
                    color: Theme.text
                    font.pixelSize: Theme.fontTitle
                    font.bold: true
                    Accessible.role: Accessible.Heading
                    Accessible.name: "让员工做什么"
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
                        placeholderText: "例如：帮我写一份产品分析报告，输出 Markdown"
                        color: Theme.text
                        placeholderTextColor: Theme.textFaint
                        font.pixelSize: Theme.fontBody
                        wrapMode: TextArea.Wrap
                        background: null
                        selectByMouse: true
                        Accessible.name: "描述你想让员工完成的事"
                        Keys.onPressed: function (event) {
                            // Ctrl+Enter 提交。单独的 Enter 必须继续换行：一个目标经常
                            // 不止一行。
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
                        // 任务书 §4 给的例子。换掉之前那四个是因为它们描述的是产品功能
                        // （"根据知识库回答问题"），而这四个描述的是用户想干的事。
                        model: [
                            "帮我写毕业论文",
                            "整理资料",
                            "制作 PPT",
                            "分析代码",
                            "生成报告"
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
                        text: "交给员工执行"
                        variant: "primary"
                        onClicked: page.executeGoal()
                    }
                }
            }
        }

        // ============================================================ 最近成果 ==
        Card {
            Layout.fillWidth: true
            title: "最近成果"
            subtitle: page.deliverablesState === "ready" && page.deliverablesTotal > 0
                      ? page.deliverablesTotal + " 件，最新的在前面"
                      : ""

            RowLayout {
                Layout.fillWidth: true
                spacing: Theme.space2

                Text {
                    Layout.fillWidth: true
                    visible: page.deliverablesState === "ready" && page.deliverables.length > 0
                    text: "每一件都能直接打开；点「继续修改」把它交回给员工接着做。"
                    color: Theme.textFaint
                    font.pixelSize: Theme.fontTiny
                }
                Button {
                    text: "全部成果"
                    variant: "ghost"
                    onClicked: page.go("deliverables")
                }
            }

            StateBlock {
                Layout.fillWidth: true
                state: page.deliverablesViewState
                busyText: "正在读取成果…"
                emptyText: "还没有成果。在上面写一句你想做的事，员工做完就会出现在这里。"
                errorText: page.deliverablesError
                errorHint: page.deliverablesErrorHint
                errorDetail: page.deliverablesErrorDetail
                onRetry: page.loadDeliverables()
            }

            Repeater {
                model: page.deliverables
                delegate: Rectangle {
                    required property var modelData

                    Layout.fillWidth: true
                    implicitHeight: row.implicitHeight + Theme.space3 * 2
                    radius: Theme.radiusMd
                    color: itemMouse.containsMouse ? Theme.bgCardHover : "transparent"
                    border.width: 1
                    border.color: Theme.divider

                    RowLayout {
                        id: row
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.verticalCenter: parent.verticalCenter
                        anchors.margins: Theme.space3
                        spacing: Theme.space3

                        Text {
                            text: page.kindGlyph(modelData.kind)
                            font.pixelSize: 22
                            Layout.alignment: Qt.AlignTop
                        }

                        ColumnLayout {
                            Layout.fillWidth: true
                            Layout.minimumWidth: 120
                            spacing: 2

                            Text {
                                Layout.fillWidth: true
                                text: modelData.title || modelData.path || "(未命名)"
                                color: Theme.text
                                font.pixelSize: Theme.fontBody
                                font.bold: true
                                elide: Text.ElideRight
                                Accessible.name: text
                            }
                            Text {
                                Layout.fillWidth: true
                                text: [
                                    page.kindLabel(modelData.kind),
                                    page.humanSize(modelData.size_bytes),
                                    modelData.version > 1 ? "第 " + modelData.version + " 版" : "",
                                    page.relativeTime(modelData.created_at)
                                ].filter(function (s) { return s !== "" }).join(" · ")
                                color: Theme.textFaint
                                font.pixelSize: Theme.fontTiny
                                elide: Text.ElideRight
                            }
                        }

                        Text {
                            text: page.deliverableStatus(modelData)
                            color: page.deliverableTone(modelData)
                            font.pixelSize: Theme.fontTiny
                        }

                        // 固定宽度的操作列：按钮的 x 不随文件名长短浮动。上一轮审计
                        // 抓到的正是这一类错位 —— 一排按钮跟着卡片内容整体平移。
                        RowLayout {
                            Layout.preferredWidth: 168
                            Layout.alignment: Qt.AlignVCenter
                            spacing: Theme.space2

                            Button {
                                Layout.preferredWidth: 76
                                text: "打开"
                                variant: "secondary"
                                enabled: !!modelData.path
                                Accessible.name: "打开 " + (modelData.title || "")
                                onClicked: page.openDeliverable(modelData)
                            }
                            Button {
                                Layout.preferredWidth: 84
                                text: "继续修改"
                                variant: "ghost"
                                Accessible.name: "继续修改 " + (modelData.title || "")
                                onClicked: page.continueFrom(modelData)
                            }
                        }
                    }

                    MouseArea {
                        id: itemMouse
                        anchors.fill: parent
                        hoverEnabled: true
                        acceptedButtons: Qt.NoButton
                    }
                }
            }
        }

        // ============================================================ 服务状态 ==
        //
        // 只有一行，而且是给"不能用"这种情况准备的：模型没配 Key 的时候，用户需要
        // 立刻知道，并且一步就能去配。正常情况下它不抢注意力。
        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.space3

            Text {
                Layout.fillWidth: true
                text: page.dashState === "loading" ? "正在检查 AI 服务…"
                    : page.dashState === "error"
                      ? "AI 服务状态未知：" + page.dashError
                    : (page.llm.api_key_set
                       ? "AI 服务：" + (page.llm.model || "已配置") + "，可以调用云端模型。"
                       : "AI 服务：未配置 API Key，当前用内置离线引擎。功能可用，回答质量不如云端模型。")
                color: page.dashState === "error" ? Theme.warn
                     : (page.summary && page.llm.api_key_set ? Theme.textFaint : Theme.warn)
                font.pixelSize: Theme.fontTiny
                wrapMode: Text.WordWrap
            }
            Button {
                text: page.llm.api_key_set ? "AI 设置" : "填写 API Key"
                variant: page.llm.api_key_set ? "ghost" : "secondary"
                onClicked: page.go("settings")
            }
        }

        Item { Layout.preferredHeight: Theme.space5 }
    }
}
