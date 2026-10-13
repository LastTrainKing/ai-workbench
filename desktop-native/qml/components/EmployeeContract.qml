import QtQuick
import QtQuick.Layouts
import "../theme.js" as Theme

// 一个员工的岗位合同 —— 任务书 §八。
//
// 单独一个组件而不是页面里的一段，有两个具体理由：
//
//   1. 合同是 12 节，页面里已经有外层 Repeater（遍历员工）和这一层 Repeater（遍历分节）。
//      嵌套的两个 `modelData` 会撞名，写出来的代码要靠在作用域里翻找才能读懂。
//   2. 合同不止这一个地方要用。交付物页说"谁做的"、运行记录说"这是谁的活"时，看到的
//      都该是同一份合同，而不是各自拼一遍。
//
// **不硬编码任何一项内容。** 每一节的文字都来自 `employee`（这个员工自己的）或
// `templates`（这个岗位的合同），取数规则在 `theme.js`，和页面共用同一套。模板里没有
// 的节直接不画 —— 画一个空标题比不画更糟，它看起来像"这一项是空的"。
ColumnLayout {
    id: root

    property var employee: null
    property var templates: []

    Layout.fillWidth: true
    spacing: 10

    //: 合同里每一节都长得一样：一个小标题 + 若干行。抽成一个内部组件，免得 12 遍重复
    //: 的排版参数出现 12 个不同的版本。
    component Section: ColumnLayout {
        id: section
        required property var sectionData
        required property var owner
        required property var catalogue

        Layout.fillWidth: true
        spacing: 3

        readonly property var lines: Theme.contractLines(owner, catalogue, sectionData.key)

        visible: lines.length > 0

        Text {
            Layout.fillWidth: true
            text: section.sectionData.title
            color: Theme.textFaint
            font.pixelSize: Theme.fontTiny
            font.bold: true
            font.letterSpacing: 0.6
            Accessible.role: Accessible.Heading
            Accessible.name: section.sectionData.title
        }

        Repeater {
            model: section.lines
            delegate: RowLayout {
                required property var modelData
                required property int index

                Layout.fillWidth: true
                spacing: 6

                // SOP 是步骤，编号有意义；其余是并列条目，编号只会让人以为有顺序。
                Text {
                    visible: section.sectionData.ordered === true
                    Layout.preferredWidth: 16
                    Layout.alignment: Qt.AlignTop
                    text: (index + 1) + "."
                    color: Theme.textFaint
                    font.pixelSize: Theme.fontSmall
                }
                Text {
                    Layout.fillWidth: true
                    text: section.sectionData.ordered === true
                          ? modelData : "· " + modelData
                    color: section.sectionData.ordered === true ? Theme.text : Theme.textMuted
                    font.pixelSize: Theme.fontSmall
                    wrapMode: Text.WordWrap
                }
            }
        }
    }

    Repeater {
        model: Theme.contractSections
        delegate: Section {
            required property var modelData
            sectionData: modelData
            owner: root.employee
            catalogue: root.templates
        }
    }
}
