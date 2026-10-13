import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "../components"
import "../theme.js" as Theme

// The roster: who works here, what each one is for, and what they are allowed to touch.
//
// Three things on this page are not decoration:
//
//   * `unknown_skills` — an employee whose stored tool list names a tool this build does
//     not have. It is shown as a warning rather than filtered out, because the alternative
//     is an employee that silently cannot do what its own contract says.
//   * the permission line — the *narrowing* the employee applies to the user's policy,
//     read from the backend rather than summarised here. A page that described its own
//     idea of the permissions would be a second answer to a security question.
//   * the persona check — the same findings the API returns, shown before saving rather
//     than after. A persona cannot grant a permission (the permission service reads the
//     database before a tool runs, not the prompt), but one that says it has will produce
//     an employee that argues instead of asking.
Flickable {
    id: page
    property var shell

    contentWidth: width
    contentHeight: column.implicitHeight
    clip: true

    property var employees: []
    property var templates: []
    property string state: "loading"
    property string errorText: ""
    property string errorDetail: ""
    property bool busy: false

    //: Which employee is expanded for editing. "" = none.
    property string editing: ""
    property string draftName: ""
    property string draftMission: ""
    property string draftPersona: ""
    property var personaFindings: []

    function load() {
        var id = "employees-" + Date.now()
        state = "loading"
        shell.request("员工名册", id, function (payload) {
            page.employees = payload.items ?? []
            page.state = "ready"
        }, function (title, code, human) {
            page.state = "error"
            page.errorText = title
            page.errorDetail = (human && human.detail) ? human.detail : code
        })
        bridge.loadEmployees(id)
    }

    function loadTemplates() {
        var id = "templates-" + Date.now()
        shell.request("岗位模板", id, function (payload) {
            page.templates = payload.templates ?? []
        })
        bridge.loadEmployeeTemplates(id)
    }

    function hire(roleId) {
        if (busy) return
        busy = true
        var id = "hire-" + Date.now()
        shell.request("招聘", id, function (payload) {
            page.busy = false
            shell.toast("info", "已创建：" + payload.name)
            page.load()
        }, function (title) {
            page.busy = false
            shell.toast("error", title)
        })
        bridge.createEmployee(id, JSON.stringify({ role_id: roleId }))
    }

    function beginEdit(employee) {
        editing = employee.id
        draftName = employee.name ?? ""
        draftMission = employee.mission ?? ""
        draftPersona = employee.persona ?? ""
        personaFindings = []
    }

    function checkPersona() {
        if (draftPersona.trim() === "") {
            personaFindings = []
            return
        }
        var id = "persona-" + Date.now()
        shell.request("人格检查", id, function (payload) {
            page.personaFindings = payload.clean ? [] : (payload.findings ?? [])
        })
        bridge.reviewPersona(id, draftPersona)
    }

    function save() {
        if (busy) return
        busy = true
        var id = "save-" + Date.now()
        shell.request("保存员工", id, function () {
            page.busy = false
            page.editing = ""
            shell.toast("info", "已保存")
            page.load()
        }, function (title) {
            page.busy = false
            shell.toast("error", title)
        })
        bridge.updateEmployee(id, editing, JSON.stringify({
            name: draftName,
            mission: draftMission,
            persona: draftPersona
        }))
    }

    function setArchived(employee, archived) {
        var id = "arch-" + Date.now()
        shell.request("归档", id, function () {
            shell.toast("info", archived ? "已归档" : "已恢复")
            page.load()
        })
        bridge.setEmployeeArchived(id, employee.id, archived)
    }

    function remove(employee) {
        var id = "del-" + Date.now()
        shell.request("删除员工", id, function () {
            shell.toast("info", "已删除：" + employee.name)
            page.load()
        }, function (title) {
            // The refusal is the interesting case: an employee with a history is archived,
            // not deleted, and the message says why.
            shell.toast("warn", title)
        })
        bridge.deleteEmployee(id, employee.id)
    }

    //: The narrowing the backend reports, as one readable line. Empty = same as the user's.
    //:
    //: The backend sends `PermissionLevel.label()` values — "Observe only" — because that
    //: is what an API response should carry. This is the window, so it says it in the
    //: window's language.
    function narrowingText(employee) {
        var n = employee.effective_narrowing ?? {}
        var parts = []
        if (n.level) parts.push("权限降到「" + Theme.permissionLevelLabel(n.level.to_value) + "」")
        if (n.mode) parts.push("模式改为「" + Theme.agentModeLabel(n.mode.to) + "」")
        if (n.max_tool_calls) parts.push("工具调用上限 " + n.max_tool_calls.to)
        if (n.max_runtime_seconds) parts.push("单次最长 " + n.max_runtime_seconds.to + " 秒")
        if (n.allowed_paths) parts.push("可写目录 " + n.allowed_paths.to + " 个")
        if (n.denied_paths) parts.push("禁止目录 " + n.denied_paths.to + " 个")
        return parts.length ? "比你的设置更窄：" + parts.join("、") : "权限与你的设置一致"
    }

    // --------------------------------------------------------------- 岗位合同 --
    //
    // 任务书 §八。**这些字段一直都在，只是没人显示。** 岗位模板里有 17 个字段，员工行
    // 持久化了 11 个，`EmployeeOut` 把它们全都发给了界面 —— 而界面此前只渲染了 6 个。
    //
    // 取数规则在 `theme.js`（`contractFor` / `contractField` / `contractLines`），
    // 因为员工页和合同组件要用同一套；渲染在 `components/EmployeeContract.qml`。
    // 这里只留"哪一份合同是展开的"这一个状态。
    property string contractOpen: ""

    property bool _loaded: false
    function _firstLoad() {
        if (_loaded || !shell) return
        _loaded = true
        load()
        loadTemplates()
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
                text: "我的员工"
                color: Theme.text
                font.pixelSize: 24
                font.bold: true
                Layout.fillWidth: true
            }
            Text {
                text: page.employees.length + " 人"
                color: Theme.textMuted
                font.pixelSize: 12
            }
        }

        StateBlock {
            Layout.fillWidth: true
            state: page.state
            busyText: "正在读取员工名册…"
            emptyText: "还没有员工。下面选一个岗位开始。"
            errorText: page.errorText
            errorDetail: page.errorDetail
            onRetry: page.load()
        }

        // ------------------------------------------------------------ 招聘 --
        Card {
            Layout.fillWidth: true
            title: "招聘"
            subtitle: "岗位模板是起点，不是限制。创建后可以改使命、人格和权限。"

            Flow {
                Layout.fillWidth: true
                spacing: 10

                Repeater {
                    model: page.templates

                    delegate: Rectangle {
                        required property var modelData
                        width: 210
                        height: templateColumn.implicitHeight + 28
                        radius: 10
                        color: Theme.surface
                        border.width: 1
                        border.color: hover.hovered ? Theme.borderFocus : Theme.border

                        HoverHandler { id: hover }

                        ColumnLayout {
                            id: templateColumn
                            anchors.left: parent.left
                            anchors.right: parent.right
                            anchors.verticalCenter: parent.verticalCenter
                            anchors.margins: 14
                            spacing: 6

                            RowLayout {
                                spacing: 8
                                NavIcon {
                                    name: "person"
                                    size: 16
                                    color: Theme.accent
                                }
                                Text {
                                    text: modelData.name
                                    color: Theme.text
                                    font.pixelSize: 14
                                    font.bold: true
                                    Layout.fillWidth: true
                                }
                            }
                            Text {
                                text: modelData.summary
                                color: Theme.textMuted
                                font.pixelSize: 11
                                wrapMode: Text.WordWrap
                                Layout.fillWidth: true
                            }
                            Text {
                                text: (modelData.tools ?? []).length + " 个工具 · "
                                      + modelData.deliverable_kind
                                color: Theme.textFaint
                                font.pixelSize: 10
                            }
                            Button {
                                text: page.busy ? "…" : "创建"
                                variant: "secondary"
                                enabled: !page.busy
                                onClicked: page.hire(modelData.role_id)
                            }
                        }
                    }
                }
            }
        }

        // ------------------------------------------------------------- 名册 --
        Repeater {
            model: page.employees

            delegate: Rectangle {
                required property var modelData
                Layout.fillWidth: true
                Layout.preferredHeight: cardColumn.implicitHeight + 28
                radius: 10
                color: modelData.archived ? Theme.surface : Theme.bgCard
                border.width: 1
                border.color: page.editing === modelData.id ? Theme.borderFocus : Theme.border
                opacity: modelData.archived ? 0.72 : 1

                ColumnLayout {
                    id: cardColumn
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.top: parent.top
                    anchors.margins: 14
                    spacing: 8

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 10

                        Rectangle {
                            width: 34
                            height: 34
                            radius: 17
                            color: Theme.surface
                            border.width: 1
                            border.color: Theme.border

                            Text {
                                anchors.centerIn: parent
                                // 岗位的记号，不是名字的首字。首字把"小文"和"小武"画成
                                // 同一个圈，而这个位置要回答的是"这是干什么的"。
                                text: Theme.roleGlyph(modelData.role_id)
                                font.pixelSize: 15
                                Accessible.name: Theme.roleName(Theme.contractFor(modelData, page.templates), modelData.role_id)
                            }
                        }

                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 2
                            RowLayout {
                                spacing: 8
                                Text {
                                    text: modelData.name
                                    color: Theme.text
                                    font.pixelSize: 15
                                    font.bold: true
                                }
                                StatusPill {
                                    status: modelData.status
                                    label: Theme.employeeStatusLabel(modelData.status)
                                }
                                Text {
                                    visible: modelData.archived
                                    text: "已归档"
                                    color: Theme.textFaint
                                    font.pixelSize: 11
                                }
                            }
                            Text {
                                text: Theme.roleName(Theme.contractFor(modelData, page.templates), modelData.role_id)
                                      + " · 第 " + modelData.version
                                      + " 版 · " + (modelData.skills ?? []).length + " 个工具"
                                color: Theme.textFaint
                                font.pixelSize: 11
                            }
                        }

                        // **A fixed column, not "whatever is left".** These three used to
                        // be laid straight into the row beside a `fillWidth` name block, so
                        // their x depended on how wide that block's *minimum* turned out to
                        // be — which depends on the employee's name. The measured result was
                        // the same three buttons at 473/579/685 in one card and
                        // 555/661/767 in the next: an 82px drift that reads as sloppy even
                        // though every card is internally consistent.
                        RowLayout {
                            spacing: 8
                            Layout.preferredWidth: 320
                            Layout.alignment: Qt.AlignRight | Qt.AlignVCenter

                            Item { Layout.fillWidth: true }

                            Button {
                                text: page.editing === modelData.id ? "收起" : "编辑"
                                onClicked: page.editing === modelData.id
                                           ? page.editing = ""
                                           : page.beginEdit(modelData)
                            }
                            Button {
                                text: modelData.archived ? "恢复" : "归档"
                                onClicked: page.setArchived(modelData, !modelData.archived)
                            }
                            Button {
                                text: "删除"
                                variant: "danger"
                                // Only offered for an employee that never ran anything. The
                                // backend refuses the rest and says why; hiding the button
                                // for the common case keeps the refusal for the case that
                                // needs explaining.
                                enabled: modelData.version === 1 && modelData.status === "idle"
                                onClicked: page.remove(modelData)
                            }
                        }
                    }

                    Text {
                        visible: (modelData.mission ?? "") !== ""
                        text: modelData.mission
                        color: Theme.textMuted
                        font.pixelSize: 12
                        wrapMode: Text.WordWrap
                        Layout.fillWidth: true
                    }

                    Text {
                        text: page.narrowingText(modelData)
                        color: Theme.textFaint
                        font.pixelSize: 11
                        wrapMode: Text.WordWrap
                        Layout.fillWidth: true
                    }

                    // An employee claiming a tool this build does not have. Shown, not
                    // hidden: the contract says it can do something it cannot.
                    Text {
                        visible: (modelData.unknown_skills ?? []).length > 0
                        text: "⚠ 本版本没有这些工具：" + (modelData.unknown_skills ?? []).join("、")
                        color: Theme.warn
                        font.pixelSize: 11
                        wrapMode: Text.WordWrap
                        Layout.fillWidth: true
                    }

                    // -------------------------------------------------- 岗位合同 --
                    //
                    // 任务书 §八要的那几项（岗位 / 使命 / 能力 / 工作方式 / 限制）全部在
                    // 合同里，而合同此前一个字都没显示。默认收起：一屏 12 节会把名册
                    // 撑成文档，而名册的用途是"挑一个员工"，不是"读它的合同"。
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: Theme.space2

                        Text {
                            Layout.fillWidth: true
                            text: "岗位合同：使命、职责、工作方式、限制、验收标准"
                            color: Theme.textFaint
                            font.pixelSize: Theme.fontTiny
                            elide: Text.ElideRight
                        }
                        Button {
                            objectName: "employee-contract-" + modelData.id
                            text: page.contractOpen === modelData.id ? "收起合同" : "查看合同"
                            variant: "ghost"
                            Accessible.name: (page.contractOpen === modelData.id ? "收起" : "查看")
                                             + (modelData.name ?? "") + "的岗位合同"
                            onClicked: page.contractOpen =
                                       page.contractOpen === modelData.id ? "" : modelData.id
                        }
                    }

                    EmployeeContract {
                        Layout.fillWidth: true
                        visible: page.contractOpen === modelData.id
                        employee: modelData
                        templates: page.templates
                    }

                    // -------------------------------------------------- 编辑 --
                    ColumnLayout {
                        Layout.fillWidth: true
                        visible: page.editing === modelData.id
                        spacing: 8

                        TextField {
                            id: nameField
                            Layout.fillWidth: true
                            placeholderText: "名字"
                            text: page.draftName
                            color: Theme.text
                            onTextChanged: page.draftName = text
                            background: Rectangle {
                                radius: 8
                                color: Theme.surface
                                border.width: nameField.activeFocus ? 2 : 1
                                border.color: nameField.activeFocus ? Theme.borderFocus : Theme.border
                            }
                        }

                        TextField {
                            id: missionField
                            Layout.fillWidth: true
                            placeholderText: "使命：这个员工为什么存在"
                            text: page.draftMission
                            color: Theme.text
                            onTextChanged: page.draftMission = text
                            background: Rectangle {
                                radius: 8
                                color: Theme.surface
                                border.width: missionField.activeFocus ? 2 : 1
                                border.color: missionField.activeFocus ? Theme.borderFocus : Theme.border
                            }
                        }

                        TextField {
                            id: personaField
                            Layout.fillWidth: true
                            placeholderText: "人格：怎么说话（不决定它能做什么）"
                            text: page.draftPersona
                            color: Theme.text
                            onTextChanged: {
                                page.draftPersona = text
                            }
                            onEditingFinished: page.checkPersona()
                            background: Rectangle {
                                radius: 8
                                color: Theme.surface
                                border.width: personaField.activeFocus ? 2 : 1
                                border.color: personaField.activeFocus ? Theme.borderFocus : Theme.border
                            }
                        }

                        Text {
                            Layout.fillWidth: true
                            visible: page.personaFindings.length > 0
                            text: "⚠ 这段话里有试图改变规则、跳过授权或套取提示词的句子。"
                                  + "它不会真的改变权限——权限在执行端检查，不读提示词——"
                                  + "但会让员工在运行中表现得不听话。"
                            color: Theme.warn
                            font.pixelSize: 11
                            wrapMode: Text.WordWrap
                        }

                        RowLayout {
                            spacing: 8
                            Button {
                                text: "检查人格"
                                onClicked: page.checkPersona()
                            }
                            Button {
                                text: page.busy ? "保存中…" : "保存"
                                variant: "primary"
                                enabled: !page.busy
                                onClicked: page.save()
                            }
                            Item { Layout.fillWidth: true }
                        }
                    }
                }
            }
        }

        Item { Layout.preferredHeight: 24 }
    }
}
