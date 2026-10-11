import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import QtQuick.Dialogs
import "../components"
import "../theme.js" as Theme

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
    property string provider: ""
    property string model: ""
    property string baseUrl: ""
    property string apiKey: ""
    property string statusText: ""
    property var embedding: null

    function loadConfig() {
        var id = "cfg-" + Date.now()
        shell.request("读取 AI 配置", id, function (payload) {
            page.effective = payload.effective
            page.provider = payload.effective.provider
            page.model = payload.effective.model
            page.baseUrl = payload.effective.base_url
            page.apiKey = ""
        })
        bridge.loadAiConfig(id)
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
            page.apiKey = ""
            page.statusText = "已保存。下一次 AI 调用即生效，无需重启或重新编译。"
            shell.toast("info", "配置已保存")
        })
        bridge.saveAiConfig(id, JSON.stringify(patch))
    }

    function clear() {
        var id = "clear-" + Date.now()
        shell.request("清除 AI 配置", id, function (payload) {
            page.effective = payload.effective
            page.statusText = "已清除本账号的覆盖，回退到安装级配置与 .env。"
            shell.toast("info", "已恢复默认")
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

            GridLayout {
                Layout.fillWidth: true
                columns: 2
                columnSpacing: 12
                rowSpacing: 10

                Text { text: "Provider"; color: Theme.textMuted; font.pixelSize: 12 }
                TextField {
                    Layout.fillWidth: true
                    text: page.provider
                    onTextChanged: page.provider = text
                    placeholderText: "deepseek / openai_compatible / ollama / offline"
                }

                Text { text: "模型"; color: Theme.textMuted; font.pixelSize: 12 }
                TextField {
                    Layout.fillWidth: true
                    text: page.model
                    onTextChanged: page.model = text
                }

                Text { text: "Base URL"; color: Theme.textMuted; font.pixelSize: 12 }
                TextField {
                    Layout.fillWidth: true
                    text: page.baseUrl
                    onTextChanged: page.baseUrl = text
                    placeholderText: "远程必须 https；本机 http 允许"
                }

                Text { text: "API Key"; color: Theme.textMuted; font.pixelSize: 12 }
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

            RowLayout {
                Layout.fillWidth: true
                spacing: 10
                // Saving is what the page is for; clearing is the way back. One of them
                // gets the accent and the other stays a word.
                Button { text: "保存"; variant: "primary"; onClicked: page.save() }
                Button { text: "恢复默认"; variant: "ghost"; onClicked: page.clear() }
                Text {
                    Layout.fillWidth: true
                    text: page.statusText
                    color: Theme.accent
                    font.pixelSize: 12
                    elide: Text.ElideRight
                }
            }
        }

        Card {
            Layout.fillWidth: true
            title: "运行信息"
            subtitle: "版本来自仓库根目录的 VERSION —— 全产品唯一来源"

            GridLayout {
                Layout.fillWidth: true
                columns: 2
                columnSpacing: 12
                rowSpacing: 6

                Text { text: "版本"; color: Theme.textMuted; font.pixelSize: 12 }
                Text { text: page.runtime ? page.runtime.version : "…"; color: Theme.text; font.pixelSize: 12 }

                Text { text: "APP_ENV"; color: Theme.textMuted; font.pixelSize: 12 }
                Text { text: page.runtime ? page.runtime.app_env : "…"; color: Theme.text; font.pixelSize: 12 }

                Text { text: "离线模式"; color: Theme.textMuted; font.pixelSize: 12 }
                Text {
                    text: page.runtime ? (page.runtime.offline ? "是（未配置 Key）" : "否")
                                       : "…"
                    color: page.runtime && page.runtime.offline ? Theme.warn : Theme.success
                    font.pixelSize: 12
                }

                Text { text: "数据目录"; color: Theme.textMuted; font.pixelSize: 12 }
                Text {
                    Layout.fillWidth: true
                    text: page.runtime ? page.runtime.runtime_root : "…"
                    color: Theme.text; font.pixelSize: 11
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
