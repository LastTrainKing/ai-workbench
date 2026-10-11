import QtQuick
import QtQuick.Layouts
import "../theme.js" as Theme

// A group of content. **It draws nothing.**
//
// The page is one dark ground and content sits on it, grouped by a quiet label and space.
// The previous version filled every instance with a blue-black rectangle and outlined it —
// and because one component was used for a page section, a list, a form block and a single
// row, twenty boxes ended up on one screen. That is what "still looks like an internal
// admin console" was describing, and it was one component's default.
//
// When a block genuinely is its own surface — the thing on the page you type into, a panel
// that has to be read apart from its neighbours — use `Panel`, which is the same idea with
// a fill. The two are separate components rather than one with a flag, so a page states
// which it means instead of inheriting a default.
//
// `title` is a *group label*: small, muted, letter-spaced. A heading that shouts is a
// heading that competes with the content under it.
ColumnLayout {
    id: card

    property string title: ""
    property string subtitle: ""
    //: A hairline above the group, for two sections of the same kind sitting together.
    property bool separated: false

    default property alias content: body.data

    spacing: Theme.space2

    Rectangle {
        visible: card.separated
        Layout.fillWidth: true
        Layout.topMargin: Theme.space3
        implicitHeight: 1
        color: Theme.divider
        Accessible.ignored: true
    }

    Text {
        visible: card.title !== ""
        Layout.fillWidth: true
        Layout.topMargin: card.separated ? Theme.space2 : Theme.space3
        text: card.title
        color: Theme.textFaint
        font.pixelSize: Theme.fontSmall
        font.bold: true
        font.letterSpacing: 0.8
        Accessible.role: Accessible.Heading
        Accessible.name: card.title
    }

    Text {
        visible: card.subtitle !== ""
        Layout.fillWidth: true
        text: card.subtitle
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
