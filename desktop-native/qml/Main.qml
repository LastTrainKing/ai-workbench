import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import QtQuick.Window
import QtQml
import "pages"
import "components"
import "theme.js" as Theme

// The shell: one window, a navigation rail, a page area, and a toast.
//
// Deliberately one window and no embedded browser. Every control below is a Qt control —
// a real `Button`, a real `TextField`, a real `ScrollView` — so the focus system, the
// input method and the accessibility tree are Qt's, not a DOM's.
ApplicationWindow {
    id: shellRoot

    // DPI: the ratio comes from the screen this window is on, and everything sized in
    // `Theme.scale` derives from it. Qt applies the platform scale factor itself; this is
    // the extra breathing room a Big Picture layout wants on a high-DPI panel.
    readonly property real uiScale: Math.min(1.6, Math.max(0.85, Screen.devicePixelRatio))
    readonly property real gap: Theme.scale(16, uiScale)
    readonly property real railWidth: Theme.scale(220, uiScale)

    property string activePage: "dashboard"
    //: The numbered pages, in shortcut order, exposed so a keyboard test can ask the
    //: running shell which page each `Ctrl+N` should open instead of keeping its own copy
    //: of the list. A copy in the test would pass while the application was wrong.
    readonly property var pages: Theme.pages
    //: The rail's entries, in the order the arrow keys move through them. **A different
    //: list from `pages`**: the rail also carries `documents` and `ask`, which are reached
    //: from a knowledge base and have no shortcut. A test that walked `pages` would stop
    //: two entries short and call a working rail broken — which is what happened.
    readonly property var railEntries: rail.entries
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
        shellRoot.toastLevel = level
        shellRoot.toastText = message
        shellRoot.toastVisible = true
        toastTimer.restart()
    }

    Timer {
        id: toastTimer
        interval: 6000
        onTriggered: shellRoot.toastVisible = false
    }

    // ---------------------------------------------------------------- bridge --
    // One request id per call, so a slow reply and a fast one cannot be confused. The
    // `pending` map is what makes that explicit rather than hoped for.
    property var pending: ({})

    function request(kind, id, callback) {
        shellRoot.pending[id] = { kind: kind, callback: callback }
        shellRoot.inflight += 1
    }

    function resolve(id, payload) {
        var entry = shellRoot.pending[id]
        delete shellRoot.pending[id]
        shellRoot.inflight = Math.max(0, shellRoot.inflight - 1)
        if (entry && entry.callback) {
            entry.callback(payload)
        }
    }

    function fail(id, message, code) {
        var entry = shellRoot.pending[id]
        delete shellRoot.pending[id]
        shellRoot.inflight = Math.max(0, shellRoot.inflight - 1)
        var label = (entry ? entry.kind : "请求")
        shellRoot.toast(code === "timeout" ? "warn" : "error", label + " 失败：" + message)
        if (entry && entry.onError) {
            entry.onError(message, code)
        }
    }

    Connections {
        target: bridge
        function onCompleted(id, payload) {
            var parsed = payload
            try { parsed = JSON.parse(payload) } catch (e) { /* plain string is fine */ }
            shellRoot.resolve(id, parsed)
        }
        function onFailed(id, message, code) {
            shellRoot.fail(id, message, code)
        }
        function onNotify(level, message) {
            shellRoot.toast(level, message)
        }
    }

    RowLayout {
        anchors.fill: parent
        spacing: 0

        NavRail {
            id: rail
            Layout.fillHeight: true
            Layout.preferredWidth: shellRoot.railWidth
            activePage: shellRoot.activePage
            inflight: shellRoot.inflight
            onNavigate: function (page) { shellRoot.activePage = page }
        }

        Rectangle {
            Layout.fillWidth: true
            Layout.fillHeight: true
            color: Theme.bg

            Loader {
                id: pageLoader
                objectName: "pageLoader"
                anchors.fill: parent
                anchors.margins: shellRoot.gap
                // The page is chosen by name; each one is a file, so a new screen is a new
                // file and not a new branch in a switch.
                sourceComponent: {
                    switch (shellRoot.activePage) {
                    case "knowledge": return knowledgePage
                    case "documents": return documentsPage
                    case "ask":       return askPage
                    case "tasks":     return tasksPage
                    case "projects":  return projectsPage
                    case "notes":     return notesPage
                    case "runs":      return runsPage
                    case "settings":  return settingsPage
                    default:          return dashboardPage
                    }
                }
                onLoaded: {
                    // Parameters are applied once, when the page instance appears, so a
                    // page cannot be recreated by a change it caused itself.
                    if (item && shellRoot.pageParams) {
                        for (var key in shellRoot.pageParams) {
                            item[key] = shellRoot.pageParams[key]
                        }
                    }
                }
            }
        }
    }

    // **The window's id is `shellRoot`, not `shell`, and that is not cosmetic.**
    //
    // These components used to read `DashboardPage { shell: shell }`. In QML an unqualified
    // name inside an object's binding resolves to that object's *own* properties before it
    // resolves to an outer id — so the right-hand `shell` was the page's own (empty) `shell`
    // property, and every page was constructed with `shell = null`. The pages still rendered,
    // because their layout does not depend on it; what did not work was everything that
    // calls back into the shell. `shell.request` threw "Cannot call method 'request' of
    // undefined" the moment a button was pressed, so no page could load or write anything.
    //
    // The symptom was a page that looked finished and did nothing, which is why it survived
    // a screenshot check and a bridge-level test. `verify_native_shell.py` now presses the
    // buttons, so a page wired to nothing fails there.
    Component { id: dashboardPage; DashboardPage { shell: shellRoot } }
    Component { id: knowledgePage; KnowledgePage {
        shell: shellRoot
        onOpenDocuments: function (kbId, kbName) {
            shellRoot.pageParams = { kbId: kbId, kbName: kbName }
            shellRoot.activePage = "documents"
        }
    } }
    Component { id: documentsPage; DocumentsPage {
        shell: shellRoot
        onBack: shellRoot.activePage = "knowledge"
        onAsk: function (kbId, kbName) {
            shellRoot.pageParams = { kbId: kbId, kbName: kbName }
            shellRoot.activePage = "ask"
        }
    } }
    Component { id: askPage; AskPage {
        shell: shellRoot
        onBack: shellRoot.activePage = "documents"
    } }
    Component { id: tasksPage;     TasksPage     { shell: shellRoot } }
    Component { id: projectsPage;  ProjectsPage  { shell: shellRoot } }
    Component { id: notesPage;     NotesPage     { shell: shellRoot } }
    Component { id: runsPage;      RunsPage      { shell: shellRoot } }
    Component { id: settingsPage;  SettingsPage  { shell: shellRoot } }

    // ---------------------------------------------------------------- toast UI --
    Rectangle {
        id: toastBox
        visible: shellRoot.toastVisible
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.bottom: parent.bottom
        anchors.bottomMargin: shellRoot.gap * 2
        width: Math.min(parent.width - shellRoot.gap * 4, toastLabel.implicitWidth + shellRoot.gap * 3)
        height: toastLabel.implicitHeight + shellRoot.gap * 1.6
        radius: 10
        color: Theme.bgCard
        border.width: 2
        border.color: shellRoot.toastLevel === "error" ? Theme.danger
                    : shellRoot.toastLevel === "warn" ? Theme.warn
                    : Theme.accent

        Text {
            id: toastLabel
            anchors.centerIn: parent
            width: parent.width - shellRoot.gap * 2
            wrapMode: Text.WordWrap
            color: Theme.text
            font.pixelSize: Theme.scale(14, shellRoot.uiScale)
            text: shellRoot.toastText
        }
    }

    // Keyboard: Escape dismisses the toast; Ctrl+1..7 moves between pages; Up/Down move
    // inside the rail and Enter opens the entry focus is on. A Big Picture shell is used
    // from a couch; the mouse is the second-class citizen.
    //
    // The list of pages and their order come from `theme.js`, which is also where the
    // rail's hint text comes from. Two lists would drift, and a hint that names the wrong
    // key is worse than no hint at all.
    Shortcut { sequence: "Escape"; onActivated: shellRoot.toastVisible = false }

    // `Instantiator`, not `Repeater`: a `Shortcut` is a QObject and a `Repeater` requires
    // its delegate to be an Item. With `Repeater` the whole window failed to load with
    // "Delegate must be of Item type".
    Instantiator {
        model: Theme.pages.length

        delegate: Shortcut {
            required property int index
            sequence: "Ctrl+" + (index + 1)
            onActivated: shellRoot.activePage = Theme.pages[index].key
        }
    }
}
