import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
// Qualified, for the same reason StateBlock does it: an explicit `QtQuick.Controls.Basic`
// import wins over the implicit same-directory lookup, so a bare `Button` here would be
// the unstyled one and `variant` would not exist. `UI.Button` is the styled one.
import "../components" as UI
import "../theme.js" as Theme

// One produced file, with its evidence and the operations it actually supports.
//
// **This is the card §8.2 asks for**, and the two things it refuses to do are the
// point of it:
//
// * It never shows 「已交付」 over a file that is not on disk. `file_state` is
//   re-stat'd by the backend on every read, and when it says the file is gone the
//   card says so and the buttons go grey with the reason attached — rather than
//   offering 「打开」 on something that is not there.
// * It renders the actions the backend computed, not a fixed toolbar. A format this
//   application cannot preview does not get a 「预览」 button that does nothing; it
//   gets a disabled one that explains why.
Rectangle {
    id: card

    //: One entry from `run_outcome.artifacts`.
    property var artifact: null
    //: The matching entry from `run_outcome.verification` (aligned by id).
    property var verification: null

    signal actionRequested(string actionId)

    Layout.fillWidth: true
    implicitHeight: body.implicitHeight + 22
    radius: 10
    color: Theme.surface
    border.width: 1
    border.color: card.fileMissing ? Theme.warn : Theme.border

    readonly property bool fileMissing: !!(artifact && artifact.file_state
                                           && !artifact.file_state.exists)

    //: The first thing that is unavailable, and why. Shown beside the buttons so the
    //: reason arrives before the click instead of after it.
    readonly property string disabledReason: {
        var list = (artifact && artifact.actions) ? artifact.actions : []
        for (var i = 0; i < list.length; i++) {
            if (!list[i].enabled && list[i].reason) return list[i].reason
        }
        return ""
    }

    ColumnLayout {
        id: body
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.margins: 11
        spacing: 6

        RowLayout {
            Layout.fillWidth: true
            spacing: 8
            Text {
                Layout.fillWidth: true
                text: card.artifact ? card.artifact.title : ""
                color: Theme.text
                font.pixelSize: 14
                font.bold: true
                elide: Text.ElideRight
            }
            Text {
                visible: card.artifact && card.artifact.accepted === true
                text: "已接受"
                color: Theme.success
                font.pixelSize: 11
            }
            Text {
                visible: card.artifact && card.artifact.accepted === false
                text: "已驳回"
                color: Theme.danger
                font.pixelSize: 11
            }
            Text {
                text: "第 " + (card.artifact ? (card.artifact.version ?? 1) : 1) + " 版"
                color: Theme.textFaint
                font.pixelSize: 11
            }
        }

        Text {
            Layout.fillWidth: true
            text: {
                if (!card.artifact) return ""
                // 文件不在了就不报大小。`file_state.size_bytes` 那时是 0，而"0 B"是
                // 一句关于这个文件的话 —— 它会被读成"文件是空的"，比不说更糟。
                var size = card.fileMissing
                         ? ""
                         : Theme.humanBytes(card.artifact.file_state
                                            ? card.artifact.file_state.size_bytes
                                            : card.artifact.size_bytes)
                var parts = [Theme.kindLabel(card.artifact.kind)]
                if (size !== "") parts.push(size)
                parts.push(Theme.verificationSummary(card.verification))
                return parts.join(" · ")
            }
            color: Theme.textMuted
            font.pixelSize: 11
            elide: Text.ElideRight
        }

        Text {
            Layout.fillWidth: true
            visible: card.fileMissing
            text: card.artifact && card.artifact.file_state
                  ? card.artifact.file_state.reason : ""
            color: Theme.warn
            font.pixelSize: 11
            wrapMode: Text.WordWrap
        }

        Text {
            Layout.fillWidth: true
            visible: (card.artifact ? (card.artifact.feedback ?? "") : "") !== ""
            text: "驳回理由：" + (card.artifact ? card.artifact.feedback : "")
            color: Theme.warn
            font.pixelSize: 11
            wrapMode: Text.WordWrap
        }

        //: 固定宽度的按钮行。宽度固定，位置就不随标题长短浮动 —— 上一轮布局审计抓到的
        //: 正是"一排按钮跟着内容整体平移"。
        Flow {
            Layout.fillWidth: true
            spacing: 6

            Repeater {
                model: (card.artifact && card.artifact.actions) ? card.artifact.actions : []

                delegate: UI.Button {
                    required property var modelData
                    text: modelData.label
                    enabled: modelData.enabled
                    variant: modelData.id === "open" ? "primary" : "secondary"
                    Accessible.name: modelData.label
                                     + (modelData.enabled ? "" : "（不可用）")
                    onClicked: card.actionRequested(modelData.id)
                }
            }
        }

        Text {
            Layout.fillWidth: true
            visible: card.disabledReason !== ""
            text: card.disabledReason
            color: Theme.textFaint
            font.pixelSize: 10
            wrapMode: Text.WordWrap
        }
    }
}
