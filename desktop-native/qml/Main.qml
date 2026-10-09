import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import QtQuick.Window
import "pages"
import "components"
import "theme.js" as Theme

// The shell: one window, a navigation rail, a page area, and a toast.
//
// Deliberately one window and no embedded browser. Every control below is a Qt control —
// a real `Button`, a real `TextField`, a real `ScrollView` — so the focus system, the
// input method and the accessibility tree are Qt's, not a DOM's.
ApplicationWindow {
    id: shell

    // DPI: the ratio comes from the screen this window is on, and everything sized in
    // `Theme.scale` derives from it. Qt applies the platform scale factor itself; this is
    // the extra breathing room a Big Picture layout wants on a high-DPI panel.
    readonly property real uiScale: Math.min(1.6, Math.max(0.85, Screen.devicePixelRatio))
    readonly property real gap: Theme.scale(16, uiScale)
    readonly property real railWidth: Theme.scale(220, uiScale)

    property string activePage: "dashboard"
    //: Parameters for the pages that need one (a knowledge base, say). Held here rather
    //: than passed through the Loader, which would recreate the page on every change.
    property var pageParams: ({})
    property string toastText: ""
    property string toastLevel: "info"
    property bool toastVisible: false
    property int inflight: 0

    title: "AI Workbench"
    width: Theme.scale(1280, uiScale)
    height: Theme.scale(840, uiScale)
    minimumWidth: Theme.scale(960, uiScale)
    minimumHeight: Theme.scale(640, uiScale)
    visible: true
    color: Theme.bg

    // ---------------------------------------------------------------- toast --
    function toast(level, message) {
        shell.toastLevel = level
        shell.toastText = message
        shell.toastVisible = true
        toastTimer.restart()
    }

    Timer {
        id: toastTimer
        interval: 6000
        onTriggered: shell.toastVisible = false
    }

    // ---------------------------------------------------------------- bridge --
    // One request id per call, so a slow reply and a fast one cannot be confused. The
    // `pending` map is what makes that explicit rather than hoped for.
    property var pending: ({})

    function request(kind, id, callback) {
        shell.pending[id] = { kind: kind, callback: callback }
        shell.inflight += 1
    }

    function resolve(id, payload) {
        var entry = shell.pending[id]
        delete shell.pending[id]
        shell.inflight = Math.max(0, shell.inflight - 1)
        if (entry && entry.callback) {
            entry.callback(payload)
        }
    }

    function fail(id, message, code) {
        var entry = shell.pending[id]
        delete shell.pending[id]
        shell.inflight = Math.max(0, shell.inflight - 1)
        var label = (entry ? entry.kind : "请求")
        shell.toast(code === "timeout" ? "warn" : "error", label + " 失败：" + message)
        if (entry && entry.onError) {
            entry.onError(message, code)
        }
    }

    Connections {
        target: bridge
        function onCompleted(id, payload) {
            var parsed = payload
            try { parsed = JSON.parse(payload) } catch (e) { /* plain string is fine */ }
            shell.resolve(id, parsed)
        }
        function onFailed(id, message, code) {
            shell.fail(id, message, code)
        }
        function onNotify(level, message) {
            shell.toast(level, message)
        }
    }

    RowLayout {
        anchors.fill: parent
        spacing: 0

        NavRail {
            Layout.fillHeight: true
            Layout.preferredWidth: shell.railWidth
            activePage: shell.activePage
            inflight: shell.inflight
            onNavigate: function (page) { shell.activePage = page }
        }

        Rectangle {
            Layout.fillWidth: true
            Layout.fillHeight: true
            color: Theme.bg

            Loader {
                id: pageLoader
                anchors.fill: parent
                anchors.margins: shell.gap
                // The page is chosen by name; each one is a file, so a new screen is a new
                // file and not a new branch in a switch.
                sourceComponent: {
                    switch (shell.activePage) {
                    case "knowledge": return knowledgePage
                    case "documents": return documentsPage
                    case "ask":       return askPage
                    case "tasks":     return tasksPage
                    case "runs":      return runsPage
                    case "settings":  return settingsPage
                    default:          return dashboardPage
                    }
                }
                onLoaded: {
                    // Parameters are applied once, when the page instance appears, so a
                    // page cannot be recreated by a change it caused itself.
                    if (item && shell.pageParams) {
                        for (var key in shell.pageParams) {
                            item[key] = shell.pageParams[key]
                        }
                    }
                }
            }
        }
    }

    Component { id: dashboardPage; DashboardPage { shell: shell } }
    Component { id: knowledgePage; KnowledgePage {
        shell: shell
        onOpenDocuments: function (kbId, kbName) {
            shell.pageParams = { kbId: kbId, kbName: kbName }
            shell.activePage = "documents"
        }
    } }
    Component { id: documentsPage; DocumentsPage {
        shell: shell
        onBack: shell.activePage = "knowledge"
        onAsk: function (kbId, kbName) {
            shell.pageParams = { kbId: kbId, kbName: kbName }
            shell.activePage = "ask"
        }
    } }
    Component { id: askPage; AskPage {
        shell: shell
        onBack: shell.activePage = "documents"
    } }
    Component { id: tasksPage;     TasksPage     { shell: shell } }
    Component { id: runsPage;      RunsPage      { shell: shell } }
    Component { id: settingsPage;  SettingsPage  { shell: shell } }

    // ---------------------------------------------------------------- toast UI --
    Rectangle {
        id: toastBox
        visible: shell.toastVisible
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.bottom: parent.bottom
        anchors.bottomMargin: shell.gap * 2
        width: Math.min(parent.width - shell.gap * 4, toastLabel.implicitWidth + shell.gap * 3)
        height: toastLabel.implicitHeight + shell.gap * 1.6
        radius: 10
        color: Theme.bgCard
        border.width: 2
        border.color: shell.toastLevel === "error" ? Theme.danger
                    : shell.toastLevel === "warn" ? Theme.warn
                    : Theme.accent

        Text {
            id: toastLabel
            anchors.centerIn: parent
            width: parent.width - shell.gap * 2
            wrapMode: Text.WordWrap
            color: Theme.text
            font.pixelSize: Theme.scale(14, shell.uiScale)
            text: shell.toastText
        }
    }

    // Keyboard: Escape dismisses the toast, Ctrl+1..4 moves between pages. A Big Picture
    // shell is used from a couch; the mouse is the second-class citizen.
    Shortcut { sequence: "Escape"; onActivated: shell.toastVisible = false }
    Shortcut { sequence: "Ctrl+1"; onActivated: shell.activePage = "dashboard" }
    Shortcut { sequence: "Ctrl+2"; onActivated: shell.activePage = "knowledge" }
    Shortcut { sequence: "Ctrl+3"; onActivated: shell.activePage = "tasks" }
    Shortcut { sequence: "Ctrl+4"; onActivated: shell.activePage = "runs" }
    Shortcut { sequence: "Ctrl+5"; onActivated: shell.activePage = "settings" }
}
