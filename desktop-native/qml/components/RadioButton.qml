import QtQuick
import QtQuick.Controls.Basic as Basic
import "../theme.js" as Theme

// A radio button, restyled — a ring and a dot in the accent, not the platform's white
// circle.
Basic.RadioButton {
    id: control

    implicitHeight: Theme.controlHeight
    spacing: Theme.space2
    font.pixelSize: Theme.fontBody

    indicator: Rectangle {
        implicitWidth: 18
        implicitHeight: 18
        x: control.leftPadding
        y: parent.height / 2 - height / 2
        radius: 9
        color: "transparent"
        border.width: control.checked ? 5 : 1
        border.color: control.checked ? Theme.accent
                    : control.activeFocus ? Theme.borderFocus
                    : Theme.border

        Behavior on border.width { NumberAnimation { duration: 100 } }
    }

    contentItem: Text {
        text: control.text
        color: control.enabled ? Theme.text : Theme.textFaint
        font.family: control.font.family
        font.pixelSize: control.font.pixelSize
        verticalAlignment: Text.AlignVCenter
        leftPadding: control.indicator.width + control.spacing
        elide: Text.ElideRight
    }
}
