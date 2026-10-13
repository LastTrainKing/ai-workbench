import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "../components"
import "../theme.js" as Theme

// The files the employees actually produced.
//
// **This page is why the release exists.** Nineteen delivery endpoints — build runs with
// execute/pause/resume/cancel, deliverables with verify/accept/reject/versions — and until
// now the bridge called none of them. An agent could produce a file, verify it, register it
// and version it, and the person who asked for it had no way to look at it.
//
// The detail panel shows the *evidence* rather than a checkmark. "Verified" on its own is a
// claim; `verification` is the JSONB the verifier wrote, and the difference is the whole
// reason the field is stored.
Flickable {
    id: page
    property var shell

    contentWidth: width
    contentHeight: column.implicitHeight
    clip: true

    property var items: []
    property string state: "loading"
    property string errorText: ""
    property string errorDetail: ""
    property bool busy: false

    property string selected: ""
    property var detail: null
    property string rejectReason: ""

    function load() {
        var id = "deliverables-" + Date.now()
        state = "loading"
        shell.request("交付物", id, function (payload) {
            page.items = payload.items ?? []
            page.state = "ready"
        }, function (title, code, human) {
            page.state = "error"
            page.errorText = title
            page.errorDetail = (human && human.detail) ? human.detail : code
        })
        bridge.loadDeliverables(id, "100")
    }

    function open(item) {
        selected = item.id
        detail = null
        var id = "deliverable-" + Date.now()
        shell.request("交付物详情", id, function (payload) {
            page.detail = payload
        })
        bridge.loadDeliverableDetail(id, item.id)
    }

    function verify(item) {
        busy = true
        var id = "verify-" + Date.now()
        shell.request("重新验证", id, function () {
            page.busy = false
            shell.toast("info", "已重新验证")
            page.open(item)
            page.load()
        }, function (title) {
            page.busy = false
            shell.toast("error", title)
        })
        bridge.verifyDeliverable(id, item.id)
    }

    function accept(item) {
        var id = "accept-" + Date.now()
        shell.request("接受交付物", id, function () {
            shell.toast("info", "已接受")
            page.load()
            if (page.selected === item.id) page.open(item)
        })
        bridge.acceptDeliverable(id, item.id)
    }

    function reject(item) {
        if (rejectReason.trim().length < 2) {
            shell.toast("warn", "驳回要写理由，至少两个字")
            return
        }
        var id = "reject-" + Date.now()
        shell.request("驳回交付物", id, function () {
            page.rejectReason = ""
            shell.toast("info", "已驳回，理由已记录")
            page.load()
            if (page.selected === item.id) page.open(item)
        }, function (title) {
            shell.toast("error", title)
        })
        bridge.rejectDeliverable(id, item.id, rejectReason.trim())
    }

    function humanSize(bytes) {
        var n = Number(bytes ?? 0)
        if (n < 1024) return n + " B"
        if (n < 1048576) return (n / 1024).toFixed(1) + " KB"
        return (n / 1048576).toFixed(1) + " MB"
    }

    //: The verification result, as a readable line rather than a boolean. A verifier that
    //: wrote a reason and a checker that did not are different answers.
    function verificationText(item) {
        var v = item.verification ?? {}
        if (v.checks && v.checks.length !== undefined) {
            var ok = 0
            for (var i = 0; i < v.checks.length; i++) if (v.checks[i].ok) ok++
            return ok + "/" + v.checks.length + " 项检查通过"
        }
        if (v.reason) return v.reason
        if (item.verified) return "已验证"
        return "尚未验证"
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
        spacing: 16

        RowLayout {
            Layout.fillWidth: true
            Text {
                text: "交付物"
                color: Theme.text
                font.pixelSize: 24
                font.bold: true
                Layout.fillWidth: true
            }
            Text {
                text: page.items.length + " 件"
                color: Theme.textMuted
                font.pixelSize: 12
            }
            Button {
                text: "刷新"
                onClicked: page.load()
            }
        }

        StateBlock {
            Layout.fillWidth: true
            // "Loaded and empty" and "still loading" are different sentences. Passing
            // `page.state` straight through meant a ready-but-empty list drew nothing at
            // all — the panel simply went blank.
            state: page.state === "ready" && page.items.length === 0 ? "empty" : page.state
            busyText: "正在读取交付物…"
            emptyText: "还没有交付物。在「我的员工」里让人做一件事，产出的文件会出现在这里。"
            errorText: page.errorText
            errorDetail: page.errorDetail
            onRetry: page.load()
        }

        Repeater {
            model: page.items

            delegate: Rectangle {
                required property var modelData
                Layout.fillWidth: true
                Layout.preferredHeight: rowColumn.implicitHeight + 26
                radius: 10
                color: Theme.bgCard
                border.width: 1
                border.color: page.selected === modelData.id ? Theme.borderFocus : Theme.border

                ColumnLayout {
                    id: rowColumn
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.top: parent.top
                    anchors.margins: 13
                    spacing: 8

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 10

                        NavIcon {
                            name: "box"
                            size: 18
                            color: modelData.accepted === true ? Theme.success
                                 : modelData.verified ? Theme.accent : Theme.textMuted
                        }

                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 2
                            Text {
                                text: modelData.title || modelData.path
                                color: Theme.text
                                font.pixelSize: 14
                                font.bold: true
                                elide: Text.ElideRight
                                Layout.fillWidth: true
                            }
                            Text {
                                text: (modelData.kind ?? "?") + " · "
                                      + page.humanSize(modelData.size_bytes) + " · 第 "
                                      + (modelData.version ?? 1) + " 版 · "
                                      + page.verificationText(modelData)
                                color: Theme.textFaint
                                font.pixelSize: 11
                                elide: Text.ElideMiddle
                                Layout.fillWidth: true
                            }
                        }

                        Text {
                            visible: modelData.accepted === true
                            text: "已接受"
                            color: Theme.success
                            font.pixelSize: 11
                        }
                        Text {
                            visible: modelData.accepted === false
                            text: "已驳回"
                            color: Theme.danger
                            font.pixelSize: 11
                        }

                        Button {
                            text: page.selected === modelData.id ? "收起" : "查看"
                            onClicked: page.selected === modelData.id
                                       ? page.selected = ""
                                       : page.open(modelData)
                        }
                        Button {
                            text: "打开文件"
                            onClicked: bridge.revealPath("reveal-" + Date.now(), modelData.path)
                        }
                    }

                    Text {
                        visible: (modelData.feedback ?? "") !== ""
                        text: "驳回理由：" + modelData.feedback
                        color: Theme.warn
                        font.pixelSize: 11
                        wrapMode: Text.WordWrap
                        Layout.fillWidth: true
                    }

                    // -------------------------------------------------- 详情 --
                    ColumnLayout {
                        Layout.fillWidth: true
                        visible: page.selected === modelData.id
                        spacing: 8

                        Text {
                            Layout.fillWidth: true
                            text: modelData.path
                            color: Theme.textMuted
                            font.pixelSize: 11
                            elide: Text.ElideMiddle
                        }

                        Rectangle {
                            Layout.fillWidth: true
                            Layout.preferredHeight: evidence.implicitHeight + 20
                            radius: 8
                            color: Theme.surface
                            border.width: 1
                            border.color: Theme.border

                            Text {
                                id: evidence
                                anchors.left: parent.left
                                anchors.right: parent.right
                                anchors.verticalCenter: parent.verticalCenter
                                anchors.margins: 10
                                text: page.detail
                                      ? JSON.stringify(page.detail.verification ?? {}, null, 1)
                                      : "读取验证证据…"
                                color: Theme.textMuted
                                font.pixelSize: 11
                                font.family: "Consolas, monospace"
                                wrapMode: Text.WordWrap
                            }
                        }

                        Text {
                            Layout.fillWidth: true
                            visible: page.detail && (page.detail.versions ?? []).length > 1
                            text: "版本链：" + (page.detail
                                  ? (page.detail.versions ?? []).map(function (v) {
                                        return "v" + v.version
                                    }).join(" → ")
                                  : "")
                            color: Theme.textFaint
                            font.pixelSize: 11
                        }

                        RowLayout {
                            spacing: 8
                            Button {
                                text: page.busy ? "验证中…" : "重新验证"
                                enabled: !page.busy
                                onClicked: page.verify(modelData)
                            }
                            Button {
                                text: "接受"
                                variant: "primary"
                                onClicked: page.accept(modelData)
                            }
                            Item { Layout.fillWidth: true }
                        }

                        RowLayout {
                            spacing: 8
                            TextField {
                                id: reasonField
                                Layout.fillWidth: true
                                placeholderText: "驳回理由（必填，会记录在案）"
                                text: page.rejectReason
                                color: Theme.text
                                onTextChanged: page.rejectReason = text
                                background: Rectangle {
                                    radius: 8
                                    color: Theme.surface
                                    border.width: reasonField.activeFocus ? 2 : 1
                                    border.color: reasonField.activeFocus
                                                  ? Theme.borderFocus : Theme.border
                                }
                            }
                            Button {
                                text: "驳回"
                                variant: "danger"
                                onClicked: page.reject(modelData)
                            }
                        }
                    }
                }
            }
        }

        Item { Layout.preferredHeight: 24 }
    }
}
