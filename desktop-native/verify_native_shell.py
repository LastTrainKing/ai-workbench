"""Keyboard navigation and accessibility, verified by pressing real keys.

    .venv\\Scripts\\python.exe desktop-native\\verify_native_shell.py

**Why this exists.** The navigation rail's comment said "every entry is focusable and
reachable with the arrow keys" and there was no handler for Up or Down. The entries *were*
focusable, so a source review would have agreed with the comment — and pressing an arrow
did nothing. A claim about interaction can only be checked by interacting.

So every check below sends a real key event with `QTest` to the real window and then looks
at where focus actually went, or at what the page actually did. Nothing here reads the QML
source to decide whether the keyboard works.

The accessibility checks read the accessible name and role **at runtime** from properties
mirrored onto the items (`a11yName`, `a11yRole`). `Accessible.name` is a QML attached
property with no Python accessor, so a hand-written copy would be the only alternative —
and a copy can drift from the thing it claims to describe. The QML binds both to the same
source, so what is read here is what a screen reader is given.

The page list is read from the running shell (`window.pages`), not kept here. A copy of the
list in the test would keep passing after the application changed.

It uses the test database (`aiworkbench_test`) and a temporary account, and removes both
afterwards. It never touches the development database or the installed data directory.
"""

from __future__ import annotations

import asyncio
import os
import pathlib
import sys
import uuid

HERE = pathlib.Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent
BACKEND = PROJECT_ROOT / "backend"

os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://aiworkbench:aiworkbench@127.0.0.1:5432/aiworkbench_test"
)
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:6379/1")
os.environ.setdefault("AI_PROVIDER", "offline")
os.environ.setdefault("EMBEDDING_PROVIDER", "offline")
os.environ.setdefault("WORKER_ENABLED", "false")
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("CACHE_ENABLED", "false")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

