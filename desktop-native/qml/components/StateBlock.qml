import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
// Qualified, because an explicit `QtQuick.Controls.Basic` import wins over the implicit
// same-directory lookup: inside this file a bare `Button` is the Basic one. `UI.Button`
// is the styled one in this directory.
import "../components" as UI
import "../theme.js" as Theme

// What a panel shows when it has no data to show.
//
// **The four states are separate on purpose.** The previous pass had one string,
// "读取中…", and it stayed on screen when the call had already failed — so a broken panel
// and a slow one looked identical, and neither ever said what was wrong or offered a way
// out. `loading`, `empty` and `error` are different things and a person can act on the
// difference.
//
// `ready` draws nothing at all: the panel's own content is the state.
ColumnLayout {
    id: block

    //: loading | ready | empty | error
    property string state: "loading"
    //: Shown for `empty`. Says what would put something here.
    property string emptyText: ""
    //: Shown for `error`. The message the call actually failed with.
    property string errorText: ""
    //: What is being loaded, so "读取中…" can say what it is waiting for.
    property string busyText: "读取中…"
    property string retryText: "重试"
    signal retry()

    spacing: Theme.space2
    visible: block.state !== "ready"

    RowLayout {
        Layout.fillWidth: true
        spacing: Theme.space2
        visible: block.state === "loading"

        BusyIndicator {
            running: block.state === "loading"
            Layout.preferredWidth: 16
            Layout.preferredHeight: 16
            Accessible.ignored: true
        }
        Text {
            Layout.fillWidth: true
            text: block.busyText
            color: Theme.textFaint
            font.pixelSize: Theme.fontSmall
        }
    }

    Text {
        Layout.fillWidth: true
        visible: block.state === "empty"
        text: block.emptyText
        color: Theme.textFaint
        font.pixelSize: Theme.fontSmall
        wrapMode: Text.WordWrap
    }

    ColumnLayout {
        Layout.fillWidth: true
        spacing: Theme.space2
        visible: block.state === "error"

        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.space2
            Rectangle {
                width: 7; height: 7; radius: 3.5
                color: Theme.danger
                Layout.alignment: Qt.AlignTop
                Layout.topMargin: 5
                Accessible.ignored: true
            }
            Text {
                Layout.fillWidth: true
                text: block.errorText !== "" ? block.errorText : "加载失败"
                color: Theme.danger
                font.pixelSize: Theme.fontSmall
                wrapMode: Text.WordWrap
            }
        }
        UI.Button {
            // A real retry, wired to the caller's own reload — not a hint to press F5.
            text: block.retryText
            onClicked: block.retry()
        }
    }
}
