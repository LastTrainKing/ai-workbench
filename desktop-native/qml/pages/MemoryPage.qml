import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "../components"
import "../theme.js" as Theme

// What the system believes about you, and what it would tell a model right now.
//
// The commitment the backend was built around — "the user can always see what the system
// believes about them, and change it" — had twenty endpoints and no screen. Three panels:
//
//   1. the memories that are in force, with a real delete
//   2. the candidates waiting for a decision, because a memory the user never approved is
//      the thing that makes an assistant untrustworthy rather than useful
//   3. the context preview, which is the transparency promise made checkable — the exact
//      blocks that would be assembled for the next request, and how much of the budget
//      each takes
Flickable {
    id: page
    property var shell

    contentWidth: width
    contentHeight: column.implicitHeight
    clip: true

    property var memories: []
    property var candidates: []
    property var preview: null
    property var stats: null

    property string memoriesState: "loading"
    property string memoriesError: ""
    property string memoriesErrorDetail: ""

    property string candidatesState: "loading"
    property string candidatesError: ""

    property string previewState: "loading"
    property string previewError: ""

    function loadMemories() {
        var id = "memories-" + Date.now()
        memoriesState = "loading"
        shell.request("记忆", id, function (payload) {
            page.memories = payload.items ?? []
            page.memoriesState = "ready"
        }, function (title, code, human) {
            page.memoriesState = "error"
            page.memoriesError = title
            page.memoriesErrorDetail = (human && human.detail) ? human.detail : code
        })
        bridge.loadMemories(id)
    }

    function loadCandidates() {
        var id = "candidates-" + Date.now()
        candidatesState = "loading"
        shell.request("记忆候选", id, function (payload) {
            page.candidates = payload.items ?? []
            page.candidatesState = "ready"
        }, function (title) {
            page.candidatesState = "error"
            page.candidatesError = title
        })
        bridge.loadMemoryCandidates(id)
    }

    function loadPreview() {
        var id = "preview-" + Date.now()
        previewState = "loading"
        shell.request("上下文预览", id, function (payload) {
            page.preview = payload
            page.previewState = "ready"
        }, function (title) {
            page.previewState = "error"
            page.previewError = title
        })
        bridge.loadContextPreview(id)
    }

    function loadStats() {
        var id = "memstats-" + Date.now()
        shell.request("记忆统计", id, function (payload) {
            page.stats = payload
        })
        bridge.loadMemoryStats(id)
    }

    function decide(candidate, approve) {
        var id = "decide-" + Date.now()
        shell.request("处理候选", id, function () {
            shell.toast("info", approve ? "已确认并记为长期记忆" : "已拒绝")
            page.loadCandidates()
            page.loadMemories()
            page.loadStats()
        })
        bridge.resolveMemoryCandidate(id, candidate.id, approve)
    }

    function forget(memory) {
        var id = "forget-" + Date.now()
        shell.request("删除记忆", id, function () {
            shell.toast("info", "记忆已删除")
            page.loadMemories()
            page.loadStats()
        })
        bridge.deleteMemory(id, memory.id)
    }

    property bool _loaded: false
    function _firstLoad() {
        if (_loaded || !shell) return
        _loaded = true
        loadMemories()
        loadCandidates()
        loadPreview()
        loadStats()
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
                text: "记忆"
                color: Theme.text
                font.pixelSize: 24
                font.bold: true
                Layout.fillWidth: true
            }
            Text {
                visible: page.stats !== null
                text: page.stats
                      ? (page.stats.active ?? 0) + " 条在效 · "
                        + (page.stats.candidates_pending ?? 0) + " 条待确认"
                      : ""
                color: Theme.textMuted
                font.pixelSize: 12
            }
            Button {
                text: "刷新"
                onClicked: {
                    page.loadMemories()
                    page.loadCandidates()
                    page.loadPreview()
                    page.loadStats()
                }
            }
        }

        // ------------------------------------------------- 待确认的候选 --
        Card {
            Layout.fillWidth: true
            title: "待确认"
            subtitle: "系统从对话和资料里提炼出来的记忆。你不确认，它就不会生效。"

            ColumnLayout {
                Layout.fillWidth: true
                spacing: 8

                StateBlock {
                    Layout.fillWidth: true
                    state: page.candidatesState === "ready" && page.candidates.length === 0
                           ? "empty" : page.candidatesState
                    busyText: "正在读取候选…"
                    emptyText: "没有待确认的记忆。"
                    errorText: page.candidatesError
                    onRetry: page.loadCandidates()
                }

                Repeater {
                    model: page.candidates

                    delegate: Rectangle {
                        required property var modelData
                        Layout.fillWidth: true
                        Layout.preferredHeight: candidateColumn.implicitHeight + 24
                        radius: 8
                        color: Theme.surface
                        border.width: 1
                        border.color: Theme.border

                        ColumnLayout {
                            id: candidateColumn
                            anchors.left: parent.left
                            anchors.right: parent.right
                            anchors.top: parent.top
                            anchors.margins: 12
                            spacing: 6

                            Text {
                                Layout.fillWidth: true
                                text: modelData.content
                                color: Theme.text
                                font.pixelSize: 13
                                wrapMode: Text.WordWrap
                            }
                            Text {
                                Layout.fillWidth: true
                                text: (modelData.proposed_kind ?? "fact") + " · "
                                      + (modelData.proposed_scope ?? "global") + " · 置信度 "
                                      + Number(modelData.confidence ?? 0).toFixed(2)
                                      + ((modelData.conflicts_with ?? "") !== ""
                                         ? " · ⚠ 与既有记忆冲突" : "")
                                color: (modelData.conflicts_with ?? "") !== ""
                                       ? Theme.warn : Theme.textFaint
                                font.pixelSize: 11
                            }
                            Text {
                                Layout.fillWidth: true
                                visible: (modelData.reason ?? "") !== ""
                                text: "为什么记它：" + modelData.reason
                                color: Theme.textFaint
                                font.pixelSize: 11
                                wrapMode: Text.WordWrap
                            }
                            RowLayout {
                                spacing: 8
                                Button {
                                    text: "记住"
                                    variant: "primary"
                                    onClicked: page.decide(modelData, true)
                                }
                                Button {
                                    text: "不要"
                                    onClicked: page.decide(modelData, false)
                                }
                                Item { Layout.fillWidth: true }
                            }
                        }
                    }
                }
            }
        }

        // ------------------------------------------------------ 在效记忆 --
        Card {
            Layout.fillWidth: true
            title: "在效记忆"
            subtitle: "删除是真的删除。归档用编辑接口，删除之后不会再被检索到。"

            ColumnLayout {
                Layout.fillWidth: true
                spacing: 8

                StateBlock {
                    Layout.fillWidth: true
                    state: page.memoriesState === "ready" && page.memories.length === 0
                           ? "empty" : page.memoriesState
                    busyText: "正在读取记忆…"
                    emptyText: "还没有长期记忆。"
                    errorText: page.memoriesError
                    errorDetail: page.memoriesErrorDetail
                    onRetry: page.loadMemories()
                }

                Repeater {
                    model: page.memories

                    delegate: Rectangle {
                        required property var modelData
                        Layout.fillWidth: true
                        Layout.preferredHeight: memoryRow.implicitHeight + 22
                        radius: 8
                        color: Theme.surface
                        border.width: 1
                        border.color: Theme.border

                        RowLayout {
                            id: memoryRow
                            anchors.left: parent.left
                            anchors.right: parent.right
                            anchors.verticalCenter: parent.verticalCenter
                            anchors.margins: 11
                            spacing: 10

                            NavIcon {
                                name: "memory"
                                size: 16
                                color: modelData.confirmed_by_user ? Theme.success
                                                                  : Theme.textMuted
                            }

                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 2
                                Text {
                                    Layout.fillWidth: true
                                    text: modelData.content
                                    color: Theme.text
                                    font.pixelSize: 13
                                    wrapMode: Text.WordWrap
                                }
                                Text {
                                    text: (modelData.kind ?? "fact") + " · "
                                          + (modelData.scope ?? "global") + " · 来源 "
                                          + (modelData.source ?? "?") + " · 用过 "
                                          + (modelData.use_count ?? 0) + " 次"
                                          + (modelData.confirmed_by_user ? " · 你确认过" : "")
                                    color: Theme.textFaint
                                    font.pixelSize: 11
                                }
                            }

                            Button {
                                text: "删除"
                                variant: "danger"
                                onClicked: page.forget(modelData)
                            }
                        }
                    }
                }
            }
        }

        // ---------------------------------------------------- 上下文预览 --
        Card {
            Layout.fillWidth: true
            title: "模型此刻会被告知什么"
            subtitle: "下一次请求会带上这些内容。数字是各块占用的 token 预算。"

            ColumnLayout {
                Layout.fillWidth: true
                spacing: 8

                StateBlock {
                    Layout.fillWidth: true
                    state: page.previewState
                    busyText: "正在装配上下文…"
                    errorText: page.previewError
                    onRetry: page.loadPreview()
                }

                Rectangle {
                    Layout.fillWidth: true
                    visible: page.previewState === "ready"
                    Layout.preferredHeight: previewText.implicitHeight + 20
                    radius: 8
                    color: Theme.surface
                    border.width: 1
                    border.color: Theme.border

                    Text {
                        id: previewText
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.verticalCenter: parent.verticalCenter
                        anchors.margins: 10
                        text: page.preview
                              ? ((page.preview.rendered ?? page.preview.text ?? "")
                                 || JSON.stringify(page.preview, null, 1))
                              : ""
                        color: Theme.textMuted
                        font.pixelSize: 11
                        font.family: "Consolas, monospace"
                        wrapMode: Text.WordWrap
                    }
                }
            }
        }

        Item { Layout.preferredHeight: 24 }
    }
}
