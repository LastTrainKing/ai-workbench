import QtQuick
import QtQuick.Window

// A line icon, drawn rather than typed.
//
// **Not a font glyph.** The obvious shortcut is a private-use codepoint from an icon font,
// and it fails exactly where it matters: this application ships without fonts, Qt prints
// "Cannot find font directory … Qt no longer ships fonts" on every launch, and a missing
// glyph is a hollow box that looks like a rendering bug rather than a missing font.
//
// One drawing routine, one stroke weight, one 24-unit grid, so fifteen icons look like
// fifteen icons from the same set instead of fifteen decisions.
Item {
    id: root

    property string name: ""
    property color color: "#e8eef8"
    property real size: 18
    //: Stroke width in the 24-unit grid. 1.7 reads as a 1.3px line at 18px, which is the
    //: weight Windows uses for its own shell icons.
    property real weight: 1.7

    implicitWidth: size
    implicitHeight: size

    readonly property real dpr: Screen.devicePixelRatio > 0 ? Screen.devicePixelRatio : 1

    Canvas {
        id: canvas
        anchors.fill: parent
        antialiasing: true
        canvasSize: Qt.size(Math.round(root.size * root.dpr), Math.round(root.size * root.dpr))

        onPaint: {
            var ctx = getContext("2d")
            var s = root.size / 24
            ctx.reset()
            ctx.scale(s * root.dpr, s * root.dpr)
            ctx.strokeStyle = root.color
            ctx.lineWidth = root.weight
            ctx.lineCap = "round"
            ctx.lineJoin = "round"
            ctx.fillStyle = root.color

            var n = root.name
            if (n === "home") {
                ctx.beginPath()
                ctx.moveTo(3, 11); ctx.lineTo(12, 3.5); ctx.lineTo(21, 11)
                ctx.moveTo(5.5, 9.5); ctx.lineTo(5.5, 20); ctx.lineTo(18.5, 20); ctx.lineTo(18.5, 9.5)
                ctx.moveTo(10, 20); ctx.lineTo(10, 14); ctx.lineTo(14, 14); ctx.lineTo(14, 20)
                ctx.stroke()
            } else if (n === "folder") {
                ctx.beginPath()
                ctx.moveTo(3, 7)
                ctx.lineTo(9.5, 7); ctx.lineTo(11.5, 9.5); ctx.lineTo(21, 9.5)
                ctx.lineTo(21, 19.5); ctx.lineTo(3, 19.5); ctx.closePath()
                ctx.stroke()
            } else if (n === "check") {
                ctx.beginPath()
                ctx.moveTo(4.5, 4.5); ctx.lineTo(19.5, 4.5); ctx.lineTo(19.5, 19.5); ctx.lineTo(4.5, 19.5); ctx.closePath()
                ctx.moveTo(8.5, 12.2); ctx.lineTo(11, 14.8); ctx.lineTo(15.8, 9)
                ctx.stroke()
            } else if (n === "page") {
                ctx.beginPath()
                ctx.moveTo(6, 3.5); ctx.lineTo(14, 3.5); ctx.lineTo(18.5, 8)
                ctx.lineTo(18.5, 20.5); ctx.lineTo(6, 20.5); ctx.closePath()
                ctx.moveTo(14, 3.5); ctx.lineTo(14, 8); ctx.lineTo(18.5, 8)
                ctx.moveTo(9, 13); ctx.lineTo(15.5, 13)
                ctx.moveTo(9, 16.5); ctx.lineTo(15.5, 16.5)
                ctx.stroke()
            } else if (n === "note") {
                ctx.beginPath()
                ctx.moveTo(6, 3.5); ctx.lineTo(18.5, 3.5); ctx.lineTo(18.5, 20.5); ctx.lineTo(6, 20.5); ctx.closePath()
                ctx.moveTo(9, 8); ctx.lineTo(15.5, 8)
                ctx.moveTo(9, 12); ctx.lineTo(15.5, 12)
                ctx.moveTo(9, 16); ctx.lineTo(13, 16)
                ctx.stroke()
            } else if (n === "chat") {
                ctx.beginPath()
                ctx.moveTo(4, 6.5)
                ctx.lineTo(20, 6.5); ctx.lineTo(20, 16.5); ctx.lineTo(12.5, 16.5)
                ctx.lineTo(8, 20.5); ctx.lineTo(8, 16.5); ctx.lineTo(4, 16.5); ctx.closePath()
                ctx.stroke()
            } else if (n === "spark") {
                ctx.beginPath()
                ctx.moveTo(12, 2.5); ctx.lineTo(13.8, 10.2); ctx.lineTo(21.5, 12)
                ctx.lineTo(13.8, 13.8); ctx.lineTo(12, 21.5); ctx.lineTo(10.2, 13.8)
                ctx.lineTo(2.5, 12); ctx.lineTo(10.2, 10.2); ctx.closePath()
                ctx.stroke()
            } else if (n === "history") {
                ctx.beginPath()
                ctx.arc(12, 12, 8, 0, Math.PI * 2)
                ctx.stroke()
                ctx.beginPath()
                ctx.moveTo(12, 7.5); ctx.lineTo(12, 12.5); ctx.lineTo(16, 14.5)
                ctx.stroke()
            } else if (n === "book") {
                ctx.beginPath()
                ctx.moveTo(3.5, 5.5)
                ctx.bezierCurveTo(7, 3.5, 10.5, 3.5, 12, 5.5)
                ctx.bezierCurveTo(13.5, 3.5, 17, 3.5, 20.5, 5.5)
                ctx.lineTo(20.5, 19)
                ctx.bezierCurveTo(17, 17, 13.5, 17, 12, 19)
                ctx.bezierCurveTo(10.5, 17, 7, 17, 3.5, 19)
                ctx.closePath()
                ctx.moveTo(12, 5.5); ctx.lineTo(12, 19)
                ctx.stroke()
            } else if (n === "question") {
                ctx.beginPath()
                ctx.arc(12, 12, 8.5, 0, Math.PI * 2)
                ctx.stroke()
                ctx.beginPath()
                ctx.moveTo(9.2, 9.6)
                ctx.bezierCurveTo(9.4, 7.6, 14.6, 7.4, 14.6, 10.2)
                ctx.bezierCurveTo(14.6, 12.2, 12, 12.4, 12, 14.4)
                ctx.stroke()
                ctx.beginPath()
                ctx.arc(12, 17.2, 0.9, 0, Math.PI * 2)
                ctx.fill()
            } else if (n === "search") {
                ctx.beginPath()
                ctx.arc(10.8, 10.8, 6.3, 0, Math.PI * 2)
                ctx.stroke()
                ctx.beginPath()
                ctx.moveTo(15.4, 15.4); ctx.lineTo(20.5, 20.5)
                ctx.stroke()
            } else if (n === "graph") {
                ctx.beginPath()
                ctx.moveTo(7.2, 16.2); ctx.lineTo(11.4, 8.6)
                ctx.moveTo(13.2, 8.6); ctx.lineTo(16.8, 14.2)
                ctx.moveTo(8.4, 17.6); ctx.lineTo(15.6, 15.6)
                ctx.stroke()
                ctx.beginPath()
                ctx.arc(6, 18, 2.6, 0, Math.PI * 2)
                ctx.moveTo(14.6, 6)
                ctx.arc(12, 6, 2.6, 0, Math.PI * 2)
                ctx.moveTo(20.6, 15.4)
                ctx.arc(18, 15.4, 2.6, 0, Math.PI * 2)
                ctx.stroke()
            } else if (n === "calendar") {
                ctx.beginPath()
                ctx.moveTo(4, 6.5)
                ctx.lineTo(20, 6.5); ctx.lineTo(20, 20.5); ctx.lineTo(4, 20.5); ctx.closePath()
                ctx.moveTo(4, 11); ctx.lineTo(20, 11)
                ctx.moveTo(8, 3.5); ctx.lineTo(8, 8)
                ctx.moveTo(16, 3.5); ctx.lineTo(16, 8)
                ctx.stroke()
            } else if (n === "shield") {
                ctx.beginPath()
                ctx.moveTo(12, 3)
                ctx.lineTo(20, 6.2); ctx.lineTo(20, 12)
                ctx.bezierCurveTo(20, 16.8, 16.6, 19.8, 12, 21.3)
                ctx.bezierCurveTo(7.4, 19.8, 4, 16.8, 4, 12)
                ctx.lineTo(4, 6.2); ctx.closePath()
                ctx.stroke()
            } else if (n === "gear") {
                ctx.beginPath()
                ctx.arc(12, 12, 3.2, 0, Math.PI * 2)
                ctx.stroke()
                ctx.beginPath()
                for (var i = 0; i < 8; i++) {
                    var a = i * Math.PI / 4
                    ctx.moveTo(12 + Math.cos(a) * 6, 12 + Math.sin(a) * 6)
                    ctx.lineTo(12 + Math.cos(a) * 9, 12 + Math.sin(a) * 9)
                }
                ctx.stroke()
            }
        }
    }

    onColorChanged: canvas.requestPaint()
    onNameChanged: canvas.requestPaint()
    onSizeChanged: canvas.requestPaint()
}
