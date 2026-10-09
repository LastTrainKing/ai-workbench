import QtQuick
import "../theme.js" as Theme

// A status chip whose colour comes from the same function the rest of the app uses, so a
// status cannot be green here and amber in the API.
Rectangle {
    id: pill

    property string status: ""
    property string label: ""

    implicitWidth: text.implicitWidth + 20
    implicitHeight: 24
    radius: 12
    color: "transparent"
    border.width: 1
    border.color: Theme.runTone(pill.status)

    Text {
        id: text
        anchors.centerIn: parent
        text: pill.label !== "" ? pill.label : pill.status
        color: Theme.runTone(pill.status)
        font.pixelSize: 11
        font.bold: true
    }
}
