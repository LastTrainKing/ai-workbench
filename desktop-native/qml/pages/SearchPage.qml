import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "../components"
import "../theme.js" as Theme

// Search over the knowledge base — and a clear statement of how each hit was found.
//
// The three modes are genuinely different capabilities, not a slider on one:
//
//   keyword   matches words. Works with no model configured at all.
//   semantic  matches meaning. Needs an embedding provider **and** a vector store; with
//             neither it raises, and this page shows the error rather than quietly
//             returning keyword results under a semantic label.
//   hybrid    runs both and fuses them. When the vector side is unavailable it degrades to
//             keyword-only and reports `dense_hits = 0`.
//
// So each result shows its keyword score and its vector score separately, and the header
// says which retrievals actually ran. A page that showed one blended number would make
// "this matched by spelling" indistinguishable from "this matched by meaning", which is the
// one thing a search screen must not do.
Flickable {
    id: page
    property var shell

    contentWidth: width
    contentHeight: column.implicitHeight
    clip: true

    property string query: ""
    property string mode: "hybrid"
    property var result: null
    property string errorText: ""
    property bool busy: false
    property bool _loaded: true

    function run() {
        var text = query.trim()
        if (text === "") {
            shell.toast("warn", "先写一个搜索词")
            return
        }
        busy = true
        result = null
        errorText = ""
        var id = "search-" + Date.now()
        shell.request("搜索", id, function (payload) {
            page.result = payload
            page.busy = false
        })
        bridge.search(id, text, mode)
    }

    function modeWord(m) {
        switch (m) {
        case "keyword":  return "关键词"
        case "semantic": return "语义"
        case "hybrid":   return "混合"
        default:         return m || ""
        }
    }

    //: What actually ran, as opposed to what was asked for.
    function retrievalSummary(payload) {
        if (!payload) return ""
        var dense = payload.dense_hits || 0
        var sparse = payload.sparse_hits || 0
        var parts = []
        if (sparse > 0) parts.push("关键词命中 " + sparse)
        if (dense > 0) parts.push("向量命中 " + dense)
        if (dense === 0 && (payload.mode === "hybrid" || payload.mode === "semantic")) {
            parts.push("向量检索本次没有返回任何结果")
        }
        if (parts.length === 0) parts.push("没有命中")
        return parts.join(" · ")
    }

    ColumnLayout {
        id: column
        width: page.width
        spacing: 16

        Text {
            text: "搜索"
            color: Theme.text
            font.pixelSize: 24
            font.bold: true
        }

        Card {
            Layout.fillWidth: true
            title: "在知识库里找"
            subtitle: "关键词靠词面匹配；语义靠向量；混合两者都跑并融合"

            RowLayout {
                Layout.fillWidth: true
                spacing: 10

                TextField {
                    id: queryField
                    objectName: "search-input"
                    Layout.fillWidth: true
                    text: page.query
                    onTextChanged: page.query = text
                    color: Theme.text
                    font.pixelSize: 14
                    placeholderText: "搜索词（中文、英文、符号都可以）"
                    Accessible.role: Accessible.EditableText
                    Accessible.name: "搜索词"
                    background: Rectangle {
                        radius: 8
                        color: Theme.surface
                        border.width: queryField.activeFocus ? 2 : 1
                        border.color: queryField.activeFocus ? Theme.borderFocus : Theme.border
                    }
                    Keys.onReturnPressed: page.run()
                    Keys.onEnterPressed: page.run()
                }

                Button {
                    objectName: "search-run"
                    text: page.busy ? "搜索中…" : "搜索"
                    enabled: !page.busy
                    onClicked: page.run()
                    Accessible.name: "开始搜索"
                }
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 8
                Repeater {
                    model: ["keyword", "semantic", "hybrid"]
                    delegate: Button {
                        required property string modelData
                        objectName: "search-mode-" + modelData
                        text: page.modeWord(modelData)
                        checkable: true
                        checked: page.mode === modelData
                        onClicked: {
                            page.mode = modelData
                            if (page.query.trim() !== "") page.run()
                        }
                        Accessible.name: "检索方式：" + page.modeWord(modelData)
                    }
                }
                Item { Layout.fillWidth: true }
                Text {
                    text: "语义检索需要配置 Embedding Provider；没有配置时它会明确报错，不会退回关键词"
                    color: Theme.textFaint
                    font.pixelSize: 11
                }
            }
        }

        Card {
            Layout.fillWidth: true
            title: "结果"
            subtitle: page.busy
                      ? "搜索中…"
                      : (page.result
                         ? (page.result.results.length + " 条 · " + page.modeWord(page.result.mode)
                            + " · " + (page.result.took_ms || 0) + " ms"
                            + (page.result.cached ? " · 命中缓存" : ""))
                         : "")

            Text {
                Layout.fillWidth: true
                visible: page.errorText !== ""
                wrapMode: Text.WordWrap
                color: Theme.danger
                font.pixelSize: 12
                text: page.errorText
            }

            Text {
                Layout.fillWidth: true
                visible: page.result !== null
                wrapMode: Text.WordWrap
                color: Theme.textMuted
                font.pixelSize: 12
                text: page.result ? page.retrievalSummary(page.result) : ""
            }

            Text {
                Layout.fillWidth: true
                visible: page.result !== null && page.result.message
                wrapMode: Text.WordWrap
                color: Theme.warn
                font.pixelSize: 12
                text: page.result ? (page.result.message || "") : ""
            }

            Text {
                Layout.fillWidth: true
                visible: page.result !== null
                         && (page.result.results || []).length === 0 && !page.result.message
                color: Theme.textMuted
                font.pixelSize: 13
                text: "没有找到匹配的内容。换个说法，或把检索方式换成关键词再试。"
            }

            Repeater {
                model: page.result ? (page.result.results || []) : []

                delegate: Rectangle {
                    required property var modelData
                    required property int index

                    Layout.fillWidth: true
                    implicitHeight: hitColumn.implicitHeight + 20
                    radius: 8
                    color: Theme.surface
                    border.width: 1
                    border.color: Theme.border

                    Accessible.role: Accessible.StaticText
                    Accessible.name: "结果 " + (index + 1) + "：" + modelData.document_title
                                     + "，关键词分 " + modelData.keyword_score
                                     + "，向量分 " + modelData.vector_score

                    ColumnLayout {
                        id: hitColumn
                        anchors.fill: parent
                        anchors.margins: 10
                        spacing: 4

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 10
                            Text {
                                Layout.fillWidth: true
                                text: (index + 1) + ". " + modelData.document_title
                                      + (modelData.heading ? (" › " + modelData.heading) : "")
                                color: Theme.text
                                font.pixelSize: 13
                                elide: Text.ElideRight
                            }
                            Text {
                                text: "综合 " + modelData.score
                                color: Theme.accent
                                font.pixelSize: 11
                            }
                        }

                        Text {
                            Layout.fillWidth: true
                            wrapMode: Text.WrapAnywhere
                            color: Theme.textMuted
                            font.pixelSize: 12
                            maximumLineCount: 4
                            elide: Text.ElideRight
                            text: modelData.content
                        }

                        // Both scores, always. This is the difference between "matched by
                        // meaning" and "matched by spelling", and one blended number hides it.
                        Text {
                            Layout.fillWidth: true
                            color: Theme.textFaint
                            font.pixelSize: 11
                            font.family: "Consolas"
                            text: "关键词 " + modelData.keyword_score
                                  + "   向量 " + modelData.vector_score
                                  + "   第 " + (modelData.chunk_index + 1) + " 段"
                        }
                    }
                }
            }
        }

        Item { Layout.preferredHeight: 24 }
    }
}
