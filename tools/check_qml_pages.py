"""Load every page and report the QML errors.

A page with a syntax error or an unresolvable binding still gets a `rootObjects()` entry in
some Qt versions, so counting roots is not enough — this prints the engine's own messages,
which is the only place a broken binding shows up before someone clicks it.

Run:  python build/rc/check_qml_pages.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "desktop-native"))

from PySide6.QtCore import QUrl, qInstallMessageHandler  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402
from PySide6.QtQml import QQmlApplicationEngine  # noqa: E402

app = QGuiApplication(sys.argv[:1] + ["qml-check"])
from awbnative import resources  # noqa: E402

resources.configure()

MESSAGES: list[str] = []
qInstallMessageHandler(lambda mode, ctx, msg: MESSAGES.append(str(msg)))

engine = QQmlApplicationEngine()
root = REPO / "desktop-native" / "qml" / "Main.qml"
engine.load(QUrl.fromLocalFile(str(root)))

print(f"  Main.qml 根对象: {len(engine.rootObjects())}")
errors = [m for m in MESSAGES if ".qml" in m and ("is not" in m or "Cannot" in m or "Unable" in m
                                                 or "Expected" in m or "error" in m.lower())]
for message in MESSAGES[:20]:
    print("   !", message[:220])

PAGES = [
    "DashboardPage", "EmployeesPage", "DeliverablesPage", "MemoryPage", "ApprovalsPage",
    "TasksPage", "ProjectsPage", "NotesPage", "KnowledgePage", "DocumentsPage",
    "AskPage", "SearchPage", "GraphPage", "PlannerPage", "RunsPage", "ConversationPage",
    "AgentWorkbenchPage", "PermissionsPage", "SettingsPage", "ProjectDetailPage",
]
print()
print("  逐个加载页面（不接 shell，只看它自己能不能构造）：")
broken: list[str] = []
for name in PAGES:
    path = REPO / "desktop-native" / "qml" / "pages" / f"{name}.qml"
    if not path.exists():
        broken.append(f"{name}（文件不存在）")
        print(f"   缺失  {name}")
        continue
    before = len(MESSAGES)
    sub = QQmlApplicationEngine()
    sub.load(QUrl.fromLocalFile(str(path)))
    new = [m for m in MESSAGES[before:] if "Error" in m or "error" in m or "Cannot" in m
           or "Expected" in m or "is not a" in m]
    # A page loaded without `shell` legitimately reports one TypeError when it calls
    # `shell.request`; that is the harness, not the page. `bridge` is the same story and
    # was missed here: it is a context property the real application sets before the QML
    # is loaded, so a page that does `Connections { target: bridge }` — which is what
    # `AgentWorkbenchPage` has always done — is reported broken by a harness that never
    # provided it. Filtering both is filtering the harness's own gap, not a page's defect.
    CONTEXT_PROPS = ("shell", "bridge")
    #: The other phrasing of the same gap. `shell.request(...)` with no `shell` reads as
    #: "Cannot call method 'request' of undefined" — the word `shell` is not in the
    #: message, so the name filter above misses it. Listed as an exact phrase rather than
    #: a loose "of undefined" so that a genuinely broken binding still reports.
    HARNESS_GAPS = ("Cannot call method 'request' of undefined",)
    real = [
        m
        for m in new
        if not any(p in m for p in CONTEXT_PROPS)
        and not any(g in m for g in HARNESS_GAPS)
        and "null" not in m.lower()
    ]
    if real:
        broken.append(name)
        print(f"   问题  {name}")
        for message in real[:2]:
            print("         ", message[:200])
    else:
        print(f"   OK    {name}")

print()
if broken:
    print(f"结果：{len(PAGES) - len(broken)}/{len(PAGES)} 页面可加载；问题：{'、'.join(broken)}")
    raise SystemExit(1)
print(f"结果：{len(PAGES)}/{len(PAGES)} 页面可加载")
raise SystemExit(0)
