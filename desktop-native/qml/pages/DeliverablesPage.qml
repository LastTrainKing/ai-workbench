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

    //: build_run_id → 当初那句话。**「重新生成」必须重跑原来那个请求，不能拿标题猜。**
    //: 拿标题当目标会生成一个"看起来相关但不是你要的"文件，而用户以为自己按的是重做。
    property var runGoals: ({})

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

    // ------------------------------------------------------------------ 成果之后 --
    function go(name, params) {
        // shell 唯一的导航入口：它会清掉过期的页面参数。
        shell.navigate(name, params)
    }

    //: 载入每个运行的目标，供「重新生成」用。取不到就不猜 —— 见 `runGoals` 的说明。
    function loadRunGoals() {
        var id = "dl-goals-" + Date.now()
        shell.request("运行记录", id, function (payload) {
            var map = {}
            var list = (payload && payload.items) ? payload.items : []
            for (var i = 0; i < list.length; i++) {
                if (list[i].id && list[i].goal) map[list[i].id] = list[i].goal
            }
            page.runGoals = map
        }, function () {
            // 拿不到就不拿。按「重新生成」时会明说找不到原请求，而不是猜一个。
            page.runGoals = ({})
        })
        bridge.loadBuildRuns(id, "50")
    }

    function reveal(item) {
        if (!item.path) {
            shell.toast("warn", "这条成果没有记录文件路径")
            return
        }
        bridge.revealPath("reveal-" + Date.now(), item.path)
    }

    //: 拿同一句话再跑一遍。新的一版会作为独立交付物出现，旧的那份不动 —— 覆盖它才是
    //: 危险的做法，用户就没法比较了。
    function regenerate(item) {
        var goal = page.runGoals[item.build_run_id]
        if (!goal) {
            shell.toast("warn", "找不到当初那次请求，无法原样重跑。可以点「继续修改」手写一句。")
            return
        }
        var id = "dl-regen-" + Date.now()
        shell.request("重新生成", id, function () {
            shell.toast("info", "已经交给员工重新做，完成后会出现在这里")
            page.load()
        }, function (title) {
            shell.toast("error", title)
        })
        bridge.startBuildRun(id, goal, "")
    }

    //: 在这份成果上接着做。**不自动开跑** —— 点「继续修改」的人正要补一句话，直接开跑
    //: 会跳过他正要说的那句。
    function continueFrom(item) {
        go("agent", {
            goal: "在《" + (item.title || item.path || "上一版") + "》的基础上继续修改：",
            autoStart: false
        })
    }

    function humanSize(bytes) { return Theme.humanBytes(bytes) }

    //: The verification result, as a readable line rather than a boolean. A verifier that
    //: wrote a reason and a checker that did not are different answers.
    //:
    //: 三个结果（通过 / 未通过 / 无法验证）都在这句话里。旧写法只数 `ok` 的个数，
    //: 于是"这项没查"和"这项失败"长得一模一样 —— 而它们对用户是两件事。
    function verificationText(item) { return Theme.verificationSummary(item.verification) }

    //: 能不能在应用里读出来，由后端说（`Deliverable.as_dict().previewable`），
    //: 不由这里按后缀猜。两份清单就是两个答案。
    function canPreview(item) { return !!(item && item.previewable && item.path) }

    // ---------------------------------------------------------------- 预览 --
    //
    // §8.3：Markdown / TXT / 代码在原生界面里直接读出来，不跳外部程序。读不了的时候
    // 说清为什么（文件没了 / 不是 UTF-8 / 格式不支持），而不是给一个空白框。
    property var preview: null
    property bool previewBusy: false
    property string previewFor: ""

    function openPreview(item) {
        if (previewFor === item.id) {
            previewFor = ""
            preview = null
            return
        }
        previewFor = item.id
        previewBusy = true
        preview = null
        var id = "dl-preview-" + Date.now()
        shell.request("预览", id, function (payload) {
            page.previewBusy = false
            page.preview = payload
        }, function (title) {
            page.previewBusy = false
            page.preview = { title: item.title, reason: title, text: "", truncated: false }
        })
        bridge.readDeliverableText(id, item.id)
    }

    property bool _loaded: false
    function _firstLoad() {
        if (_loaded || !shell) return
        _loaded = true
        load()
        // 单独一次，因为它只服务「重新生成」这一个按钮，失败了也不该影响列表。
        loadRunGoals()
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
                                text: Theme.kindLabel(modelData.kind) + " · "
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

                        // 固定宽度的操作列。按钮的 x 不能随文件名长短浮动 —— 上一轮
                        // 审计抓到的正是这一类错位：一排按钮跟着卡片内容整体平移。
                        RowLayout {
                            Layout.preferredWidth: 372
                            Layout.alignment: Qt.AlignVCenter
                            spacing: 8

                            Button {
                                Layout.preferredWidth: 56
                                text: page.selected === modelData.id ? "收起" : "查看"
                                onClicked: page.selected === modelData.id
                                           ? page.selected = ""
                                           : page.open(modelData)
                            }
                            Button {
                                Layout.preferredWidth: 56
                                text: "预览"
                                // 文本类才有得预览。其余格式给一个按不动的按钮，
                                // 比给一个点开是空的按钮诚实。
                                enabled: page.canPreview(modelData)
                                Accessible.name: "预览 " + (modelData.title || "")
                                onClicked: page.openPreview(modelData)
                            }
                            Button {
                                Layout.preferredWidth: 56
                                text: "打开"
                                enabled: !!modelData.path
                                Accessible.name: "打开 " + (modelData.title || "")
                                onClicked: bridge.openPath("open-" + Date.now(), modelData.path)
                            }
                            Button {
                                Layout.preferredWidth: 84
                                text: "继续修改"
                                Accessible.name: "继续修改 " + (modelData.title || "")
                                onClicked: page.continueFrom(modelData)
                            }
                            Button {
                                Layout.preferredWidth: 84
                                text: "重新生成"
                                Accessible.name: "重新生成 " + (modelData.title || "")
                                onClicked: page.regenerate(modelData)
                            }
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

                        // ---------------------------------------------------- 预览 --
                        //
                        // §8.3：能读的格式就地读出来。读不了的时候给出原因，
                        // 而不是一个空白框 —— 空白框会被当成"文件是空的"。
                        Rectangle {
                            Layout.fillWidth: true
                            visible: page.previewFor === modelData.id
                                     && (page.preview !== null || page.previewBusy)
                            Layout.preferredHeight: Math.min(320, previewBody.implicitHeight + 24)
                            radius: 8
                            color: Theme.bgInput
                            border.width: 1
                            border.color: Theme.border

                            ColumnLayout {
                                id: previewBody
                                anchors.left: parent.left
                                anchors.right: parent.right
                                anchors.top: parent.top
                                anchors.margins: 12
                                spacing: 6

                                Text {
                                    Layout.fillWidth: true
                                    text: page.preview
                                          ? (page.preview.title + "　"
                                             + Theme.humanBytes(page.preview.size_bytes))
                                          : "正在读取…"
                                    color: Theme.textMuted
                                    font.pixelSize: 11
                                }

                                Text {
                                    Layout.fillWidth: true
                                    visible: page.preview !== null
                                             && (page.preview.reason ?? "") !== ""
                                    text: page.preview ? page.preview.reason : ""
                                    color: Theme.warn
                                    font.pixelSize: 12
                                    wrapMode: Text.WordWrap
                                }

                                ScrollView {
                                    Layout.fillWidth: true
                                    Layout.preferredHeight: 240
                                    visible: page.preview !== null
                                             && (page.preview.text ?? "") !== ""
                                    clip: true

                                    TextArea {
                                        readOnly: true
                                        text: page.preview ? page.preview.text : ""
                                        color: Theme.text
                                        font.pixelSize: 12
                                        font.family: "Consolas, monospace"
                                        wrapMode: TextArea.Wrap
                                        background: null
                                    }
                                }

                                Text {
                                    Layout.fillWidth: true
                                    visible: page.preview !== null && page.preview.truncated === true
                                    text: "内容较长，只显示前面一部分。完整内容请用系统程序打开。"
                                    color: Theme.textFaint
                                    font.pixelSize: 10
                                }
                            }
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
                            // 在文件夹里打开。**跟「打开」是两件事**：打开是让默认程序读这个
                            // 文件，这个是让你看见它在哪 —— 而"文件到底在哪"正是这一版要
                            // 回答的问题，不能只给一个。
                            Button {
                                text: "在文件夹中显示"
                                variant: "ghost"
                                enabled: !!modelData.path
                                Accessible.name: "在文件夹中显示 " + (modelData.title || "")
                                onClicked: page.reveal(modelData)
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
