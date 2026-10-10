import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "../components"
import "../theme.js" as Theme

// Agent permissions: what is actually in force, and what the policy does about a path.
//
// The point of this page is that **nothing here is a copy of the rules**. Every value comes
// from `policy_store.describe()`, every change goes through `policy_store.save_overrides()`,
// and the path probe runs `PermissionManager.check_path()` — the same call `file_write`
// makes before it touches a file. A screen with its own implementation of the rules would be
// a second source of truth, and the second one is always the one that drifts; the screen is
// the one people believe.
//
// Three things it deliberately does not do:
//
//  * **It does not hide the tools the policy excludes.** They are listed with the reason.
//    Someone opening this page is asking "what can the agent do", and a list that silently
//    drops things answers a different question.
//  * **It does not present a protected path as something a higher level would unlock.**
//    `.ssh`, credential stores and the project's own `.env` are merged back in from the
//    built-in policy on every load; the probe says `拒绝` and says why, at every level.
//  * **It does not soften a refusal.** The verdict's own words are shown, including the
//    remedy, because a refusal a person cannot act on is just a wall.
Flickable {
    id: page
    property var shell

    contentWidth: width
    contentHeight: column.implicitHeight
    clip: true

    property var policy: null
    property var capabilities: []
    property var tools: []
    property var excluded: []
    property string probePath: ""
    property bool probeWrite: false
    property var probeResult: null
    property bool busy: false
    property bool _loaded: false

    function load() {
        busy = true
        var a = "perm-" + Date.now()
        shell.request("权限策略", a, function (payload) {
            page.policy = payload
            page.busy = false
        })
        bridge.loadPermissions(a)

        var b = "caps-" + Date.now()
        shell.request("能力分组", b, function (payload) {
            page.capabilities = payload.capabilities || []
        })
        bridge.loadCapabilities(b)

        var c = "tools-" + Date.now()
        shell.request("工具清单", c, function (payload) {
            page.tools = payload.tools || []
            page.excluded = payload.excluded || []
        })
        bridge.loadTools(c)
    }

    function apply(patch) {
        var id = "perm-save-" + Date.now()
        shell.request("保存权限", id, function (payload) {
            page.policy = payload
            shell.toast("info", "权限已更新，下一次工具调用就会按新策略执行")
            page.load()
        })
        bridge.savePermissions(id, JSON.stringify(patch))
    }

    function reset() {
        var id = "perm-reset-" + Date.now()
        shell.request("重置权限", id, function (payload) {
            page.policy = payload
            shell.toast("info", "已恢复默认策略")
            page.load()
        })
        bridge.resetPermissions(id)
    }

    function toggleCapability(key, on) {
        var id = "caps-save-" + Date.now()
        shell.request("保存能力", id, function (payload) {
            shell.toast("info", "能力已更新，可用工具 " + payload.tool_count + " 个")
            page.load()
        })
        var patch = {}
        patch[key] = on
        bridge.saveCapabilities(id, JSON.stringify(patch))
    }

    function probe() {
        if (probePath.trim() === "") {
            shell.toast("warn", "先写一个路径")
            return
        }
        var id = "perm-check-" + Date.now()
        shell.request("权限检查", id, function (payload) {
            page.probeResult = payload
        })
        bridge.checkPermission(id, probePath, probeWrite)
    }

    function decisionWord(decision) {
        switch (decision) {
        case "allow":   return "允许"
        case "confirm": return "需要确认"
        case "deny":    return "拒绝"
        default:        return decision || "未知"
        }
    }

    function decisionColor(decision) {
        switch (decision) {
        case "allow":   return Theme.success
        case "confirm": return Theme.warn
        case "deny":    return Theme.danger
        default:        return Theme.textMuted
        }
    }

    Component.onCompleted: load()

    ColumnLayout {
        id: column
        width: page.width
        spacing: 16

        RowLayout {
            Layout.fillWidth: true
            spacing: 12
            Text {
                text: "Agent 权限"
                color: Theme.text
                font.pixelSize: 24
                font.bold: true
            }
            Item { Layout.fillWidth: true }
            Button {
                objectName: "permissions-reset"
                text: "恢复默认"
                enabled: page.policy !== null
                onClicked: page.reset()
                Accessible.name: "恢复默认权限策略"
            }
        }

        // ---------------------------------------------------- what is in force --
        Card {
            Layout.fillWidth: true
            title: "实际生效的策略"
            subtitle: page.policy === null
                      ? "读取中…"
                      : (page.policy.source === "runtime"
                         ? "来自本机保存的改动（overrides_active=" + page.policy.overrides_active + "）"
                         : "来自默认配置，没有本机改动")

            RowLayout {
                Layout.fillWidth: true
                spacing: 24
                visible: page.policy !== null

                ColumnLayout {
                    spacing: 2
                    Text { text: "运行模式"; color: Theme.textMuted; font.pixelSize: 11 }
                    Text {
                        objectName: "permissions-mode"
                        text: page.policy ? page.policy.mode : ""
                        color: Theme.text
                        font.pixelSize: 16
                        font.family: "Consolas"
                    }
                }
                ColumnLayout {
                    spacing: 2
                    Text { text: "权限级别"; color: Theme.textMuted; font.pixelSize: 11 }
                    Text {
                        objectName: "permissions-level"
                        text: page.policy ? (page.policy.level + " · " + page.policy.level_label) : ""
                        color: Theme.accent
                        font.pixelSize: 16
                    }
                }
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 2
                    Text { text: "工作区"; color: Theme.textMuted; font.pixelSize: 11 }
                    Text {
                        Layout.fillWidth: true
                        text: page.policy ? page.policy.workspace : ""
                        color: Theme.textFaint
                        font.pixelSize: 12
                        font.family: "Consolas"
                        elide: Text.ElideMiddle
                    }
                }
            }

            Text {
                Layout.fillWidth: true
                visible: page.policy !== null
                wrapMode: Text.WordWrap
                color: Theme.textFaint
                font.pixelSize: 11
                text: page.policy
                      ? ("受保护的位置（.ssh、凭据库、浏览器配置、Windows、Program Files，以及本项目的 .env）"
                         + "始终被拒绝，改动这里的任何设置都不会解除。")
                      : ""
            }
        }

        // ------------------------------------------------------------- modes --
        Card {
            Layout.fillWidth: true
            title: "运行模式"
            subtitle: "Assist 只分析；Supervised 执行但高风险要确认；Autonomous 在授权范围内自主完成"

            Repeater {
                model: page.policy ? (page.policy.modes || []) : []

                delegate: Rectangle {
                    required property var modelData
                    Layout.fillWidth: true
                    implicitHeight: modeRow.implicitHeight + 18
                    radius: 8
                    color: Theme.bgElevated
                    border.width: page.policy && page.policy.mode === modelData.value ? 2 : 1
                    border.color: page.policy && page.policy.mode === modelData.value
                                  ? Theme.borderFocus : Theme.border

                    Accessible.role: Accessible.RadioButton
                    Accessible.name: modelData.label + "：" + modelData.description
                    Accessible.checked: page.policy && page.policy.mode === modelData.value

                    MouseArea {
                        anchors.fill: parent
                        onClicked: page.apply({ mode: modelData.value })
                    }

                    RowLayout {
                        id: modeRow
                        anchors.fill: parent
                        anchors.margins: 9
                        spacing: 10
                        Text {
                            text: modelData.label
                            color: Theme.text
                            font.pixelSize: 14
                            font.bold: true
                        }
                        Text {
                            Layout.fillWidth: true
                            text: modelData.description
                            color: Theme.textMuted
                            font.pixelSize: 12
                            elide: Text.ElideRight
                        }
                    }
                }
            }
        }

        // ------------------------------------------------------------ levels --
        Card {
            Layout.fillWidth: true
            title: "权限级别"
            subtitle: "级别决定工具能不能跑；受保护路径不受级别影响"

            Repeater {
                model: page.policy ? (page.policy.levels || []) : []

                delegate: Rectangle {
                    required property var modelData
                    Layout.fillWidth: true
                    implicitHeight: levelRow.implicitHeight + 14
                    radius: 8
                    color: Theme.bgElevated
                    border.width: page.policy && page.policy.level === modelData.value ? 2 : 1
                    border.color: page.policy && page.policy.level === modelData.value
                                  ? Theme.borderFocus : Theme.border

                    Accessible.role: Accessible.RadioButton
                    Accessible.name: "权限级别 " + modelData.label
                    Accessible.checked: page.policy && page.policy.level === modelData.value

                    MouseArea {
                        anchors.fill: parent
                        onClicked: page.apply({ level: modelData.value })
                    }

                    RowLayout {
                        id: levelRow
                        anchors.fill: parent
                        anchors.margins: 7
                        spacing: 10
                        Text {
                            text: modelData.value
                            color: Theme.textFaint
                            font.pixelSize: 12
                            font.family: "Consolas"
                        }
                        Text {
                            Layout.fillWidth: true
                            text: modelData.label
                            color: Theme.text
                            font.pixelSize: 14
                        }
                    }
                }
            }
        }

        // ------------------------------------------------------------- probe --
        Card {
            Layout.fillWidth: true
            title: "问一下策略：这个路径会怎样？"
            subtitle: "用的是 PermissionManager 本身——也就是 file_write 在动文件之前问的同一个问题"

            RowLayout {
                Layout.fillWidth: true
                spacing: 10

                TextField {
                    id: probeField
                    objectName: "permissions-probe-input"
                    Layout.fillWidth: true
                    text: page.probePath
                    onTextChanged: page.probePath = text
                    color: Theme.text
                    font.pixelSize: 13
                    placeholderText: "例如 C:\\Users\\you\\.ssh\\id_rsa 或工作区里的一个文件"
                    Accessible.role: Accessible.EditableText
                    Accessible.name: "要检查的路径"
                    background: Rectangle {
                        radius: 8
                        color: Theme.bgElevated
                        border.width: probeField.activeFocus ? 2 : 1
                        border.color: probeField.activeFocus ? Theme.borderFocus : Theme.border
                    }
                }

                CheckBox {
                    objectName: "permissions-probe-write"
                    text: "写入"
                    checked: page.probeWrite
                    onToggled: page.probeWrite = checked
                    Accessible.name: "按写入检查"
                }

                Button {
                    objectName: "permissions-probe-run"
                    text: "检查"
                    onClicked: page.probe()
                    Accessible.name: "检查这个路径"
                }
            }

            Rectangle {
                Layout.fillWidth: true
                objectName: "permissions-probe-result"
                visible: page.probeResult !== null
                implicitHeight: probeColumn.implicitHeight + 20
                radius: 8
                color: Theme.bgElevated
                border.width: 2
                border.color: page.probeResult
                              ? page.decisionColor(page.probeResult.verdict.decision) : Theme.border

                Accessible.role: Accessible.StaticText
                Accessible.name: page.probeResult
                                 ? ("检查结果：" + page.decisionWord(page.probeResult.verdict.decision)
                                    + "。" + page.probeResult.verdict.reason)
                                 : ""

                ColumnLayout {
                    id: probeColumn
                    anchors.fill: parent
                    anchors.margins: 10
                    spacing: 6

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 10
                        Text {
                            text: page.probeResult
                                  ? page.decisionWord(page.probeResult.verdict.decision) : ""
                            color: page.probeResult
                                   ? page.decisionColor(page.probeResult.verdict.decision) : Theme.text
                            font.pixelSize: 15
                            font.bold: true
                        }
                        Text {
                            Layout.fillWidth: true
                            text: page.probeResult
                                  ? (page.probeResult.write ? "写入" : "读取") + " · "
                                    + page.probeResult.verdict.risk + " 风险"
                                  : ""
                            color: Theme.textFaint
                            font.pixelSize: 11
                        }
                    }

                    Text {
                        Layout.fillWidth: true
                        wrapMode: Text.WordWrap
                        color: Theme.text
                        font.pixelSize: 13
                        text: page.probeResult ? page.probeResult.verdict.reason : ""
                    }

                    Text {
                        Layout.fillWidth: true
                        visible: page.probeResult && page.probeResult.verdict.detail !== ""
                        wrapMode: Text.WordWrap
                        color: Theme.textMuted
                        font.pixelSize: 12
                        text: page.probeResult ? page.probeResult.verdict.detail : ""
                    }

                    Text {
                        Layout.fillWidth: true
                        visible: page.probeResult && page.probeResult.verdict.impact !== ""
                        wrapMode: Text.WordWrap
                        color: Theme.textFaint
                        font.pixelSize: 12
                        text: page.probeResult ? ("怎么办：" + page.probeResult.verdict.impact) : ""
                    }
                }
            }
        }

        // -------------------------------------------------------- capabilities --
        Card {
            Layout.fillWidth: true
            title: "能力分组"
            subtitle: "关掉一组，那组工具就从模型的清单里消失——不是留着再拒绝，那样每次都要读一遍拒绝"

            Repeater {
                model: page.capabilities

                delegate: Rectangle {
                    required property var modelData
                    Layout.fillWidth: true
                    implicitHeight: capRow.implicitHeight + 14
                    radius: 8
                    color: Theme.bgElevated
                    border.width: 1
                    border.color: Theme.border

                    RowLayout {
                        id: capRow
                        anchors.fill: parent
                        anchors.margins: 7
                        spacing: 10

                        Switch {
                            objectName: "cap-" + modelData.key
                            checked: modelData.user_enabled
                            onToggled: page.toggleCapability(modelData.key, checked)
                            Accessible.name: modelData.label
                        }
                        Text {
                            text: modelData.label
                            color: Theme.text
                            font.pixelSize: 14
                            Layout.preferredWidth: 120
                        }
                        Text {
                            Layout.fillWidth: true
                            text: modelData.description
                            color: Theme.textMuted
                            font.pixelSize: 12
                            elide: Text.ElideRight
                        }
                        Text {
                            text: modelData.tools.length + " 个工具"
                            color: Theme.textFaint
                            font.pixelSize: 11
                        }
                    }
                }
            }
        }

        // ------------------------------------------------------------- tools --
        Card {
            Layout.fillWidth: true
            title: "工具清单"
            subtitle: page.tools.length + " 个工具，其中 " + page.tools.filter(function (t) {
                return t.available
            }).length + " 个当前可用"

            Text {
                Layout.fillWidth: true
                visible: page.excluded.length > 0
                wrapMode: Text.WordWrap
                color: Theme.warn
                font.pixelSize: 12
                text: "当前策略排除了 " + page.excluded.length + " 个工具："
                      + page.excluded.slice(0, 8).map(function (e) {
                            return e.name + "（" + e.reason + "）"
                        }).join("、")
                      + (page.excluded.length > 8 ? " 等" : "")
            }

            Repeater {
                model: page.tools

                delegate: Rectangle {
                    required property var modelData
                    Layout.fillWidth: true
                    implicitHeight: toolRow.implicitHeight + 12
                    radius: 6
                    color: Theme.bgElevated
                    border.width: 1
                    border.color: modelData.available ? Theme.border : Theme.warn

                    Accessible.role: Accessible.StaticText
                    Accessible.name: modelData.name + "，"
                                     + (modelData.available ? "可用" : "不可用")

                    RowLayout {
                        id: toolRow
                        anchors.fill: parent
                        anchors.margins: 6
                        spacing: 10

                        Text {
                            text: modelData.name
                            color: modelData.available ? Theme.text : Theme.textMuted
                            font.pixelSize: 12
                            font.family: "Consolas"
                            Layout.preferredWidth: 160
                            elide: Text.ElideRight
                        }
                        Text {
                            Layout.fillWidth: true
                            text: modelData.description
                            color: Theme.textFaint
                            font.pixelSize: 11
                            elide: Text.ElideRight
                        }
                        Text {
                            text: modelData.permission_label
                            color: Theme.textFaint
                            font.pixelSize: 11
                        }
                        StatusPill {
                            status: modelData.available ? "SUCCEEDED" : "UNKNOWN"
                        }
                    }
                }
            }
        }

        Item { Layout.preferredHeight: 24 }
    }
}
