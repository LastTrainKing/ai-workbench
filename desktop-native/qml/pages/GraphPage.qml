import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "../components"
import "../theme.js" as Theme

// The relationship graph, from the database's own foreign keys.
//
// **Real data, real interaction.** Nodes come from `GraphService.build()`, which projects
// projects, knowledge bases, documents, concepts, tasks, notes and runs out of the tables —
// there is no hand-written demo graph anywhere in this file. Edges are join tables and
// foreign keys, so a line on screen means a row that exists.
//
// Three decisions worth stating:
//
//  * **The layout is capped and chunked.** A few hundred nodes laid out in one pass would
//    freeze the window while it computed; here the initial set is bounded by degree, and the
//    force iterations run a slice at a time off a `Timer` so the window keeps painting. The
//    header says how many were shown out of how many exist, because a graph that silently
//    drops most of its nodes is a graph that lies about the shape of the data.
//  * **A deterministic seed.** Positions start from a hash of the node id, so the same graph
//    lays out the same way twice. A layout that reshuffles on every open makes it impossible
//    to build a mental map of your own data.
//  * **Pan, zoom and selection are here; pinning and clustering are not.** Those are real
//    features and they are not in this version — the header says so rather than leaving the
//    absence to be discovered.
Flickable {
    id: page
    property var shell

    contentWidth: width
    contentHeight: column.implicitHeight
    clip: true

    property var nodes: []
    property var edges: []
    property var stats: ({})
    property var selected: null
    property bool loading: false
    property string errorText: ""
    property bool _loaded: false

    //: How many nodes to lay out. Above this the graph is thinned by degree — the hubs are
    //: what the shape is made of, and the leaves are what makes it unreadable.
    readonly property int maxNodes: 120
    //: Force iterations, run in slices so the window keeps painting.
    readonly property int iterations: 240
    readonly property int sliceSize: 24
    property int _done: 0

    function load() {
        loading = true
        errorText = ""
        var id = "graph-" + Date.now()
        shell.request("知识图谱", id, function (payload) {
            page.ingest(payload)
            page.loading = false
        })
        bridge.loadGraph(id, "")
    }

    function ingest(payload) {
        var all = payload.nodes || []
        var allEdges = payload.edges || []
        page.stats = payload.stats || {}

        // Degree, so the thinning keeps the nodes that carry the structure.
        var degree = {}
        for (var i = 0; i < allEdges.length; i++) {
            degree[allEdges[i].source] = (degree[allEdges[i].source] || 0) + 1
            degree[allEdges[i].target] = (degree[allEdges[i].target] || 0) + 1
        }
        var ranked = all.slice().sort(function (a, b) {
            return (degree[b.id] || 0) - (degree[a.id] || 0)
        })
        var kept = ranked.slice(0, page.maxNodes)
        var keep = {}
        for (var k = 0; k < kept.length; k++) keep[kept[k].id] = true

        var laid = []
        for (var j = 0; j < kept.length; j++) {
            var node = kept[j]
            // Deterministic seed from the id: the same graph lays out the same way twice.
            var h = 0
            for (var c = 0; c < node.id.length; c++) h = (h * 31 + node.id.charCodeAt(c)) % 100000
            laid.push({
                id: node.id,
                type: node.type,
                label: node.label,
                meta: node.meta || {},
                degree: degree[node.id] || 0,
                x: 200 + (h % 400),
                y: 160 + ((h * 7) % 320),
                vx: 0,
                vy: 0
            })
        }

        var links = []
        for (var e = 0; e < allEdges.length; e++) {
            if (keep[allEdges[e].source] && keep[allEdges[e].target]) {
                links.push({ s: allEdges[e].source, t: allEdges[e].target, type: allEdges[e].type })
            }
        }

        page.nodes = laid
        page.edges = links
        page.selected = null
        page._done = 0
        if (laid.length > 0) layoutTimer.restart()
        canvas.requestPaint()
    }

    //: One slice of the force simulation. `Timer` rather than a loop: the window keeps
    //: painting between slices, so a large graph never freezes the interface.
    function step() {
        var ns = page.nodes
        var es = page.edges
        var index = {}
        for (var i = 0; i < ns.length; i++) index[ns[i].id] = i

        for (var it = 0; it < page.sliceSize; it++) {
            // Repulsion between every pair. O(n²), which is exactly why n is capped.
            for (var a = 0; a < ns.length; a++) {
                for (var b = a + 1; b < ns.length; b++) {
                    var dx = ns[b].x - ns[a].x
                    var dy = ns[b].y - ns[a].y
                    var d2 = dx * dx + dy * dy
                    if (d2 < 1) d2 = 1
                    var f = 5200 / d2
                    var d = Math.sqrt(d2)
                    var fx = (dx / d) * f
                    var fy = (dy / d) * f
                    ns[a].vx -= fx; ns[a].vy -= fy
                    ns[b].vx += fx; ns[b].vy += fy
                }
            }
            // Attraction along edges.
            for (var e = 0; e < es.length; e++) {
                var s = ns[index[es[e].s]]
                var t = ns[index[es[e].t]]
                if (!s || !t) continue
                var ex = t.x - s.x
                var ey = t.y - s.y
                var ed = Math.max(1, Math.sqrt(ex * ex + ey * ey))
                var pull = (ed - 110) * 0.012
                s.vx += (ex / ed) * pull; s.vy += (ey / ed) * pull
                t.vx -= (ex / ed) * pull; t.vy -= (ey / ed) * pull
            }
            // Integrate, with damping, and keep everything in frame.
            for (var n = 0; n < ns.length; n++) {
                var node = ns[n]
                node.vx *= 0.82; node.vy *= 0.82
                node.x += node.vx; node.y += node.vy
                node.x = Math.max(40, Math.min(880, node.x))
                node.y = Math.max(40, Math.min(620, node.y))
            }
        }

        page._done += page.sliceSize
        canvas.requestPaint()
        if (page._done < page.iterations) layoutTimer.restart()
    }

    function typeColor(t) {
        switch (t) {
        case "project":       return "#6366f1"
        case "knowledge_base": return "#0ea5e9"
        case "document":      return "#14b8a6"
        case "concept":       return "#a855f7"
        case "task":          return "#f59e0b"
        case "note":          return "#84cc16"
        case "ai_run":        return "#ef4444"
        default:              return "#94a3b8"
        }
    }

    function selectedEdges() {
        if (!selected) return []
        var out = []
        for (var i = 0; i < page.edges.length; i++) {
            var e = page.edges[i]
            if (e.s === selected.id || e.t === selected.id) out.push(e)
        }
        return out
    }

    function labelFor(id) {
        for (var i = 0; i < page.nodes.length; i++) {
            if (page.nodes[i].id === id) return page.nodes[i].label
        }
        return id
    }

    Timer {
        id: layoutTimer
        interval: 16
        repeat: false
        onTriggered: page.step()
    }

    Component.onCompleted: load()

    ColumnLayout {
        id: column
        width: page.width
        spacing: 16

        Text {
            text: "知识图谱"
            color: Theme.text
            font.pixelSize: 24
            font.bold: true
        }

        Card {
            Layout.fillWidth: true
            title: "关系图"
            subtitle: page.loading
                      ? "读取中…"
                      : (page.nodes.length + " 个节点 / " + page.edges.length + " 条关系"
                         + (page.stats.truncated ? "（服务端还有更多，已截断）" : "")
                         + (page.stats.nodes > page.nodes.length
                            ? "　显示前 " + page.nodes.length + " 个（按连接数）" : ""))

            RowLayout {
                Layout.fillWidth: true
                spacing: 10
                Button {
                    objectName: "graph-reload"
                    text: "重新读取"
                    onClicked: page.load()
                    Accessible.name: "重新读取图谱"
                }
                Button {
                    objectName: "graph-relayout"
                    text: "重新布局"
                    enabled: page.nodes.length > 0
                    onClicked: {
                        page._done = 0
                        layoutTimer.restart()
                    }
                    Accessible.name: "重新计算布局"
                }
                Text {
                    Layout.fillWidth: true
                    color: Theme.textFaint
                    font.pixelSize: 11
                    text: page._done < page.iterations && page.nodes.length > 0
                          ? ("布局计算中 " + Math.round(100 * page._done / page.iterations) + "%（分片计算，界面不阻塞）")
                          : "滚轮缩放，拖动平移，点节点看它的关系。固定节点与聚类尚未实现。"
                }
            }

            Text {
                Layout.fillWidth: true
                visible: page.errorText !== ""
                wrapMode: Text.WordWrap
                color: Theme.danger
                font.pixelSize: 12
                text: page.errorText
            }

            Text {
                Layout.fillWidth: true
                visible: !page.loading && page.nodes.length === 0
                color: Theme.textMuted
                font.pixelSize: 13
                text: "还没有可画的关系。创建项目、知识库或任务之后再回来看。"
            }

            // The canvas. One item, so panning and zooming are a transform rather than a
            // relayout of hundreds of delegates.
            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: 520
                visible: page.nodes.length > 0
                radius: 10
                color: Theme.surface
                border.width: 1
                border.color: Theme.border
                clip: true

                property real zoom: 1.0
                property real offsetX: 0
                property real offsetY: 0

                Canvas {
                    id: canvas
                    anchors.fill: parent
                    renderStrategy: Canvas.Immediate
                    transform: [
                        Translate { x: parent.offsetX; y: parent.offsetY },
                        Scale { origin.x: 0; origin.y: 0; xScale: parent.zoom; yScale: parent.zoom }
                    ]

                    onPaint: {
                        var ctx = getContext("2d")
                        ctx.reset()
                        ctx.clearRect(0, 0, canvas.width, canvas.height)

                        // Edges first, so nodes sit on top of them.
                        ctx.lineWidth = 1
                        ctx.strokeStyle = Theme.border
                        for (var i = 0; i < page.edges.length; i++) {
                            var e = page.edges[i]
                            var s = null, t = null
                            for (var j = 0; j < page.nodes.length; j++) {
                                if (page.nodes[j].id === e.s) s = page.nodes[j]
                                if (page.nodes[j].id === e.t) t = page.nodes[j]
                            }
                            if (!s || !t) continue
                            var touches = page.selected && (e.s === page.selected.id || e.t === page.selected.id)
                            ctx.strokeStyle = touches ? Theme.accent : Theme.border
                            ctx.lineWidth = touches ? 2 : 1
                            ctx.beginPath()
                            ctx.moveTo(s.x, s.y)
                            ctx.lineTo(t.x, t.y)
                            ctx.stroke()
                        }

                        // Nodes, sized by how connected they are.
                        for (var k = 0; k < page.nodes.length; k++) {
                            var node = page.nodes[k]
                            var r = 5 + Math.min(11, node.degree)
                            ctx.beginPath()
                            ctx.arc(node.x, node.y, r, 0, Math.PI * 2)
                            ctx.fillStyle = page.typeColor(node.type)
                            ctx.fill()
                            if (page.selected && page.selected.id === node.id) {
                                ctx.lineWidth = 3
                                ctx.strokeStyle = Theme.text
                                ctx.stroke()
                            }
                        }
                    }
                }

                MouseArea {
                    anchors.fill: parent
                    acceptedButtons: Qt.LeftButton
                    property real lastX: 0
                    property real lastY: 0
                    property bool dragging: false

                    onPressed: function (mouse) {
                        lastX = mouse.x
                        lastY = mouse.y
                        dragging = false
                    }
                    onPositionChanged: function (mouse) {
                        if (!pressed) return
                        var dx = mouse.x - lastX
                        var dy = mouse.y - lastY
                        if (Math.abs(dx) + Math.abs(dy) > 2) dragging = true
                        parent.offsetX += dx
                        parent.offsetY += dy
                        lastX = mouse.x
                        lastY = mouse.y
                    }
                    onClicked: function (mouse) {
                        if (dragging) return
                        var gx = (mouse.x - parent.offsetX) / parent.zoom
                        var gy = (mouse.y - parent.offsetY) / parent.zoom
                        var best = null
                        var bestD = 900
                        for (var i = 0; i < page.nodes.length; i++) {
                            var n = page.nodes[i]
                            var d = (n.x - gx) * (n.x - gx) + (n.y - gy) * (n.y - gy)
                            if (d < bestD) { bestD = d; best = n }
                        }
                        page.selected = best
                    }
                    onWheel: function (wheel) {
                        var factor = wheel.angleDelta.y > 0 ? 1.12 : 1 / 1.12
                        parent.zoom = Math.max(0.35, Math.min(3.0, parent.zoom * factor))
                    }
                }
            }

            // Legend, from the types actually present rather than a fixed list.
            RowLayout {
                Layout.fillWidth: true
                spacing: 12
                visible: page.nodes.length > 0
                Repeater {
                    model: {
                        var seen = {}
                        var out = []
                        for (var i = 0; i < page.nodes.length; i++) {
                            if (!seen[page.nodes[i].type]) {
                                seen[page.nodes[i].type] = true
                                out.push(page.nodes[i].type)
                            }
                        }
                        return out
                    }
                    delegate: RowLayout {
                        required property string modelData
                        spacing: 4
                        Rectangle {
                            width: 10; height: 10; radius: 5
                            color: page.typeColor(modelData)
                        }
                        Text {
                            text: modelData
                            color: Theme.textMuted
                            font.pixelSize: 11
                        }
                    }
                }
            }
        }

        Card {
            Layout.fillWidth: true
            visible: page.selected !== null
            title: "节点"
            subtitle: page.selected ? page.selected.type : ""

            Text {
                Layout.fillWidth: true
                wrapMode: Text.WrapAnywhere
                color: Theme.text
                font.pixelSize: 14
                text: page.selected ? page.selected.label : ""
            }

            Text {
                Layout.fillWidth: true
                color: Theme.textFaint
                font.pixelSize: 11
                font.family: "Consolas"
                text: page.selected
                      ? (page.selected.id + "　连接 " + page.selected.degree + " 条")
                      : ""
            }

            Text {
                Layout.fillWidth: true
                color: Theme.textMuted
                font.pixelSize: 12
                text: "关系（" + page.selectedEdges().length + "）"
            }

            Repeater {
                model: page.selected ? page.selectedEdges() : []
                delegate: Text {
                    required property var modelData
                    Layout.fillWidth: true
                    wrapMode: Text.WrapAnywhere
                    color: Theme.textMuted
                    font.pixelSize: 12
                    text: "— " + modelData.type + " — "
                          + (modelData.s === page.selected.id
                             ? page.labelFor(modelData.t)
                             : page.labelFor(modelData.s))
                }
            }
        }

        Item { Layout.preferredHeight: 24 }
    }
}
