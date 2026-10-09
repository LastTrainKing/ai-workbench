import QtQuick
import QtQuick.Layouts
import "../theme.js" as Theme

// A big card. The default surface for everything in a Big Picture layout.
Rectangle {
    id: card

    property string title: ""
    property string subtitle: ""
    default property alias content: body.data

    radius: 12
    color: Theme.bgCard
    border.width: 1
    border.color: Theme.border

    implicitHeight: layout.implicitHeight + 32

    ColumnLayout {
        id: layout
        anchors.fill: parent
        anchors.margins: 16
        spacing: 10

        Text {
            visible: card.title !== ""
            text: card.title
            color: Theme.text
            font.pixelSize: 16
            font.bold: true
            Layout.fillWidth: true
        }
        Text {
            visible: card.subtitle !== ""
            text: card.subtitle
            color: Theme.textMuted
            font.pixelSize: 12
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }
        ColumnLayout {
            id: body
            Layout.fillWidth: true
            spacing: 8
        }
    }
}
