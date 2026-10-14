import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import QtQuick.Window
import QtQml
import "pages"
import "components"
import "theme.js" as Theme
import "errors.js" as Errors

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
    readonly property real railWidth: Theme.scale(236, uiScale)

    property string activePage: "dashboard"

    //: 显示多少。普通模式只给"任务、状态、结果"；专家模式再加上步骤、工具调用、token
    //: 和延迟。**持久化在磁盘上**（按账号，见 `app/services/ui_preferences.py`）——
    //: 每次启动都退回默认值，等于每次都要重新选一遍。
    property bool expertMode: false

    function setExpertMode(value) {
        shellRoot.expertMode = value
        var id = "ui-prefs-save-" + Date.now()
        // 写失败不弹错：下次启动退回默认显示而已，不该打断用户正在做的事。
        request("显示偏好", id, function () {}, function () {})
        bridge.saveUiPreferences(id, JSON.stringify({ expert_mode: value }))
    }

    function _loadUiPreferences() {
        if (typeof bridge === "undefined" || !bridge) return
        var id = "ui-prefs-load-" + Date.now()
        request("显示偏好", id, function (payload) {
            if (payload && typeof payload.expert_mode === "boolean") {
                shellRoot.expertMode = payload.expert_mode
            }
        }, function () {
            // 读不到就用默认（普通模式）。一个坏掉的首选项文件不该让界面打不开。
        })
        bridge.loadUiPreferences(id)
    }

    // 启动时读一次。`bridge` 是上下文属性，在 QML 加载之前就设好了，所以这里它在；
    // 后端可能还没就绪，那一次读会失败，回调里已经咽掉，界面就用默认值。
    Component.onCompleted: _loadUiPreferences()
    //: The numbered pages, in shortcut order, exposed so a keyboard test can ask the
    //: running shell which page each `Ctrl+N` should open instead of keeping its own copy
    //: of the list. A copy in the test would pass while the application was wrong.
    //:
    //: **Mode-dependent.** §7 keeps the normal rail restrained, and a `Ctrl+5` that
    //: jumps to a page the rail is not showing is a shortcut to somewhere the user
    //: cannot see. `theme.js` owns both lists so they cannot disagree.
    readonly property var pages: Theme.pagesFor(expertMode)
    //: How many of them have a `Ctrl+N`. Exposed so a keyboard test asks the shell
    //: instead of assuming every page does.
    readonly property int shortcutCount: Theme.shortcutCountFor(expertMode)
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

    //: `onError` is optional, and it exists because a page has to be able to say *which*
    //: of its panels failed. The toast names the request, which is right for a one-off
    //: action and useless for a screen that loads four things at once — the panel that
    //: failed stayed on "读取中…" while the toast faded.
    function request(kind, id, callback, onError) {
        shellRoot.pending[id] = { kind: kind, callback: callback, onError: onError,
                                  epoch: shellRoot.pageEpoch }
        shellRoot.inflight += 1
    }

    function resolve(id, payload) {
        var entry = shellRoot.pending[id]
        delete shellRoot.pending[id]
        shellRoot.inflight = Math.max(0, shellRoot.inflight - 1)
        // The answer is for a page that has been replaced. The counter still counts it —
        // the call really is running — but nothing on screen wants the result.
        if (entry && entry.epoch !== shellRoot.pageEpoch) return
        if (entry && entry.callback) {
            entry.callback(payload)
        }
    }

    function fail(id, message, code) {
        var entry = shellRoot.pending[id]
        delete shellRoot.pending[id]
        shellRoot.inflight = Math.max(0, shellRoot.inflight - 1)
        if (entry && entry.epoch !== shellRoot.pageEpoch) return
        var label = (entry ? entry.kind : "请求")
        // In the reader's terms, not the exception's. The raw text is handed to the page
        // as well — panels put it behind a 「技术详情」 toggle — so this translates the
        // message rather than hiding it.
        var human = Errors.human(message, code)
        var line = label + " 失败：" + human.title
        if (human.hint !== "") line += "　" + human.hint
        shellRoot.toast(code === "timeout" ? "warn" : "error", line)
        if (entry && entry.onError) {
            entry.onError(human.title, code, human)
        }
    }

    //: Bumped on every navigation, and stamped onto every request.
    //:
    //: **A request belongs to the page that made it.** When the user navigates away the
    //: Loader destroys that page, but its callback stayed in `pending` — so when the answer
    //: arrived it ran against a destroyed object and threw
    //: `TypeError: Value is null and could not be converted to an object` at the assignment
    //: inside it. Nothing looked wrong on screen, which is why it survived a screenshot
    //: check: the log filled up and the panel the user had left stayed empty forever.
    //:
    //: Counting navigations is what makes "this answer is for a page that is gone"
    //: decidable, and it only works because there is **one door** for navigation —
    //: `navigate()`. Fifteen places used to assign `activePage` directly, and a counter
    //: that most callers forget to bump is worse than no counter.
    property int pageEpoch: 0

    //: Hand a page the parameters it was navigated to with. Only the ones it declares:
    //: writing an undeclared property is a QML error, and the shell holds one params
    //: object for every page.
    function applyParams(item) {
        if (!item || !shellRoot.pageParams) return
        for (var key in shellRoot.pageParams) {
            if (item[key] !== undefined) {
                item[key] = shellRoot.pageParams[key]
            }
        }
    }

    //: The one way to change page.
    function navigate(name, params) {
        // Params are cleared when there are none: the shell holds one params object for
        // every page, so a stale one would re-fill the next page's form — and with
        // `autoStart` on it, re-run the Agent.
        shellRoot.pageParams = params || ({})
        if (name === shellRoot.activePage) {
            // **The same page, so nothing is destroyed and nothing in flight is stale.**
            // Bumping the epoch here marked the page's own pending requests as belonging to
            // a page that had gone, and because the Loader does not rebuild a page it is
            // already showing, nothing ever replaced them — the screen sat on "读取中…" for
            // ever. That is the one outcome the loading states exist to prevent.
            //
            // The parameters still have to land, since `onLoaded` will not fire again.
            shellRoot.applyParams(pageLoader.item)
            return
        }
        shellRoot.pageEpoch += 1
        shellRoot.activePage = name
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
            expertMode: shellRoot.expertMode
            onNavigate: function (page) { shellRoot.navigate(page) }
            onToggleExpertMode: shellRoot.setExpertMode(!shellRoot.expertMode)
        }

        Rectangle {
            id: contentArea
            Layout.fillWidth: true
            Layout.fillHeight: true
            color: Theme.bg

            // ------------------------------------------------------- startup --
            //
            // The local stack comes up **behind** the window: PostgreSQL may need to
            // initialise a cluster, which is tens of seconds. Doing that before the window
            // exists means the user double-clicks an icon and nothing happens at all — and
            // if the database is missing, the window never appears and the only evidence is
            // a log file.
            //
            // So the window is up first and this strip says what is happening. It is not
            // decoration: the state is also a *word*, not only a colour, because a
            // colour-blind user and a screen reader both need the word.
            Rectangle {
                id: startupBanner
                objectName: "startup-banner"

                //: Guarded: during teardown the context property can already be gone, and
                //: an unguarded read logs a TypeError after the report — in the log
                //: that is supposed to show real errors.
                readonly property string state: bridge ? bridge.startupState : "idle"
                readonly property string stateWord: {
                    switch (state) {
                    case "idle":     return "准备中"
                    case "starting": return "启动中"
                    case "degraded": return "降级"
                    case "failed":   return "失败"
                    default:         return "已就绪"
                    }
                }
                readonly property string a11yName: stateWord + "：" + (bridge ? bridge.startupDetail : "")

                visible: state !== "ready"
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.top: parent.top
                anchors.margins: shellRoot.gap
                height: startupRow.implicitHeight + shellRoot.gap * 1.4
                radius: 10
                color: Theme.bgCard
                border.width: 2
                border.color: state === "failed" ? Theme.danger
                            : state === "degraded" ? Theme.warn
                            : Theme.accent

                Accessible.role: Accessible.StaticText
                Accessible.name: a11yName

                RowLayout {
                    id: startupRow
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    anchors.margins: shellRoot.gap
                    spacing: shellRoot.gap

                    BusyIndicator {
                        objectName: "startup-busy"
                        running: startupBanner.state === "starting" || startupBanner.state === "idle"
                        visible: running
                        Layout.preferredWidth: 24
                        Layout.preferredHeight: 24
                    }

                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 2

                        Text {
                            id: startupStateLabel
                            objectName: "startup-state"
                            text: startupBanner.stateWord
                            color: startupBanner.state === "failed" ? Theme.danger
                                 : startupBanner.state === "degraded" ? Theme.warn
                                 : Theme.accent
                            font.pixelSize: Theme.scale(14, shellRoot.uiScale)
                            font.bold: true
                        }

                        Text {
                            id: startupDetailLabel
                            objectName: "startup-detail"
                            Layout.fillWidth: true
                            text: bridge ? bridge.startupDetail : ""
                            color: Theme.textMuted
                            wrapMode: Text.WordWrap
                            font.pixelSize: Theme.scale(13, shellRoot.uiScale)
                        }
                    }

                    Button {
                        id: startupRetry
                        objectName: "startup-retry"
                        visible: startupBanner.state === "failed" || startupBanner.state === "degraded"
                        text: "重试"
                        Accessible.name: "重新启动本地服务"
                        onClicked: bridge.startEnvironment()
                    }
                }
            }

            Loader {
                id: pageLoader
                objectName: "pageLoader"
                anchors.fill: parent
                anchors.margins: shellRoot.gap
                // The page sits below the banner while it is up, so the banner never covers
                // a control the user is trying to reach.
                anchors.topMargin: startupBanner.visible
                    ? startupBanner.height + shellRoot.gap * 2
                    : shellRoot.gap
                // The page is chosen by name; each one is a file, so a new screen is a new
                // file and not a new branch in a switch.
                sourceComponent: {
                    switch (shellRoot.activePage) {
                    case "employees":    return employeesPage
                    case "deliverables": return deliverablesPage
                    case "memory":       return memoryPage
                    case "approvals":    return approvalsPage
                    case "knowledge":    return knowledgePage
                    case "documents":    return documentsPage
                    case "ask":          return askPage
                    case "conversation": return conversationPage
                    case "permissions":  return permissionsPage
                    case "search":       return searchPage
                    case "graph":        return graphPage
                    case "planner":      return plannerPage
                    case "tasks":        return tasksPage
                    case "projects":     return projectsPage
                    case "notes":        return notesPage
                    case "runs":         return runsPage
                    case "settings":     return settingsPage
                    case "agent":        return agentWorkbenchPage
                    case "project":      return projectDetailPage
                    default:             return dashboardPage
                    }
                }
                onLoaded: {
                    // Parameters are applied once, when the page instance appears, so a
                    // page cannot be recreated by a change it caused itself.
                    shellRoot.applyParams(item)
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
    Component { id: agentWorkbenchPage; AgentWorkbenchPage { shell: shellRoot } }
    Component { id: projectDetailPage; ProjectDetailPage { shell: shellRoot } }
    Component { id: knowledgePage; KnowledgePage {
        shell: shellRoot
        onOpenDocuments: function (kbId, kbName) {
            shellRoot.navigate("documents", { kbId: kbId, kbName: kbName })
        }
    } }
    Component { id: documentsPage; DocumentsPage {
        shell: shellRoot
        onBack: shellRoot.navigate("knowledge")
        onAsk: function (kbId, kbName) {
            shellRoot.navigate("ask", { kbId: kbId, kbName: kbName })
        }
    } }
    Component { id: askPage; AskPage {
        shell: shellRoot
        onBack: shellRoot.navigate("documents")
    } }
    Component { id: tasksPage;     TasksPage     { shell: shellRoot } }
    Component { id: conversationPage; ConversationPage { shell: shellRoot } }
    Component { id: permissionsPage; PermissionsPage { shell: shellRoot } }
    Component { id: searchPage; SearchPage { shell: shellRoot } }
    Component { id: graphPage; GraphPage { shell: shellRoot } }
    Component { id: plannerPage; PlannerPage { shell: shellRoot } }
    Component { id: projectsPage;  ProjectsPage  {
        shell: shellRoot
        onOpenProject: function (projectId, projectName) {
            shellRoot.navigate("project", { projectId: projectId, projectName: projectName })
        }
    } }
    Component { id: notesPage;     NotesPage     { shell: shellRoot } }
    Component { id: runsPage;      RunsPage      { shell: shellRoot } }
    Component { id: settingsPage;  SettingsPage  { shell: shellRoot } }
    Component { id: employeesPage;    EmployeesPage    { shell: shellRoot } }
    Component { id: deliverablesPage; DeliverablesPage { shell: shellRoot } }
    Component { id: memoryPage;       MemoryPage       { shell: shellRoot } }
    Component { id: approvalsPage;    ApprovalsPage    { shell: shellRoot } }

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
        // `shortcutCount`, not `pages.length`: Qt has no `Key_10`, so a
        // `Shortcut` past the ninth can never fire. See `theme.js`.
        model: shellRoot.shortcutCount

        delegate: Shortcut {
            required property int index
            sequence: "Ctrl+" + (index + 1)
            onActivated: shellRoot.navigate(shellRoot.pages[index].key)
        }
    }
}
