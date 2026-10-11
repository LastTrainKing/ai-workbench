import QtQuick
import QtQuick.Layouts
import "../theme.js" as Theme

// One row of a form: a label, and the field it belongs to.
//
// **The geometry is decided here, once.** A `GridLayout` with two columns sizes its first
// column to the widest label, so the gap between a label and its field is whatever is left
// over — on the settings page that was 43px for 模型 and 67px for Base URL, and the label
// sat nine pixels below the centre of its own field. Every row looked assembled rather
// than laid out, which is the complaint, and it is a property of how the columns were
// built rather than of any one row.
//
// The label column is a fixed width, the gap is fixed, the field fills what is left, and
// the label is centred on the row so a taller field (a text area) does not leave it
// floating at the top.
RowLayout {
    id: row

    property string label: ""
    //: A second line under the field, for something that belongs to this field alone.
    property string hint: ""
    //: A shorter column for dense forms. Same width for every row in that form.
    property int labelWidth: Theme.formLabelWidth

    default property alias field: body.data

    Layout.fillWidth: true
    spacing: Theme.formGap

    Text {
        text: row.label
        color: Theme.textMuted
        font.pixelSize: Theme.fontSmall
        Layout.preferredWidth: row.labelWidth
        Layout.alignment: Qt.AlignVCenter
        // A label longer than the column wraps rather than pushing the field sideways.
        wrapMode: Text.WordWrap
    }

    // The field, and — under it — the hint. **Not three columns in the row.** The hint was
    // a sibling of the field in the `RowLayout`, so it took horizontal space from the
    // field: the API Key box came out 875px wide at 1920 while the three boxes above it
    // were 1544, on the same form, for no reason a reader could see.
    ColumnLayout {
        Layout.fillWidth: true
        spacing: Theme.space1

        ColumnLayout {
            id: body
            Layout.fillWidth: true
            spacing: Theme.space1
        }

        Text {
            Layout.fillWidth: true
            visible: row.hint !== ""
            text: row.hint
            color: Theme.textFaint
            font.pixelSize: Theme.fontTiny
            wrapMode: Text.WordWrap
        }
    }
}
