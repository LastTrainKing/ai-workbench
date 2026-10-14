"""Look at the real control tree instead of at the absence of errors.

`verify_native_shell.py` answers "does the QML load and do the controls work". It cannot
answer "is anything overlapping", because a layout defect is not an error: every item has a
valid geometry, the tree is well formed, and two labels are simply drawn on top of each
other. That is what the runs page did.

This walks the actual item tree at three window sizes and reports:

  clipped   a Text whose natural width exceeds its box, with no elide and no wrapping —
            the string is cut off and nothing says so
  overflow  a child drawn outside a parent that does not scroll (inside a Flickable the
            content is *meant* to be taller than the viewport)
  overlap   two visible Text siblings whose rectangles intersect — unreadable by
            construction
  zero      a visible item with a real size that ends up 0×0, which is how a panel
            silently disappears

Run against the development stack so the pages have real rows to overflow.
"""

from __future__ import annotations

import os
import sys
import time

DATA = r"C:\Users\user\AppData\Local\AI Workbench Data\.runtime"
os.environ["AI_WORKBENCH_DATA_DIR"] = DATA
os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://aiworkbench:aiworkbench@127.0.0.1:5432/aiworkbench"
)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "desktop-native"))

import importlib.util  # noqa: E402

from PySide6.QtCore import QUrl, qInstallMessageHandler  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402
from PySide6.QtQml import QQmlApplicationEngine  # noqa: E402

# Before the engine loads: registers QQuickWindow with shiboken so the root object is
# wrapped as a QQuickWindow, which is where `grabWindow` lives.
from PySide6.QtQuick import QQuickItem, QQuickWindow  # noqa: E402,F401

app = QGuiApplication([*sys.argv[:1], "check-layout"])

from awbnative import resources  # noqa: E402

resources.configure()

_spec = importlib.util.spec_from_file_location(
    "awb_main", os.path.join(ROOT, "desktop-native", "main.py")
)
_awb_main = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_awb_main)
_awb_main.configure_font(app)

from awbnative.bridge import ServiceBridge  # noqa: E402

messages: list[str] = []
qInstallMessageHandler(lambda mode, ctx, msg: messages.append(msg))

bridge = ServiceBridge(owner_id=None)
bridge.record_startup({}, {"ready": True})
engine = QQmlApplicationEngine()
engine.rootContext().setContextProperty("bridge", bridge)
engine.load(QUrl.fromLocalFile(os.path.join(ROOT, "desktop-native", "qml", "Main.qml")))
window = engine.rootObjects()[0]

PAGES = ["dashboard", "projects", "tasks", "documents", "notes",
         "employees", "deliverables",
         "conversation", "agent", "runs", "approvals",
         "knowledge", "ask", "search", "graph", "memory",
         "planner", "permissions", "settings"]
SIZES = [(1920, 1080), (1280, 800), (960, 640)]

TOL = 1.5


def pump(ms: int) -> None:
    end = time.monotonic() + ms / 1000
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.02)


def _prop(item: QQuickItem, name: str):
    """A property, or None. Some QML enums have no shiboken converter and raise on read."""
    try:
        return item.property(name)
    except RuntimeError:
        return None


def _is_scroll_container(item: QQuickItem) -> bool:
    """A Flickable (or ScrollView's flickable) — content larger than the viewport is the
    point of it."""
    return _prop(item, "contentWidth") is not None and _prop(item, "contentHeight") is not None


def _label(item: QQuickItem) -> str:
    text = _prop(item, "text")
    if isinstance(text, str) and text.strip():
        short = text.strip().replace("\n", " ")[:42]
        return f"Text({short!r})"
    name = _prop(item, "objectName")
    if isinstance(name, str) and name:
        return f"#{name}"
    return item.metaObject().className().split("_QML")[0].split("_")[-1] or "Item"


def _rect(item: QQuickItem) -> tuple[float, float, float, float]:
    return (item.x(), item.y(), item.width(), item.height())


def _visible(item: QQuickItem) -> bool:
    return bool(_prop(item, "visible")) and item.width() > 0.5 and item.height() > 0.5


def audit(item: QQuickItem, path: str, findings: list[str], scrolling: bool) -> None:
    children = [c for c in item.childItems() if isinstance(c, QQuickItem) and _visible(c)]
    labels: list[tuple[QQuickItem, tuple[float, float, float, float]]] = []

    for child in children:
        x, y, w, h = _rect(child)
        here = f"{path}/{_label(child)}"

        # --- clipped text -------------------------------------------------------
        text = _prop(child, "text")
        if isinstance(text, str) and text.strip():
            natural = child.implicitWidth()
            # `elide` and `wrapMode` are QML enums with no shiboken converter, so they
            # cannot be read from here. `truncated` and `lineCount` can, and they answer
            # the question directly: an ellipsis means the cut is visible and intended, a
            # multi-line box means it wrapped, and a single line that is wider than its box
            # with no ellipsis is a string that is simply gone.
            truncated = _prop(child, "truncated")
            lines = _prop(child, "lineCount") or 1
            if natural > w + 2 and truncated is False and lines <= 1:
                findings.append(
                    f"clipped   {here}  自然宽 {natural:.0f} > 框宽 {w:.0f}，单行硬切，没有省略号"
                )
            labels.append((child, (x, y, w, h)))

        # --- overflow -----------------------------------------------------------
        # A child wider than the parent that clips it is scrolling content; wider than a
        # parent that does not clip is a defect, and so is anything with a negative origin
        # unless the parent is a scroller.
        if (
            not scrolling
            and not _is_scroll_container(item)
            and (x + w > item.width() + TOL or y + h > item.height() + TOL or x < -TOL or y < -TOL)
        ):
            findings.append(
                f"overflow  {here}  子 {x:.0f},{y:.0f} {w:.0f}×{h:.0f} "
                f"超出父 {item.width():.0f}×{item.height():.0f}"
            )

        # --- zero size ----------------------------------------------------------
        if (
            _prop(child, "visible")
            and (child.width() < 0.5 or child.height() < 0.5)
            and (_prop(child, "text") or _prop(child, "objectName"))
        ):
            findings.append(f"zero      {here}  可见但尺寸 {child.width():.0f}×{child.height():.0f}")

        audit(child, here, findings, scrolling or _is_scroll_container(child))

    # --- text overlap ---------------------------------------------------------
    for i in range(len(labels)):
        for j in range(i + 1, len(labels)):
            (a, ra), (b, rb) = labels[i], labels[j]
            ox = min(ra[0] + ra[2], rb[0] + rb[2]) - max(ra[0], rb[0])
            oy = min(ra[1] + ra[3], rb[1] + rb[3]) - max(ra[1], rb[1])
            if ox > 2 and oy > 2:
                findings.append(
                    f"overlap   {path}  {_label(a)} × {_label(b)}  重叠 {ox:.0f}×{oy:.0f}"
                )


