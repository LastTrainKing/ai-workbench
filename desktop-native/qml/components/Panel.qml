import QtQuick
import QtQuick.Layouts
import "../theme.js" as Theme

// A block that is genuinely its own surface.
//
// Use it for the one thing on a screen you interact with — a form you are filling in, the
// goal box, an expanded record — and for a panel that has to be read apart from what is
// around it. Not for every section: `Card` is the section, and a screen made of panels is
// the screen this redesign is undoing.
//
// `accented` puts a cyan edge down the leading side, for the single panel that is the
// point of the screen.
Rectangle {
    id: panel

    property string title: ""
    property string subtitle: ""
    property int padding: Theme.space4
    property int radius: Theme.radiusMd
    property bool accented: false

    default property alias content: body.data

    color: Theme.surface
    //: No outline. The fill is the separation; a border on top of it is the double
    //: statement that made the old screens busy.
    border.width: 0

    implicitHeight: layout.implicitHeight + panel.padding * 2

    Rectangle {
        visible: panel.accented
        width: 3
        radius: 1.5
        color: Theme.accent
        anchors.left: parent.left
        anchors.top: parent.top
        anchors.bottom: parent.bottom
        anchors.topMargin: panel.radius
        anchors.bottomMargin: panel.radius
        Accessible.ignored: true
    }

    ColumnLayout {
        id: layout
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.verticalCenter: parent.verticalCenter
        anchors.leftMargin: panel.padding
        anchors.rightMargin: panel.padding
        spacing: Theme.space2

        Text {
            visible: panel.title !== ""
            Layout.fillWidth: true
            text: panel.title
            color: Theme.text
            font.pixelSize: Theme.fontHeading
            font.bold: true
            Accessible.role: Accessible.Heading
            Accessible.name: panel.title
        }

        Text {
            visible: panel.subtitle !== ""
            Layout.fillWidth: true
            text: panel.subtitle
            color: Theme.textMuted
            font.pixelSize: Theme.fontSmall
            wrapMode: Text.WordWrap
        }

        ColumnLayout {
            id: body
            Layout.fillWidth: true
            spacing: Theme.space2
        }
    }
}
