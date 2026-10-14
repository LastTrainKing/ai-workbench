import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import QtQuick.Dialogs
import "../components"
import "../theme.js" as Theme
import "../errors.js" as Errors

// The Provider configuration entry point, and the file operations.
//
// This page is the answer to "when I have a new API key, how do I set it?" — type it here,
// press save, and it takes effect on the next call. No source edit, no rebuild, no key
// baked into an installer. The key is write-only: the page can tell you whether one is
// set and show a mask, and it can never read the value back.
Flickable {
    id: page
    property var shell

    contentWidth: width
    contentHeight: column.implicitHeight
    clip: true

    property var effective: null
    property var runtime: null
    //: The ladder from the backend: not_configured / configured_untested / connected /
    //: failed / offline. It arrives with the configuration so the screen cannot invent a
    //: second opinion from a different layer — which is what it used to do.
    property var status: null
    property bool testing: false
    property string provider: ""
    property string model: ""
    property string baseUrl: ""
    property string apiKey: ""
    property string statusText: ""
    property var embedding: null

    //: What each state is called, what it means, and what to do about it. Kept next to the
    //: state names so a state cannot be added without somebody deciding what it says.
    readonly property var ladder: ({
        offline: {
            word: "离线模式",
            hint: "当前用内置离线引擎，不需要 API Key。可以对话与整理，但它写不出交付文件，回答质量也不如云端模型。想换成云端模型：在上面填好 Provider、模型和 API Key，保存后点「测试连接」。",
            tone: Theme.textMuted
        },
        not_configured: {
            word: "未配置",
            hint: "还缺必需项。填好 Provider、模型和 API Key，保存后再测试连接。",
            tone: Theme.warn
        },
        configured_untested: {
            word: "已配置，未测试",
            hint: "配置已经保存，但还没有验证过连接。「已保存」不等于「能用」。",
            tone: Theme.accent
        },
        connected: {
            word: "连接成功",
            hint: "上一次测试拿到了真实响应。",
            tone: Theme.success
        },
        failed: {
            word: "连接失败",
            hint: "配置已保存，但上一次测试被拒绝。",
            tone: Theme.danger
        }
    })

    readonly property string stateWord:
        (status && ladder[status.state]) ? ladder[status.state].word : "…"
    readonly property string stateHint:
        (status && ladder[status.state]) ? ladder[status.state].hint : ""
    readonly property color stateTone:
        (status && ladder[status.state]) ? ladder[status.state].tone : Theme.textFaint

    //: Re-read everything this page shows, without leaving it.
    //:
    //: **Not a fix for anything.** The state that read 未配置 was not stale — it was computed
    //: from the process environment every single time, so reloading it returned the same
    //: wrong answer. This is here for the case a reload actually covers: the configuration
    //: was changed somewhere else (the file edited by hand, another window), and the page
    //: should be able to pick that up. It is also the one thing every other page has and
    //: this one did not.
    function reload() {
        page.statusText = ""
        loadConfig()
        loadRuntime()
        loadEmbedding()
    }

    function loadConfig() {
        var id = "cfg-" + Date.now()
        shell.request("读取 AI 配置", id, function (payload) {
            page.effective = payload.effective
            page.status = payload.status
            page.provider = payload.effective.provider
            page.model = payload.effective.model
            page.baseUrl = payload.effective.base_url
            page.apiKey = ""
        })
        bridge.loadAiConfig(id)
    }

    //: One real request to the configured endpoint, because "已配置" and "能用" are
    //: different claims and only one of them can be made without asking the server.
    function testConnection() {
        if (page.testing) return
        page.testing = true
        page.statusText = ""
        var id = "test-" + Date.now()
        shell.request("测试 AI 连接", id, function (payload) {
            page.testing = false
            page.status = payload.status
            var result = payload.result
            if (!result) {
                page.statusText = "还没有可测试的配置。"
                return
            }
            if (result.ok) {
                page.statusText = "连接成功，" + (result.latency_ms || 0) + " 毫秒。"
                shell.toast("info", "AI 连接正常")
            } else {
                page.statusText = "连接失败。"
                shell.toast("error", "连接失败：" +
                            Errors.human(result.error_message, result.error_code).title)
            }
        }, function (message, code, human) {
            page.testing = false
            page.statusText = message + (human && human.hint ? "　" + human.hint : "")
        })
        bridge.testAiConnection(id)
    }

    function loadEmbedding() {
        var id = "emb-" + Date.now()
        shell.request("读取 Embedding 状态", id, function (payload) { page.embedding = payload })
        bridge.loadEmbeddingStatus(id)
    }

    function loadRuntime() {
        var id = "rt-" + Date.now()
        shell.request("读取运行信息", id, function (payload) { page.runtime = payload })
        bridge.loadRuntimeInfo(id)
    }

    function save() {
        var patch = {}
        if (provider !== "") patch.provider = provider
        if (model !== "") patch.model = model
        if (baseUrl !== "") patch.base_url = baseUrl
        // Only send the key when something was typed: an empty box means "leave it alone",
        // not "erase it".
        if (apiKey !== "") patch.api_key = apiKey

        var id = "save-" + Date.now()
        shell.request("保存 AI 配置", id, function (payload) {
            page.effective = payload.effective
            page.status = payload.status
            page.apiKey = ""
            page.statusText = "已保存。下一次 AI 调用即生效，无需重启或重新编译。"
            shell.toast("info", "配置已保存")
            // The 运行信息 card reads the same account, so it is refreshed rather than
            // left showing what it said before the save.
            page.loadRuntime()
        })
        bridge.saveAiConfig(id, JSON.stringify(patch))
    }

    function clear() {
        var id = "clear-" + Date.now()
        shell.request("清除 AI 配置", id, function (payload) {
            page.effective = payload.effective
            page.statusText = "已清除本账号的覆盖，回退到安装级配置与 .env。"
            shell.toast("info", "已恢复默认")
            page.loadConfig()
            page.loadRuntime()
        })
        bridge.clearAiConfig(id)
    }

    function exportSummary() {
        var text = "AI Workbench 配置摘要\n"
                 + "版本: " + (runtime ? runtime.version : "?") + "\n"
                 + "provider: " + (effective ? effective.provider : "?") + "\n"
                 + "model: " + (effective ? effective.model : "?") + "\n"
                 + "base_url: " + (effective ? effective.base_url : "?") + "\n"
                 + "api_key_set: " + (effective ? effective.api_key_set : "?") + "\n"
        var id = "export-" + Date.now()
        shell.request("导出配置", id, function (payload) {
            page.statusText = "已写入 " + payload.path + "（" + payload.bytes + " 字节）"
        })
        bridge.saveTextFile(id, defaultExportPath(), text)
    }

    function defaultExportPath() {
        var root = runtime ? runtime.runtime_root : "."
        return root + "/config-summary.txt"
    }

    property bool _loaded: false

    function _firstLoad() {
        if (_loaded || !shell) return
        _loaded = true
        { loadConfig(); loadRuntime(); loadEmbedding() }
    }

    onShellChanged: _firstLoad()
    Component.onCompleted: _firstLoad()

    FileDialog {
        id: exportDialog
        title: "导出配置摘要"
        fileMode: FileDialog.SaveFile
        defaultSuffix: "txt"
        onAccepted: {
            var id = "export-dialog-" + Date.now()
            var path = String(selectedFile).replace(/^file:\/\/\//, "")
            var text = "AI Workbench 配置摘要\n版本: " + (page.runtime ? page.runtime.version : "?")
            shell.request("导出配置", id, function (payload) {
                page.statusText = "已写入 " + payload.path
            })
            bridge.saveTextFile(id, decodeURIComponent(path), text)
        }
    }

    ColumnLayout {
        id: column
        width: page.width
        spacing: 16

        Text {
            text: "设置"
            color: Theme.text
            font.pixelSize: 24
            font.bold: true
        }

        Card {
            Layout.fillWidth: true
            title: "AI Provider"
            subtitle: "按账号隔离保存。API Key 只写不读——这里只能看到「是否已配置」和掩码。"

            // ------------------------------------------------------------ state --
            //
            // The one place that says what state this configuration is in, and the evidence
            // for it. It used to be said twice, in two vocabularies, from two layers: the
            // fields below read the account's file and a 离线模式 row in the next card read
            // the process environment, so the page showed a key's mask and "未配置 Key" at
            // the same time.
            Rectangle {
                Layout.fillWidth: true
                implicitHeight: stateRow.implicitHeight + Theme.space3 * 2
                radius: Theme.radiusSm
                color: Theme.surface

                RowLayout {
                    id: stateRow
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    anchors.leftMargin: Theme.space3
                    anchors.rightMargin: Theme.space3
                    spacing: Theme.space3

                    Rectangle {
                        width: 8; height: 8; radius: 4
                        color: page.stateTone
                        Layout.alignment: Qt.AlignTop
                        Layout.topMargin: 5
                        Accessible.ignored: true
                    }

                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 2

                        RowLayout {
                            spacing: Theme.space2
                            Text {
                                objectName: "ai-state-word"
                                text: page.stateWord
                                color: page.stateTone
                                font.pixelSize: Theme.fontBody
                                font.bold: true
                            }
                            Text {
                                visible: page.status !== null && page.status.api_key_set
                                text: page.status ? page.status.api_key_hint : ""
                                color: Theme.textFaint
                                font.pixelSize: Theme.fontTiny
                            }
                        }
                        Text {
                            Layout.fillWidth: true
                            wrapMode: Text.WordWrap
                            text: page.stateHint
                            color: Theme.textMuted
                            font.pixelSize: Theme.fontSmall
                        }
                        // The evidence, when there is any. A green light with no date on it
                        // is a claim about a moment nobody can name.
                        Text {
                            Layout.fillWidth: true
                            wrapMode: Text.WordWrap
                            // `!!check.at`: an empty object is still an object, and
                            // `undefined !== ""` is true — so the line rendered
                            // "上次测试 undefined 失败" on a configuration that had
                            // never been tested.
                            visible: page.status !== null && !!page.status.check
                                     && !!page.status.check.at
                            color: page.status && page.status.check && page.status.check.ok
                                   ? Theme.textFaint : Theme.danger
                            font.pixelSize: Theme.fontTiny
                            text: {
                                if (!page.status || !page.status.check) return ""
                                var c = page.status.check
                                if (c.ok) {
                                    return "上次测试 " + c.at + "，用时 " + c.latency_ms + " 毫秒"
                                }
                                return "上次测试 " + c.at + " 失败：" +
                                       Errors.human(c.error_message, c.error_code).title
                            }
                        }
                    }

                    Button {
                        objectName: "ai-test-connection"
                        // Nothing to test when there is nothing configured, and calling
                        // anyway would turn "you have not filled this in" into a network
                        // error from a half-built request.
                        enabled: !page.testing && page.status !== null
                                 && page.status.state !== "not_configured"
                                 && page.status.state !== "offline"
                        text: page.testing ? "测试中…" : "测试连接"
                        variant: page.status && page.status.state === "connected"
                                 ? "ghost" : "secondary"
                        onClicked: page.testConnection()
                        Accessible.name: "向配置的端点发一次真实请求"
                    }
                }
            }

            // ------------------------------------------------------------ fields --
            // One component decides the label column, the gap and the vertical alignment,
            // so every row lines up with every other row — in this form and in the next.
            FormRow {
                label: "Provider"
                TextField {
                    Layout.fillWidth: true
                    text: page.provider
                    onTextChanged: page.provider = text
                    placeholderText: "deepseek / openai_compatible / ollama / offline"
                }
            }
            FormRow {
                label: "模型"
                TextField {
                    Layout.fillWidth: true
                    text: page.model
                    onTextChanged: page.model = text
                }
            }
            FormRow {
                label: "Base URL"
                TextField {
                    Layout.fillWidth: true
                    text: page.baseUrl
                    onTextChanged: page.baseUrl = text
                    placeholderText: "远程必须 https；本机 http 允许"
                }
            }
            FormRow {
                label: "API Key"
                hint: "只写不读。留空表示不修改已保存的 Key。"
                TextField {
                    Layout.fillWidth: true
                    text: page.apiKey
                    onTextChanged: page.apiKey = text
                    echoMode: TextInput.Password
                    placeholderText: page.effective && page.effective.api_key_set
                                   ? ("已配置 " + page.effective.api_key_hint + "（留空表示不修改）")
                                   : "尚未配置"
                }
            }

            // ------------------------------------------------------------ actions --
            // Buttons, then the result of pressing one. The message is on its own line
            // rather than beside them: it is longer than the buttons, and sharing the row
            // made the buttons move when it appeared.
            Rectangle {
                Layout.fillWidth: true
                implicitHeight: 1
                color: Theme.divider
                Accessible.ignored: true
            }
            RowLayout {
                Layout.fillWidth: true
                spacing: Theme.space2
                Button {
                    objectName: "ai-save"
                    text: "保存"
                    variant: "primary"
                    onClicked: page.save()
                }
                Button { text: "恢复默认"; variant: "ghost"; onClicked: page.clear() }
                Button {
                    objectName: "ai-reload"
                    // Text, not a box: the one primary action here is 保存, and a reload is
                    // a way of looking again rather than a way of changing anything.
                    text: "重新读取"
                    variant: "ghost"
                    onClicked: page.reload()
                    Accessible.name: "重新读取磁盘上的 AI 配置与运行信息"
                }
            }
            Text {
                Layout.fillWidth: true
                visible: page.statusText !== ""
                text: page.statusText
                color: Theme.textMuted
                font.pixelSize: Theme.fontSmall
                wrapMode: Text.WordWrap
            }
        }

        Card {
            Layout.fillWidth: true
            title: "运行信息"
            subtitle: "生效值来自本账号已保存的配置，不是 .env。"

            FormRow {
                label: "版本"
                Text {
                    text: page.runtime ? page.runtime.version : "…"
                    color: Theme.text
                    font.pixelSize: Theme.fontSmall
                }
            }
            FormRow {
                label: "生效 Provider"
                Text {
                    text: page.runtime ? (page.runtime.ai_provider || "…") : "…"
                    color: Theme.text
                    font.pixelSize: Theme.fontSmall
                }
            }
            FormRow {
                label: "生效模型"
                Text {
                    text: page.runtime ? (page.runtime.ai_model || "…") : "…"
                    color: Theme.text
                    font.pixelSize: Theme.fontSmall
                }
            }
            FormRow {
                label: "运行环境"
                hint: "构建时的环境标识，不影响 AI 调用。AI 用哪个 Provider 看上面一行。"
                Text {
                    text: page.runtime ? page.runtime.app_env : "…"
                    color: Theme.text
                    font.pixelSize: Theme.fontSmall
                }
            }
            FormRow {
                label: "数据目录"
                Text {
                    Layout.fillWidth: true
                    text: page.runtime ? page.runtime.runtime_root : "…"
                    color: Theme.text
                    font.pixelSize: Theme.fontTiny
                    elide: Text.ElideMiddle
                }
            }
        }

        Card {
            Layout.fillWidth: true
            title: "Embedding"
            subtitle: page.embedding === null
                      ? "读取中…"
                      : (page.embedding.semantic_search
                         ? "语义检索可用"
                         : "语义检索不可用（不影响关键词检索）")

            Text {
                Layout.fillWidth: true
                visible: page.embedding !== null
                wrapMode: Text.WrapAnywhere
                color: page.embedding && page.embedding.semantic_search ? Theme.success : Theme.warn
                font.pixelSize: 13
                text: page.embedding ? page.embedding.note : ""
            }

            Text {
                Layout.fillWidth: true
                visible: page.embedding !== null
                wrapMode: Text.WrapAnywhere
                color: Theme.textMuted
                font.pixelSize: 12
                font.family: "Consolas"
                text: page.embedding
                      ? ("provider " + page.embedding.provider
                         + "　model " + page.embedding.model
                         + (page.embedding.base_url ? ("　" + page.embedding.base_url) : "")
                         + "　配置值 " + page.embedding.configured_provider
                         + "/" + page.embedding.configured_model)
                      : ""
            }

            Text {
                Layout.fillWidth: true
                visible: page.embedding !== null && page.embedding.reason !== ""
                wrapMode: Text.WrapWord
                color: Theme.textFaint
                font.pixelSize: 11
                text: page.embedding ? page.embedding.reason : ""
            }

            Text {
                Layout.fillWidth: true
                wrapMode: Text.WordWrap
                color: Theme.textFaint
                font.pixelSize: 11
                text: "向量维度变化或换用不同 embedding 模型时，写入会被拒绝并说明原因——"
                      + "不同模型的向量不会混进同一个集合。"
            }
        }

        Card {
            Layout.fillWidth: true
            title: "文件"
            subtitle: "写入前仍然过权限层，不是绕过它"

            RowLayout {
                Layout.fillWidth: true
                spacing: 10
                Button { text: "导出配置摘要"; onClicked: page.exportSummary() }
                Button { text: "另存为…"; variant: "ghost"; onClicked: exportDialog.open() }
                Text {
                    Layout.fillWidth: true
                    text: "保存路径由系统对话框选择，写入由 PermissionManager 判定"
                    color: Theme.textFaint
                    font.pixelSize: 11
                    elide: Text.ElideRight
                }
            }
        }

        Item { Layout.preferredHeight: 24 }
    }
}
