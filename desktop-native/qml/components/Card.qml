import QtQuick
import QtQuick.Layouts
import "../theme.js" as Theme

// A raised panel.
//
// **No border by default.** The previous version drew a one-pixel outline around every
// card, and a screen made of outlined boxes reads as a web dashboard — that was the
// complaint. Separation comes from the surface being lighter than the ground, and from the
// spacing between panels.
//
// `outlined: true` is available for the rare case where two panels of the *same* colour
// sit against each other and the seam has to be visible.
Rectangle {
    id: card

    property string title: ""
    property string subtitle: ""
    property int padding: Theme.space4
    property int radius: Theme.radiusMd
    //: A hairline, for when the surface alone does not separate two panels.
    property bool outlined: false
    //: The accent strip down the leading edge. For the one panel on a screen that is the
    //: point of the screen.
    property bool accented: false

    default property alias content: body.data

    color: Theme.bgCard
    border.width: card.outlined ? 1 : 0
    border.color: Theme.divider

    implicitHeight: layout.implicitHeight + card.padding * 2

    Rectangle {
        visible: card.accented
        width: 3
        radius: 1.5
        color: Theme.accent
        anchors.left: parent.left
        anchors.top: parent.top
        anchors.bottom: parent.bottom
        anchors.topMargin: Theme.radiusMd
        anchors.bottomMargin: Theme.radiusMd
        Accessible.ignored: true
    }

    ColumnLayout {
        id: layout
        anchors.fill: parent
        anchors.margins: card.padding
        spacing: Theme.space3

        Text {
            visible: card.title !== ""
            text: card.title
            color: Theme.text
            font.pixelSize: Theme.fontHeading
            font.bold: true
            Layout.fillWidth: true
        }
        Text {
            visible: card.subtitle !== ""
            text: card.subtitle
            color: Theme.textMuted
            font.pixelSize: Theme.fontSmall
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }
        ColumnLayout {
            id: body
            Layout.fillWidth: true
            spacing: Theme.space2
        }
    }
}
