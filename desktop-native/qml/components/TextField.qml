import QtQuick
import QtQuick.Controls.Basic as Basic
import "../theme.js" as Theme

// A field, restyled.
//
// Like `Button.qml`, this shadows the `Basic` control for any page that imports
// `../components`: a form is where a dark interface usually falls apart, because the
// platform default draws a white box with a system border on it.
//
// The field is *recessed* — darker than the panel it sits in — so the eye reads it as
// somewhere to type rather than as another button.
Basic.TextField {
    id: control

    implicitHeight: Theme.controlHeight
    leftPadding: 12
    rightPadding: 12
    topPadding: 0
    bottomPadding: 0
    font.pixelSize: Theme.fontBody
    color: Theme.text
    placeholderTextColor: Theme.textFaint
    selectionColor: Theme.accent
    selectedTextColor: "#04222a"
    //: The caret is the accent, not the system highlight.
    cursorDelegate: Item {
        implicitWidth: 1
        Rectangle {
            width: 1
            height: parent.height
            color: Theme.accent
            opacity: control.cursorVisible ? 1 : 0
        }
    }

    background: Rectangle {
        radius: Theme.radiusSm
        color: control.enabled ? Theme.bgInput : Theme.bgCard
        border.width: 1
        border.color: control.activeFocus ? Theme.borderFocus
                    : control.hovered ? Theme.border
                    : Theme.divider
        Behavior on border.color { ColorAnimation { duration: 90 } }
    }
}
