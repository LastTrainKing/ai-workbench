import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "../components"
import "../theme.js" as Theme

// A conversation with the configured provider, streamed.
//
// The plumbing is the same as the knowledge-base ask page; what is different is that there
// is no retrieval and no system prompt about citations, so this is the plain multi-turn
// chat. `history` is sent back with every turn, which is what makes the second answer aware
// of the first — a chat that only ever sends the newest message is not a conversation, and
// the failure is invisible until someone asks a follow-up.
//
// Three states the interface must not fake:
//
//  * **waiting** — the request is out and nothing has come back. Shown as 「等待模型…」 with
//    the elapsed time, because on a reasoning model this can be tens of seconds and a
//    frozen-looking window reads as a crash.
//  * **reasoning** — the provider is emitting `reasoning_content` and no answer text yet.
//    Shown as 「模型正在推理（已收到 N 字）」. This is the case that produced an empty
//    answer during the acceptance round: the token budget went entirely into reasoning.
//  * **unsupported** — the provider cannot stream. Said plainly instead of animating one
//    chunk as though it were many.
Flickable {
    id: page
    property var shell

    contentWidth: width
    contentHeight: column.implicitHeight
    clip: true

    property string draft: ""
    property bool busy: false
    property string streamedText: ""
    property string reasoningText: ""
    property string errorText: ""
    property string providerNote: ""
    property int emptyBecauseReasoning: 0
    property double startedAt: 0
    property int elapsedSeconds: 0

    //: The transcript, oldest first. `{role, content}` — the same shape `streamChat` accepts.
    property var history: []
    property bool _loaded: true

    signal back()

    function send() {
        var text = draft.trim()
        if (text === "" || busy) return

        // Snapshot before appending: this is what goes back to the model as context.
        var context = history.slice()
        var next = context.concat([{ role: "user", content: text }])
        history = next

        draft = ""
        busy = true
        streamedText = ""
        reasoningText = ""
        errorText = ""
        providerNote = ""
        emptyBecauseReasoning = 0
        startedAt = Date.now()
        elapsedSeconds = 0
        elapsedTimer.restart()

        var id = "chat-" + Date.now()
        shell.request("对话", id, function (payload) {
            page.busy = false
            elapsedTimer.stop()
            if (payload.streamed === false) {
                page.providerNote = payload.reason ?? "该 Provider 不支持流式输出"
                // The user's message stays in the transcript: it was sent, and removing it
                // would lose the fact that they said it.
                return
            }
            page.emptyBecauseReasoning = payload.empty_because_reasoning ? 1 : 0
            page.history = page.history.concat([
                { role: "assistant", content: payload.answer || "" }
            ])
        })
        bridge.streamChat(id, JSON.stringify(context), text)
    }

    function clearAll() {
        history = []
        streamedText = ""
        reasoningText = ""
        errorText = ""
        providerNote = ""
    }

    Timer {
        id: elapsedTimer
        interval: 500
        repeat: true
        onTriggered: page.elapsedSeconds = Math.floor((Date.now() - page.startedAt) / 1000)
    }

    Connections {
        target: bridge
        function onProgress(id, delta) {
            if (!page.busy || delta === "") return
            // A reasoning delta arrives as JSON so the two streams stay distinguishable.
            // Mixing them would put the model's scratch pad into the answer.
            if (delta.charAt(0) === "{") {
                try {
                    var parsed = JSON.parse(delta)
                    if (parsed && parsed.reasoning) {
                        page.reasoningText += parsed.reasoning
                        return
                    }
                } catch (e) { /* not JSON: it is answer text that starts with a brace */ }
            }
            page.streamedText += delta
        }
    }

    ColumnLayout {
        id: column
        width: page.width
        spacing: 16

        RowLayout {
            Layout.fillWidth: true
            spacing: 12

            Text {
                text: "对话"
                color: Theme.text
                font.pixelSize: 24
                font.bold: true
            }
            Item { Layout.fillWidth: true }
            Button {
                objectName: "chat-clear"
                text: "清空"
                enabled: page.history.length > 0 && !page.busy
                onClicked: page.clearAll()
                Accessible.name: "清空对话"
            }
        }

        Text {
            Layout.fillWidth: true
            color: Theme.textFaint
            font.pixelSize: 12
            text: "每次发送都会把完整对话历史一起交给模型，所以追问能接上上文。"
        }

        Card {
            Layout.fillWidth: true
            title: "对话记录"
            subtitle: page.history.length === 0 && !page.busy
                      ? "还没有消息"
                      : (page.history.length + " 条消息")

            Text {
                Layout.fillWidth: true
                visible: page.history.length === 0 && !page.busy
                color: Theme.textMuted
                font.pixelSize: 13
                text: "在下面输入第一条消息。"
            }

            Repeater {
                model: page.history

                delegate: Rectangle {
                    required property var modelData
                    required property int index

                    Layout.fillWidth: true
                    implicitHeight: bubbleText.implicitHeight + 20
                    radius: 10
                    color: modelData.role === "user" ? Theme.bgCardHover : Theme.bgElevated
                    border.width: 1
                    border.color: Theme.border

                    Accessible.role: Accessible.StaticText
                    Accessible.name: (modelData.role === "user" ? "我说：" : "模型回答：")
                                     + modelData.content

                    Text {
                        id: bubbleText
                        anchors.fill: parent
                        anchors.margins: 10
                        wrapMode: Text.WrapAnywhere
                        color: Theme.text
                        font.pixelSize: 13
                        text: (modelData.role === "user" ? "我：" : "模型：") + modelData.content
                    }
                }
            }

            // The live stream, in its own bubble, so it is obvious which text is still
            // arriving.
            Rectangle {
                Layout.fillWidth: true
                objectName: "chat-stream"
                visible: page.busy || page.streamedText !== ""
                implicitHeight: streamColumn.implicitHeight + 20
                radius: 10
                color: Theme.bgElevated
                border.width: 1
                border.color: Theme.accent

                ColumnLayout {
                    id: streamColumn
                    anchors.fill: parent
                    anchors.margins: 10
                    spacing: 6

                    Text {
                        Layout.fillWidth: true
                        visible: page.reasoningText !== ""
                        wrapMode: Text.WordWrap
                        color: Theme.warn
                        font.pixelSize: 11
                        text: "模型正在推理（已收到 " + page.reasoningText.length + " 字，"
                              + "这些是思考过程，不是回答）"
                    }

                    Text {
                        Layout.fillWidth: true
                        visible: page.busy && page.streamedText === ""
                        color: Theme.textMuted
                        font.pixelSize: 12
                        text: page.reasoningText !== ""
                              ? "等待回答内容…"
                              : ("等待模型… " + page.elapsedSeconds + "s")
                    }

                    Text {
                        Layout.fillWidth: true
                        wrapMode: Text.WrapAnywhere
                        color: Theme.text
                        font.pixelSize: 13
                        text: "模型：" + page.streamedText
                    }
                }
            }

            Text {
                Layout.fillWidth: true
                visible: page.providerNote !== ""
                wrapMode: Text.WordWrap
                color: Theme.warn
                font.pixelSize: 12
                text: page.providerNote
            }

            Text {
                Layout.fillWidth: true
                visible: page.emptyBecauseReasoning === 1
                wrapMode: Text.WordWrap
                color: Theme.warn
                font.pixelSize: 12
                text: "模型把全部输出预算用在了推理上，没有产生回答内容。"
                      + "这不是网络问题——把问题问得更具体，或换一个非推理模型。"
            }

            Text {
                Layout.fillWidth: true
                visible: page.errorText !== ""
                wrapMode: Text.WordWrap
                color: Theme.danger
                font.pixelSize: 12
                text: page.errorText
            }
        }

        Card {
            Layout.fillWidth: true
            title: "发消息"

            TextArea {
                id: draftField
                objectName: "chat-input"
                Layout.fillWidth: true
                Layout.preferredHeight: 96
                text: page.draft
                onTextChanged: page.draft = text
                color: Theme.text
                font.pixelSize: 14
                placeholderText: "输入消息，Ctrl+Enter 发送"
                wrapMode: TextEdit.Wrap
                Accessible.role: Accessible.EditableText
                Accessible.name: "消息内容"
                background: Rectangle {
                    radius: 8
                    color: Theme.bgElevated
                    border.width: draftField.activeFocus ? 2 : 1
                    border.color: draftField.activeFocus ? Theme.borderFocus : Theme.border
                }

                Keys.onPressed: function (event) {
                    if ((event.key === Qt.Key_Return || event.key === Qt.Key_Enter)
                            && (event.modifiers & Qt.ControlModifier)) {
                        page.send()
                        event.accepted = true
                    }
                }
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 10

                Button {
                    objectName: "chat-send"
                    text: page.busy ? "等待中…" : "发送"
                    enabled: !page.busy && page.draft.trim() !== ""
                    onClicked: page.send()
                    Accessible.name: "发送消息"
                }

                Text {
                    Layout.fillWidth: true
                    color: Theme.textFaint
                    font.pixelSize: 11
                    text: page.busy ? ("已发出 " + page.elapsedSeconds + " 秒") : "Ctrl+Enter 发送"
                }
            }
        }

        Item { Layout.preferredHeight: 24 }
    }
}
