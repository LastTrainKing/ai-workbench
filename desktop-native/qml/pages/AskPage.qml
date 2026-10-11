import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "../components"
import "../theme.js" as Theme

// Ask a question against one knowledge base, and see what the answer is actually standing on.
//
// Two things this page refuses to do:
//
//  * **It does not show a green tick for "has citations".** It shows the grounding report —
//    how many claim-bearing sentences there were, how many carry a valid citation, and the
//    reason when they do not. A citation number that exists is not evidence that it points
//    at anything, so the sources are listed with their document and score, and an invalid
//    id is shown as invalid.
//  * **It does not animate a fake stream.** The streaming button asks the provider whether
//    it can stream first, and says so plainly when it cannot.
Flickable {
    id: page
    property var shell
    property string kbId: ""
    property string kbName: ""

    contentWidth: width
    contentHeight: column.implicitHeight
    clip: true

    property string question: ""
    property bool busy: false
    property bool streaming: false
    property string streamedText: ""
    property var result: null
    property string errorText: ""

    signal back()

    function ask(useStream) {
        var text = question.trim()
        if (text === "") {
            shell.toast("warn", "先写一个问题")
            return
        }
        busy = true
        streaming = useStream
        result = null
        streamedText = ""
        errorText = ""

        var id = "ask-" + Date.now()
        shell.request(useStream ? "流式提问" : "提问", id, function (payload) {
            page.busy = false
            if (useStream && payload.streamed === false) {
                page.errorText = payload.reason ?? "该 Provider 不支持流式输出"
                return
            }
            page.result = payload
        })
        if (useStream) {
            bridge.streamAnswer(id, kbId, text)
        } else {
            bridge.askKnowledge(id, kbId, text)
        }
    }

    Connections {
        target: bridge
        function onProgress(id, delta) {
            if (page.busy && page.streaming && delta !== "") {
                page.streamedText = page.streamedText + delta
            }
        }
    }

    ColumnLayout {
        id: column
        width: page.width
        spacing: 16

        RowLayout {
            Layout.fillWidth: true
            Button { text: "← 返回"; variant: "ghost"; onClicked: page.back() }
            Text {
                text: "提问 · " + page.kbName
                color: Theme.text
                font.pixelSize: 22
                font.bold: true
                Layout.fillWidth: true
                elide: Text.ElideRight
            }
        }

        Card {
            Layout.fillWidth: true
            title: "问题"

            RowLayout {
                Layout.fillWidth: true
                spacing: 10
                TextField {
                    id: questionField
                    Layout.fillWidth: true
                    placeholderText: "针对这个知识库问一个问题"
                    text: page.question
                    onTextChanged: page.question = text
                    color: Theme.text
                    Keys.onReturnPressed: page.ask(false)
                    background: Rectangle {
                        radius: 8
                        color: Theme.surface
                        border.width: questionField.activeFocus ? 2 : 1
                        border.color: questionField.activeFocus ? Theme.borderFocus : Theme.border
                    }
                }
                Button {
                    text: page.busy && !page.streaming ? "思考中…" : "提问"
                    variant: "primary"
                    enabled: !page.busy
                    onClicked: page.ask(false)
                }
                Button {
                    text: page.busy && page.streaming ? "输出中…" : "流式提问"
                    enabled: !page.busy
                    onClicked: page.ask(true)
                }
            }
        }

        Card {
            Layout.fillWidth: true
            visible: page.streaming && page.streamedText !== ""
            title: "流式输出（逐块到达）"
            Text {
                Layout.fillWidth: true
                wrapMode: Text.WordWrap
                color: Theme.text
                font.pixelSize: 14
                text: page.streamedText
            }
        }

        Text {
            visible: page.errorText !== ""
            text: page.errorText
            color: Theme.warn
            font.pixelSize: 13
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }

        Card {
            Layout.fillWidth: true
            visible: page.result !== null && page.result.streamed !== true
            title: "回答"

            Text {
                Layout.fillWidth: true
                wrapMode: Text.WordWrap
                color: Theme.text
                font.pixelSize: 14
                text: page.result ? (page.result.answer ?? "") : ""
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 10
                Rectangle {
                    width: 10; height: 10; radius: 5
                    color: page.result && page.result.grounded ? Theme.success : Theme.warn
                }
                Text {
                    Layout.fillWidth: true
                    color: Theme.textMuted
                    font.pixelSize: 12
                    text: page.result
                          ? (page.result.grounded
                             ? "每条结论都有可解析的引用"
                             : ("未通过接地检查：" + (page.result.citation_report
                                                      ? page.result.citation_report.reason : "")))
                          : ""
                }
            }

            Text {
                Layout.fillWidth: true
                color: Theme.textFaint
                font.pixelSize: 11
                font.family: "Consolas"
                wrapMode: Text.WrapAnywhere
                text: page.result
                      ? ("run " + page.result.run_id + "  ·  "
                         + page.result.provider + "/" + page.result.model + "  ·  "
                         + "引用 " + JSON.stringify(page.result.used_citation_ids))
                      : ""
            }
        }

        Card {
            Layout.fillWidth: true
            visible: page.result !== null && (page.result.sources ?? []).length > 0
            title: "来源（引用编号指向的资料）"

            Repeater {
                model: page.result ? (page.result.sources ?? []) : []

                delegate: RowLayout {
                    required property var modelData
                    Layout.fillWidth: true
                    spacing: 10

                    Text {
                        text: "[" + modelData.index + "]"
                        color: Theme.accent
                        font.pixelSize: 12
                        font.bold: true
                    }
                    Text {
                        Layout.fillWidth: true
                        color: Theme.text
                        font.pixelSize: 12
                        elide: Text.ElideRight
                        text: modelData.document_title
                              + (modelData.score !== undefined
                                 ? "   ·   score " + Number(modelData.score).toFixed(3) : "")
                    }
                }
            }
        }

        Card {
            Layout.fillWidth: true
            visible: page.result !== null && page.result.citation_report !== undefined
            title: "接地检查（程序实际验证了什么）"
            Text {
                Layout.fillWidth: true
                wrapMode: Text.WrapAnywhere
                color: Theme.textFaint
                font.family: "Consolas"
                font.pixelSize: 11
                text: page.result ? JSON.stringify(page.result.citation_report) : ""
            }
            Text {
                Layout.fillWidth: true
                wrapMode: Text.WordWrap
                color: Theme.textMuted
                font.pixelSize: 11
                text: "这是结构性检查：每条结论是否带了指向真实来源的编号。"
                      + "它不是「来源能否推出这句话」的事实级验证。"
            }
        }

        Item { Layout.preferredHeight: 24 }
    }
}