def alignment(item: QQuickItem, findings: list[str]) -> None:
    """Group the page's buttons by their label and report any that do not share an x.

    Scoped to buttons because a label that repeats is the case where a reader compares the
    two without being asked to: the eye runs down the column of 开始 buttons. Two headings
    with the same words in different sections are a different matter, and this does not
    pretend to judge them.

    **Only vertical lists are judged.** The first version compared every instance of a
    label with every other, and reported a Flow of role cards as broken: six 「创建」
    buttons at six x positions, because six cards sit side by side. That is what a flow
    looks like and it is not a defect. What the eye actually compares is a *column* — the
    same control once per row, drifting as it goes down — which is the Planner bug this
    check was written for.

    So instances are clustered into rows by their vertical overlap, and the check only
    fires when every row holds exactly one instance. A grid (several per row) is skipped
    whole rather than half-judged.
    """
    by_text: dict[str, list[tuple[float, float, float]]] = {}
    stack = [item]
    while stack:
        node = stack.pop()
        for child in node.childItems():
            if not isinstance(child, QQuickItem):
                continue
            stack.append(child)
            if "Button" not in child.metaObject().className():
                continue
            if not _visible(child):
                continue
            label = _prop(child, "text")
            if not isinstance(label, str) or not label.strip():
                continue
            x, y = _scene(child, item)
            by_text.setdefault(label.strip(), []).append((round(x, 1), round(y, 1), child.height()))

    for label, instances in sorted(by_text.items()):
        rows = _cluster_by_row(instances)
        if len(rows) < 2 or any(len(row) > 1 for row in rows):
            # One instance, or a layout that puts several on a line. Neither is a column.
            continue
        positions = [row[0][0] for row in rows]
        distinct = sorted(set(positions))
        spread = distinct[-1] - distinct[0]
        if len(distinct) > 1 and spread > 1.5:
            findings.append(
                f"unaligned 按钮「{label}」出现 {len(instances)} 次，"
                f"却有 {len(distinct)} 个不同左边缘（{distinct[0]:.0f}…{distinct[-1]:.0f}，"
                f"相差 {spread:.0f}px）"
            )


def _cluster_by_row(
    instances: list[tuple[float, float, float]],
) -> list[list[tuple[float, float, float]]]:
    """Group buttons that sit on the same visual line.

    Two buttons share a row when their vertical ranges overlap by more than half the
    shorter one — the same rule a reader uses without thinking about it. Clustering rather
    than bucketing by rounded `y` because a row's members need not be pixel-identical:
    a label and its neighbour in a `RowLayout` can differ by a pixel or two of baseline.
    """
    rows: list[list[tuple[float, float, float]]] = []
    for item in sorted(instances, key=lambda t: (t[1], t[0])):
        _x, y, height = item
        for row in rows:
            _rx, ry, rheight = row[0]
            overlap = min(y + height, ry + rheight) - max(y, ry)
            if overlap > min(height, rheight) / 2:
                row.append(item)
                break
        else:
            rows.append([item])
    return rows


def _scene(item: QQuickItem, root: QQuickItem) -> tuple[float, float]:
    x, y = 0.0, 0.0
    node = item
    while node is not None and node is not root:
        x += node.x()
        y += node.y()
        node = node.parentItem()
    return x, y


total = 0
for width, height in SIZES:
    window.setProperty("width", width)
    window.setProperty("height", height)
    pump(400)
    for key in PAGES:
        window.navigate(key, None)
        pump(1700)
        findings: list[str] = []
        audit(window.contentItem(), key, findings, False)
        alignment(window.contentItem(), findings)
        if findings:
            total += len(findings)
            print(f"\n{width}×{height}  {key}  —— {len(findings)} 处")
            for f in findings[:12]:
                print("   ", f)
            if len(findings) > 12:
                print(f"    …另有 {len(findings) - 12} 处")

print("\n" + "-" * 70)
print(f"布局问题合计：{total}")
errors = [m for m in messages if "TypeError" in m or "Cannot assign" in m or "is not a" in m]
print(f"QML 错误：{len(errors)}")
for e in errors[:8]:
    print("   ", e[:150])
raise SystemExit(1 if total else 0)
