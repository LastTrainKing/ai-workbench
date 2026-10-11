import QtQuick
import QtQuick.Controls.Basic as Basic
import "../theme.js" as Theme

// The application's button.
//
// **This file shadows `QtQuick.Controls.Basic`'s `Button`.** A page writes
// `import QtQuick.Controls.Basic` and then `import "../components"`, and in QML the last
// import that provides a name wins — so every `Button` in every page is this one. That is
// deliberate and it is the reason the file exists: the default Basic button is a flat
// light-grey rectangle, and the previous pass had one on every screen. Restyling it here
// fixes sixteen pages at once instead of sixteen times.
//
// `Basic.Button` underneath, not a `Rectangle`: the API (`text`, `checkable`, `checked`,
// `pressed`, `onClicked`, `display`, `icon`) and the focus/keys handling stay Qt's.
//
// Variants carry the hierarchy the old design did not have:
//
//   primary    one per screen — the thing the user came to do
//   secondary  everything else that is a real action (the default)
//   ghost      actions inside a row, where a filled box would be noise
//   danger     irreversible actions, and only those
Basic.Button {
    id: control

    property string variant: "secondary"

    implicitHeight: Theme.controlHeight
    implicitWidth: Math.max(96, content.implicitWidth + 28)
    leftPadding: 14
    rightPadding: 14
    font.pixelSize: Theme.fontBody
    //: A shorter label for the same action, shown when the button is too narrow for the
    //: full one. Empty means the full text always fits.
    property string shortText: ""

    readonly property bool _isPrimary: variant === "primary"
    readonly property bool _isGhost: variant === "ghost"
    readonly property bool _isDanger: variant === "danger"
    //: A checkable button that is on. **This was missing**, so the Planner's status filters
    //: were six identical filled buttons and nothing said which one was filtering the list —
    //: a control with a state the user cannot see is a control that does not work.
    readonly property bool _isOn: control.checkable && control.checked

    readonly property color _fill: {
        if (!control.enabled) return control._isGhost ? "transparent" : Theme.bgCard
        if (control._isOn) return control.hovered ? Qt.rgba(Theme.accent.r, Theme.accent.g,
                                                            Theme.accent.b, 0.24)
                                                  : Theme.accentSoft
        if (control._isPrimary) {
            return control.down ? Theme.accentDim
                 : control.hovered ? Qt.lighter(Theme.accent, 1.12)
                 : Theme.accent
        }
        if (control._isDanger) {
            return control.down ? Qt.darker(Theme.danger, 1.25)
                 : control.hovered ? Qt.lighter(Theme.danger, 1.1)
                 : Qt.rgba(Theme.danger.r, Theme.danger.g, Theme.danger.b, 0.14)
        }
        if (control._isGhost) {
            return control.hovered || control.down ? Theme.bgCardHover : "transparent"
        }
        return control.down ? Theme.bgCard
             : control.hovered ? Theme.bgCardHover
             : Theme.bgCard
    }

    readonly property color _ink: {
        if (!control.enabled) return Theme.textFaint
        if (control._isOn) return Theme.accent
        if (control._isPrimary) return "#04222a"
        if (control._isDanger) return Theme.danger
        return control._isGhost ? Theme.textMuted : Theme.text
    }

    background: Rectangle {
        radius: Theme.radiusSm
        color: control._fill
        // A border only where it separates: never on the filled primary, and only faintly
        // on a secondary so it reads as a surface rather than as an outline.
        border.width: control._isOn ? 1 : (control._isPrimary || control._isGhost ? 0 : 1)
        border.color: control._isOn ? Theme.accent
                    : control._isDanger ? Qt.rgba(Theme.danger.r, Theme.danger.g, Theme.danger.b, 0.45)
                    : Theme.border
        Behavior on color { ColorAnimation { duration: 90 } }
    }

    // Focus is visible, not implied: a keyboard user has to be able to see where they are.
    // Drawn outside the background so it survives the fill changing on hover.
    Rectangle {
        anchors.fill: parent
        anchors.margins: -3
        radius: Theme.radiusSm + 3
        color: "transparent"
        border.width: 2
        border.color: Theme.borderFocus
        visible: control.visualFocus
        Accessible.ignored: true
    }

    contentItem: Text {
        id: content
        text: control.shortText !== "" && control.width < 110 ? control.shortText : control.text
        color: control._ink
        // Member by member, not `font: control.font`. Assigning the group and then
        // `font.bold` is "Property has already been assigned a value" — and it takes the
        // whole window down, because a component that fails to load is a type that does
        // not exist.
        font.family: control.font.family
        font.pixelSize: control.font.pixelSize
        font.bold: control._isPrimary
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
        Accessible.ignored: true
    }
}