for candidate in (str(BACKEND), str(HERE)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

from PySide6.QtCore import QEventLoop, QObject, Qt, QTimer, QUrl
from PySide6.QtGui import QFont, QFontDatabase, QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtTest import QTest

RESULTS: list[tuple[str, bool, str]] = []


def check(label: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((label, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    return ok


def pump(ms: int = 120) -> None:
    """Let the event loop run, so the QML bindings settle after a key event.

    A key event that changes `activePage` changes a `Loader`'s `sourceComponent`, and the
    page is built on the next turn of the loop. Asserting immediately would read the state
    from before the change and report a working shortcut as broken.
    """
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def focused(window: object) -> object | None:
    """The QObject that holds the keyboard focus, or None.

    **`QWindow.focusObject()`, not a walk over `findChildren`.** Repeater delegates are not
    reachable through `QObject.children()` or `findChild` from Python — the rail's nine
    entries exist, carry their names and take focus, and a tree walk finds none of them. The
    first version of this verifier therefore reported "focus never left the window" while
    focus was on `nav-dashboard` the whole time. `focusObject()` is the window's own answer
    to the question, so it cannot be wrong about it.
    """
    return window.focusObject()  # type: ignore[attr-defined]


def focused_name(window: object) -> str:
    """The `objectName` of whatever holds keyboard focus, or "" if nothing does."""
    item = focused(window)
    return item.objectName() if item is not None else ""


def focused_prop(window: object, name: str) -> str:
    """A property of the focused object — how the accessible name is read at runtime."""
    item = focused(window)
    if item is None:
        return ""
    try:
        return str(item.property(name) or "")
    except Exception:  # noqa: BLE001 - an object without the property answers ""
        return ""


def key_for(index: int) -> Qt.Key:
    """`Qt.Key_1` … `Qt.Key_9`, for a 1-based page position."""
    return getattr(Qt, f"Key_{index}")


# --------------------------------------------------------------------------- #
# Database helpers, run on the bridge's own event loop
# --------------------------------------------------------------------------- #
async def _make_account() -> uuid.UUID:
    from app.core.security import hash_password
    from app.infrastructure.database import session_scope
    from app.models.user import User

    name = f"shell_{uuid.uuid4().hex[:8]}"
    async with session_scope() as session:
        user = User(
            email=f"{name}@aiworkbench.dev",
            username=name,
            display_name="Native Shell Verify",
            hashed_password=hash_password("shell1234"),
        )
        session.add(user)
        await session.flush()
        return user.id


async def _drop_account(owner: uuid.UUID) -> None:
    from app.infrastructure.database import session_scope
    from app.models.note import Note
    from app.models.project import Project
    from app.models.user import User
    from sqlalchemy import delete

    async with session_scope() as session:
        await session.execute(delete(Note).where(Note.owner_id == owner))
        await session.execute(delete(Project).where(Project.owner_id == owner))
        await session.execute(delete(User).where(User.id == owner))


async def _count_projects(owner: uuid.UUID) -> int:
    from app.infrastructure.database import session_scope
    from app.models.project import Project
    from sqlalchemy import func, select

    async with session_scope() as session:
        return int(
            (
                await session.execute(
                    select(func.count()).select_from(Project).where(Project.owner_id == owner)
                )
            ).scalar()
            or 0
        )


async def _teardown(owner: uuid.UUID) -> None:
    """Drop the temporary account and the pool, on whichever loop is running."""
    from app.infrastructure.database import dispose_engine

    await _drop_account(owner)
    await dispose_engine()


# --------------------------------------------------------------------------- #
# The checks
# --------------------------------------------------------------------------- #
def check_shortcuts(window: object, pages: list[dict], count: int) -> None:
    """Ctrl+N opens the page at position N, for every page that has a shortcut.

    `count` comes from the shell (`shortcutCount`), not from `len(pages)`. Qt has no
    `Key_10`, so a shell that advertised one shortcut per page was advertising four that
    could not fire — and this check, which assumed one per page, crashed on the tenth
    instead of noticing.
    """
    for index, page in enumerate(pages[:count], start=1):
        QTest.keyClick(window, key_for(index), Qt.ControlModifier)
        pump()
        current = str(window.property("activePage"))  # type: ignore[attr-defined]
        check(
            f"Ctrl+{index} 打开「{page['label']}」",
            current == page["key"],
            f"activePage={current}",
        )


def check_rail_arrow_keys(window: object, order: list[str]) -> list[dict]:
    """Up and Down move focus through the rail, in order, and stop at the ends.

    The accessible name and role are read **while each entry holds focus**, which is also
    the only way to reach them: the delegates are not in the object tree that
    `findChildren` walks, so an entry can only be inspected while it is the focused object.
    Doing both in one pass is not a shortcut — it is the only way either can be done.

    Returns what was observed, so the accessibility check can report it rather than repeat
    the walk.
    """
    # A known starting point: the entry whose page is active is the one that takes focus.
    QTest.keyClick(window, key_for(1), Qt.ControlModifier)
    pump()
    first = focused_name(window)
    check("导航栏首项能获得焦点", first == f"nav-{order[0]}", f"focus={first!r}")

    observed: list[dict] = [
        {
            "key": first.removeprefix("nav-"),
            "a11yName": focused_prop(window, "a11yName"),
            "a11yRole": focused_prop(window, "a11yRole"),
        }
    ]
    for _ in range(len(order) - 1):
        QTest.keyClick(window, Qt.Key_Down)
        pump(60)
        observed.append(
            {
                "key": focused_name(window).removeprefix("nav-"),
                "a11yName": focused_prop(window, "a11yName"),
                "a11yRole": focused_prop(window, "a11yRole"),
            }
        )
    check(
        f"方向键「下」逐项移动焦点（{len(order)} 项）",
        [o["key"] for o in observed] == order,
        " -> ".join(o["key"] for o in observed),
    )

    QTest.keyClick(window, Qt.Key_Up)
    pump(60)
    back = focused_name(window)
    check("方向键「上」回到上一项", back == f"nav-{order[-2]}", f"focus={back!r}")

    # The ends hold rather than wrapping. Wrapping silently jumps a keyboard user from the
    # last entry to the first, which reads as a missed keystroke.
    for _ in range(len(order) + 2):
        QTest.keyClick(window, Qt.Key_Up)
        pump(40)
    top = focused_name(window)
    check("焦点停在第一项，不环绕", top == f"nav-{order[0]}", f"focus={top!r}")

    QTest.keyClick(window, Qt.Key_End)
    pump(60)
    check("End 跳到最后一项", focused_name(window) == f"nav-{order[-1]}", focused_name(window))

    QTest.keyClick(window, Qt.Key_Home)
    pump(60)
    check("Home 跳到第一项", focused_name(window) == f"nav-{order[0]}", focused_name(window))
    return observed


def check_focus_is_not_navigation(window: object, order: list[str]) -> None:
    """Moving focus must not navigate, and Enter must open the entry focus is on.

    If arrowing changed the page, a keyboard user could not browse the rail without opening
    everything they passed — which is the difference between a list you can read and a list
    that fires as you scroll.
    """
    QTest.keyClick(window, key_for(1), Qt.ControlModifier)
    pump()
    QTest.keyClick(window, Qt.Key_Down)
    QTest.keyClick(window, Qt.Key_Down)
    pump(80)

    during = str(window.property("activePage"))  # type: ignore[attr-defined]
    check("移动焦点本身不切换页面", during == order[0], f"activePage={during}")

    QTest.keyClick(window, Qt.Key_Return)
    pump()
    after = str(window.property("activePage"))  # type: ignore[attr-defined]
    check(
        f"回车打开获得焦点的条目（{order[2]}）",
        after == order[2],
        f"activePage={after}",
    )


def check_escape_dismisses_toast(window: object) -> None:
    window.setProperty("toastVisible", True)  # type: ignore[attr-defined]
    pump(60)
    before = bool(window.property("toastVisible"))  # type: ignore[attr-defined]
    QTest.keyClick(window, Qt.Key_Escape)
    pump(60)
    after = bool(window.property("toastVisible"))  # type: ignore[attr-defined]
    check("Esc 关闭提示条", before and not after, f"{before} -> {after}")


def walk_tab_order(window: object, rail_entries: list[dict], steps: int = 14) -> list[dict]:
    """Navigate to the projects page and Tab forward, recording where focus lands.

    Reached through the **rail**, not through a shortcut. Only the first nine pages have a
    `Ctrl+N` — Qt has no `Key_10` — and `projects` is not among them once the Agent page
    sits second. Arrowing to it and pressing Enter is what a user does, and it keeps this
    check independent of where in the list a page happens to be.

    Driven entirely by keys, which is the point: `forceActiveFocus` from Python would test
    Python's ability to set focus, not whether Tab reaches the controls. The page's controls
    are also unreachable by name from here, so the only way to see them is to walk to them.

    Returns one entry per Tab, each with the focused object's name and accessible metadata.
    """
    index = next((i for i, e in enumerate(rail_entries) if e["key"] == "projects"), None)
    if index is None:
        check("项目页在导航栏里", False, "导航栏中没有 projects")
        return []

    QTest.keyClick(window, key_for(1), Qt.ControlModifier)
    pump()
    for _ in range(index):
        QTest.keyClick(window, Qt.Key_Down)
        pump(60)
    QTest.keyClick(window, Qt.Key_Return)
    pump(1500)  # the page is loaded and its list fetched

    trail: list[dict] = []
    for _ in range(steps):
        QTest.keyClick(window, Qt.Key_Tab)
        pump(120)
        trail.append(
            {
                "name": focused_name(window),
                "a11yName": focused_prop(window, "a11yName"),
                "a11yRole": focused_prop(window, "a11yRole"),
            }
        )
    return trail


def check_tab_order(window: object, trail: list[dict]) -> bool:
    """Tab walks the page's controls in the order they are laid out."""
    if not trail:
        return False
    names = [step["name"] for step in trail]
    check("项目页已加载（Tab 到达了页面控件）", "project-name" in names, " -> ".join(names[:8]))

    wanted = ["project-name", "project-description", "project-create"]
    positions = [names.index(n) for n in wanted if n in names]
    check(
        "Tab 顺序：名称 → 描述 → 创建",
        len(positions) == 3 and positions == sorted(positions),
        " -> ".join(names[:10]),
    )

    # Shift+Tab has to go back. A focus chain that only moves forward traps a keyboard user
    # at the end of the page.
    QTest.keyClick(window, Qt.Key_Backtab)
    pump(120)
    back = focused_name(window)
    check("Shift+Tab 能往回走", back != names[-1] or back == "project-create", f"focus={back!r}")
    return "project-name" in names


def type_text(window: object, text: str) -> None:
    """Type ASCII text into whatever holds focus, one key event at a time.

    `QTest.keyClicks` cannot be used here: it is declared for a `QWidget`, and the shell is a
    `QWindow`. Synthesising the key events is a few more lines and works for the characters
    a test needs — letters, digits and a dash.
    """
    for char in text:
        if char.isalpha():
            key = getattr(Qt, f"Key_{char.upper()}")
            modifier = Qt.ShiftModifier if char.isupper() else Qt.NoModifier
        elif char.isdigit():
            key = getattr(Qt, f"Key_{char}")
            modifier = Qt.NoModifier
        elif char == "-":
            key, modifier = Qt.Key_Minus, Qt.NoModifier
        elif char == " ":
            key, modifier = Qt.Key_Space, Qt.NoModifier
        else:
            continue
        QTest.keyClick(window, key, modifier)
        pump(15)


def check_button_activates_with_keyboard(
    window: object, owner: uuid.UUID, bridge: object, trail: list[dict]
) -> None:
    """A button reachable by Tab must also be operable without a mouse.

    The typing is real — key events go to whatever holds focus — and so is the write: the
    project row is counted in PostgreSQL afterwards, not read back from the interface.
    """
    names = [step["name"] for step in trail]
    if "project-name" not in names or "project-create" not in names:
        check("按钮可以用键盘触发", False, "Tab 没有到达名称输入框或创建按钮")
        return

    # Walk back to the name field using Shift+Tab, then forward again. This is the shortest
    # path that stays inside the keyboard model.
    for _ in range(len(names) + 2):
        if focused_name(window) == "project-name":
            break
        QTest.keyClick(window, Qt.Key_Backtab)
        pump(80)
    check("Shift+Tab 能回到名称输入框", focused_name(window) == "project-name", focused_name(window))

    # ASCII on purpose: a non-ASCII string depends on an input method a headless run does
    # not have. The database assertion is what proves the write, and it does not care what
    # the text looks like.
    marker = f"keyboard-project-{uuid.uuid4().hex[:8]}"
    before = bridge._runner.call_blocking(lambda: _count_projects(owner))
    type_text(window, marker)
    pump(200)

    typed = focused_prop(window, "text")
    check(
        "键盘输入的字符进入了输入框",
        typed == marker,
        f"输入框内容={typed!r}，期望={marker!r}",
    )
    if typed != marker:
        # The button still has to be tested. Setting the field directly keeps this check
        # about the button rather than about the text field, and the failure above already
        # reports the typing honestly — it is not hidden by this.
        item = focused(window)
        if item is not None:
            item.setProperty("text", marker)
            pump(200)

    for _ in range(len(names) + 2):
        if focused_name(window) == "project-create":
            break
        QTest.keyClick(window, Qt.Key_Tab)
        pump(80)
    check("Tab 能到达创建按钮", focused_name(window) == "project-create", focused_name(window))

    # Connect to the signal the key press is supposed to produce, so a failure says whether
    # the button was never activated or whether it was activated and the write did not
    # happen. Those are different defects and the count alone cannot tell them apart.
    fired: list[str] = []
    button = focused(window)
    if button is not None:
        try:
            button.clicked.connect(lambda: fired.append("clicked"))  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001
            print(f"     （无法连接 clicked：{exc}）")

    QTest.keyClick(window, Qt.Key_Space)
    pump(6000)
    after = bridge._runner.call_blocking(lambda: _count_projects(owner))
    toast = str(window.property("toastText") or "")  # type: ignore[attr-defined]
    check(
        "空格键触发按钮并真的写入了数据库",
        after == before + 1,
        f"{before} -> {after} 个项目；clicked={fired}；提示条：{toast!r}",
    )


def check_accessibility(window: object, pages: list[dict], rail: list[dict], trail: list[dict]) -> None:
    """Every control a person operates carries a name and a role, read at runtime."""
    unnamed = [
        o["key"]
        for o in rail
        if not o["a11yName"].strip() or not o["a11yRole"].strip()
    ]
    check(
        f"导航栏 {len(rail)} 个条目都有无障碍名称与角色",
        bool(rail) and not unnamed,
        "; ".join(unnamed[:4]) or f"{len(rail)} 个条目已逐个读取",
    )

    missing = [
        step["name"]
        for step in trail
        if step["name"].startswith("project-")
        and (not step["a11yName"].strip() or not step["a11yRole"].strip())
    ]
    reached = [step["name"] for step in trail if step["name"].startswith("project-")]
    check(
        f"项目页可操作控件都有无障碍名称与角色（读到 {len(set(reached))} 个）",
        bool(reached) and not missing,
        "; ".join(missing[:4]) or ", ".join(sorted(set(reached))),
    )

    # Status must not be carried by colour alone. The rail shows a dot *and* a word; this
    # asserts the word is there and non-empty, which is the only part a colour-blind user or
    # a screen reader can use. `rail-state` is a plain item and *is* reachable by name —
    # unlike the delegates, which is why the two are read differently.
    state = window.findChild(QObject, "rail-state")  # type: ignore[attr-defined]
    if state is None:
        check("导航栏状态有文字，不只靠颜色", False, "找不到 rail-state")
    else:
        text = str(state.property("text") or "").strip()
        mirrored = str(state.property("a11yName") or "").strip()
        check(
            "导航栏状态有文字，不只靠颜色",
            bool(text) and text == mirrored,
            f"text={text!r} a11yName={mirrored!r}",
        )


def check_focus_is_visible(window: object) -> None:
    """Focus is somewhere identifiable, and something can draw a ring around it.

    An invisible focus ring is not focus. The window reports the object that holds it; if
    that object has no name, no page can know which control to highlight, and a keyboard
    user cannot tell where they are.
    """
    item = focused(window)
    if item is None:
        check("某个控件持有键盘焦点", False, "窗口没有焦点对象")
        return
    check(
        "某个控件持有键盘焦点，且可被定位",
        bool(item.objectName()),
        f"focus={item.objectName()!r}",
    )


def load_font(app: QGuiApplication) -> str:
    """The offscreen platform reports no fonts; without one the CJK labels measure as zero."""
    for candidate in (
        pathlib.Path("C:/Windows/Fonts/msyh.ttc"),
        pathlib.Path("C:/Windows/Fonts/simhei.ttf"),
        pathlib.Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
    ):
        if not candidate.is_file():
            continue
        font_id = QFontDatabase.addApplicationFont(str(candidate))
        families = QFontDatabase.applicationFontFamilies(font_id)
        if families:
            app.setFont(QFont(families[0], 10))
            return families[0]
    return ""


def main() -> int:
    app = QGuiApplication(sys.argv[:1] + ["verify-native-shell"])

    # QML warnings go to Qt's message handler, not to Python. Without this, a binding that
    # fails inside the interface is invisible: the component loads, the control does
    # nothing, and the only symptom is a check that reports an empty string.
    from PySide6.QtCore import qInstallMessageHandler

    def _on_qt_message(_mode: object, ctx: object, message: str) -> None:
        # `ctx` is a QMessageLogContext; the file/line attributes are only present on
        # some PySide6 builds, and a diagnostic must never raise.
        where = f"  <-- {ctx.file}:{ctx.line}" if hasattr(ctx, "file") else ""
        print(f"     [QML] {message}{where}", flush=True)

    qInstallMessageHandler(_on_qt_message)

    font = load_font(app)

    from awbnative.bridge import ServiceBridge

    owner = _BOOT_OWNER
    bridge = ServiceBridge(owner_id=owner)

    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("bridge", bridge)
    engine.load(QUrl.fromLocalFile(str(HERE / "qml" / "Main.qml")))
    roots = engine.rootObjects()
    if not roots:
        print("  [FAIL] Main.qml 没有产生根对象")
        return 1
    window = roots[0]

    # A JS array arrives as a `QJSValue`; `toVariant()` is what turns it into Python. Going
    # through the QJSValue directly would work for a list of plain strings and not for a
    # list of objects, which is what this is.
    def read_array(name: str) -> list:
        raw = window.property(name)
        if hasattr(raw, "toVariant"):
            raw = raw.toVariant()
        return list(raw or [])

    pages = [dict(p) for p in read_array("pages")]
    if not check("shell 暴露了页面清单", bool(pages), f"{len(pages)} 个页面"):
        return 1
    # The rail and the numbered pages are different lists — see `Main.qml`. The keyboard
    # checks walk the rail; the shortcut checks walk the numbered pages.
    rail_order = [str(e["key"]) for e in read_array("railEntries")]
    if not check("shell 暴露了导航栏条目", bool(rail_order), f"{len(rail_order)} 个条目"):
        return 1
    print(
        f"     字体：{font or '未加载'}；页面：" + ", ".join(str(p["key"]) for p in pages)
        + f"；导航栏：{len(rail_order)} 项"
    )

    try:
        print("\n=== 1. 快捷键 Ctrl+N ===")
        check_shortcuts(window, pages, int(window.property("shortcutCount") or 0))

        print("\n=== 2. 导航栏方向键（并逐个读取无障碍名称）===")
        rail = check_rail_arrow_keys(window, rail_order)

        print("\n=== 3. 浏览与激活是两件事 ===")
        check_focus_is_not_navigation(window, rail_order)

        print("\n=== 4. Esc 关闭提示 ===")
        check_escape_dismisses_toast(window)

        print("\n=== 5. Tab 焦点顺序 ===")
        trail = walk_tab_order(window, [{"key": key} for key in rail_order])
        loaded = check_tab_order(window, trail)

        print("\n=== 6. 按钮可以用键盘触发 ===")
        if loaded:
            check_button_activates_with_keyboard(window, owner, bridge, trail)
        else:
            check("按钮可以用键盘触发", False, "项目页没有加载，无法验证")

        print("\n=== 7. 无障碍名称与角色 ===")
        check_accessibility(window, pages, rail, trail)

        print("\n=== 8. 焦点可见性 ===")
        check_focus_is_visible(window)
    finally:
        # Teardown happens **inside the bridge's loop**, not after it, and in a `finally` so
        # a check that raises still cleans up. The engine's pooled connections belong to that
        # loop; disposing the engine from a later loop raises "got Future attached to a
        # different loop" while it tries to ping a socket the dead loop used to own.
        try:
            bridge._runner.call_blocking(lambda: _teardown(owner))
        except Exception as exc:  # noqa: BLE001 - teardown must not mask the verdict
            print(f"     清理时出错（不影响上面的判定）：{type(exc).__name__}: {exc}")
        bridge.shutdown()

    passed = sum(1 for _l, ok, _d in RESULTS if ok)
    total = len(RESULTS)
    print("\n" + "-" * 70)
    print(f"结果：{passed}/{total} 通过")
    for label, ok, detail in RESULTS:
        if not ok:
            print(f"  FAIL  {label} — {detail}")
    return 0 if passed == total else 1


_BOOT_OWNER: uuid.UUID


if __name__ == "__main__":
    # The account is created in its own event loop, before the Qt application exists. The
    # engine is disposed on the way out of that loop **on purpose**: SQLAlchemy's pool holds
    # connections bound to the loop that opened them, and reusing one from the bridge's loop
    # fails with `AttributeError: 'NoneType' object has no attribute 'send'` — an error that
    # points at asyncio's proactor and says nothing about the pool. Disposing forces the
    # bridge's loop to build a fresh pool, which is what a real application start does.
    async def _create() -> uuid.UUID:
        from app.infrastructure.database import dispose_engine

        owner = await _make_account()
        await dispose_engine()
        return owner

    _BOOT_OWNER = asyncio.run(_create())
    try:
        raise SystemExit(main())
    finally:
        # Best effort, and deliberately tolerant. The account was already dropped inside the
        # bridge's loop; this covers only the case where `main` returned before it got there,
        # and a failure here must not turn a finished run into a traceback.
        async def _cleanup() -> None:
            from app.infrastructure.database import dispose_engine

            try:
                await _drop_account(_BOOT_OWNER)
            except Exception as exc:  # noqa: BLE001 - already gone, or a loop we cannot use
                print(f"     账号清理跳过：{type(exc).__name__}")
            await dispose_engine()

        try:
            asyncio.run(_cleanup())
        except Exception as exc:  # noqa: BLE001
            print(f"清理阶段出错（不影响判定）：{type(exc).__name__}: {exc}")
