import QtQuick
import QtQuick.Controls.Basic as Basic
import "../theme.js" as Theme

// A checkbox, restyled.
//
// **The default indicator is a light-grey square.** On this ground it is the brightest
// thing on the page — the permissions screen had one next to the path probe and it read as
// a rendering artefact rather than as a control. This shadows `Basic.CheckBox` for every
// page that imports `../components`, the same way `Button` and `TextField` do.
Basic.CheckBox {
    id: control

    implicitHeight: Theme.controlHeight
    spacing: Theme.space2
    font.pixelSize: Theme.fontBody

    indicator: Rectangle {
        implicitWidth: 18
        implicitHeight: 18
        x: control.leftPadding
        y: parent.height / 2 - height / 2
        radius: 4
        color: control.checked ? Theme.accent
             : control.hovered ? Theme.surfaceHover
             : "transparent"
        border.width: 1
        border.color: control.checked ? Theme.accent
                    : control.activeFocus ? Theme.borderFocus
                    : Theme.border

        // Drawn rather than a glyph: this build ships no icon font.
        Canvas {
            anchors.fill: parent
            visible: control.checked
            onPaint: {
                var ctx = getContext("2d")
                ctx.reset()
                ctx.strokeStyle = "#04222a"
                ctx.lineWidth = 2
                ctx.lineCap = "round"
                ctx.lineJoin = "round"
                ctx.beginPath()
                ctx.moveTo(4.5, 9.2)
                ctx.lineTo(7.6, 12.4)
                ctx.lineTo(13.6, 5.8)
                ctx.stroke()
            }
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
