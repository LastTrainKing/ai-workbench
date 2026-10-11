import QtQuick
import QtQuick.Controls.Basic as Basic
import "../theme.js" as Theme

// A switch, restyled. Same reason as `CheckBox`: the Basic indicator is a light track with
// a white knob, which is the brightest object on a dark page.
Basic.Switch {
    id: control

    implicitHeight: Theme.controlHeight
    spacing: Theme.space2
    font.pixelSize: Theme.fontBody

    indicator: Rectangle {
        implicitWidth: 36
        implicitHeight: 20
        x: control.leftPadding
        y: parent.height / 2 - height / 2
        radius: 10
        color: control.checked ? Theme.accent : Theme.surface
        border.width: 1
        border.color: control.checked ? Theme.accent
                    : control.activeFocus ? Theme.borderFocus
                    : Theme.border

        Rectangle {
            x: control.checked ? parent.width - width - 3 : 3
            y: 3
            width: 14
            height: 14
            radius: 7
            color: control.checked ? "#04222a" : Theme.textMuted
            Behavior on x { NumberAnimation { duration: 120; easing.type: Easing.OutCubic } }
        }
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
