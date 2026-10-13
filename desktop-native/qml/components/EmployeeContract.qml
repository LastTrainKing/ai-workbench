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

    //: 有没有任何一节岗位声明有内容。用来决定那条"尚未生效"的警告是否出现 ——
    //: 一个没有内容的警告比没有警告更糟。
    readonly property bool hasAnyDeclared: {
        for (var i = 0; i < Theme.declaredSections.length; i++) {
            var s = Theme.declaredSections[i]
            if (Theme.declaredLines(employee, templates, s.key, s.render).length > 0) return true
        }
        return false
    }

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

    //: 岗位声明的一节。与 `Section` 分开，因为**形状不同**：这里的取值是字典
    //: （`{read: [...], write: [...]}`），要按 render 分派，不能走 `contractLines`。
    component DeclaredSection: ColumnLayout {
        id: declared
        required property var sectionData
        required property var owner
        required property var catalogue

        Layout.fillWidth: true
        spacing: 3

        readonly property var lines: Theme.declaredLines(
            owner, catalogue, sectionData.key, sectionData.render)

        visible: lines.length > 0

        Text {
            Layout.fillWidth: true
            text: declared.sectionData.title
            color: Theme.textFaint
            font.pixelSize: Theme.fontTiny
            font.bold: true
            font.letterSpacing: 0.6
            Accessible.role: Accessible.Heading
            Accessible.name: declared.sectionData.title
        }

        Repeater {
            model: declared.lines
            delegate: Text {
                required property var modelData
                Layout.fillWidth: true
                text: "· " + modelData
                color: Theme.textMuted
                font.pixelSize: Theme.fontSmall
                wrapMode: Text.WordWrap
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

    // ------------------------------------------- 岗位声明（**尚未接入执行**）----
    //
    // 这三节展示的是岗位声明，而运行时并不读它们 —— 见 `theme.js` 里 `declaredSections`
    // 上方那段说明（那是查过的结论，不是推测）。
    //
    // 所以它们**必须与上面的合同分开画，并且明确标注**。"这个员工只能写全局记忆"是一句
    // 假话，而且是安全方向上的假话：用户会据此认为存在一道并不存在的墙。字段确实存在，
    // 藏起来是另一种不诚实 —— 所以展示，但说清它现在算什么。
    Repeater {
        model: Theme.declaredSections
        delegate: DeclaredSection {
            required property var modelData
            sectionData: modelData
            owner: root.employee
            catalogue: root.templates
        }
    }

    //: 那条警告只画一次，画在三节之后。
    Text {
        Layout.fillWidth: true
        visible: root.hasAnyDeclared
        text: Theme.declaredNotice
        color: Theme.warn
        font.pixelSize: Theme.fontTiny
        wrapMode: Text.WordWrap
    }
}
