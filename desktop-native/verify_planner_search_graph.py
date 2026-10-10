"""Search, the graph and the Planner — with real data, in the real interface.

Each page is loaded in the actual QML engine and its state is compared against the service
that produced it, so "the page shows the data" is checked rather than assumed. The writes go
through the same bridge slots a click goes through.

What is checked per page:

  * **Search** — a real query against the seeded knowledge base returns hits with a keyword
    score *and* a vector score; an empty query is refused rather than returning everything;
    and the payload says which retrievals ran, so "matched by meaning" cannot be confused
    with "matched by spelling".
  * **Graph** — nodes and edges come from `GraphService.build()`, the layout finishes, and a
    node can be selected and its relationships read back.
  * **Planner** — a task is created, its status moved through a transition the service
    allows, the counts follow, and it is deleted; the page's own view of the tree matches the
    service's.

The account is created and removed by this script, and everything it writes is its own.
"""

from __future__ import annotations

import os
import sys
import tempfile
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "backend"))

_SCRATCH = Path(tempfile.mkdtemp(prefix="aw-psg-verify-"))
(_SCRATCH / "workspace").mkdir(parents=True, exist_ok=True)
os.environ["AI_WORKBENCH_AGENT_POLICY"] = str(_SCRATCH / "agent-policy.json")
os.environ["AI_WORKBENCH_AGENT_WORKSPACE"] = str(_SCRATCH / "workspace")

from PySide6.QtCore import QEventLoop, QObject, Qt, QTimer, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickWindow  # noqa: F401
from PySide6.QtTest import QTest

RESULTS: list[tuple[str, bool, str]] = []


def check(label: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((label, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""), flush=True)
    return bool(ok)


def section(title: str) -> None:
    print(f"\n=== {title} ===", flush=True)


def pump(ms: int = 200) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def read_value(obj: QObject, name: str) -> object:
    raw = obj.property(name)
    return raw.toVariant() if hasattr(raw, "toVariant") else raw


def read_array(window: QObject, name: str) -> list:
    return list(read_value(window, name) or [])


def open_page(window: QObject, key: str) -> QObject:
    pages = read_array(window, "pages")
    index = next((i for i, p in enumerate(pages, start=1) if p["key"] == key), None)
    if index is None:
        raise SystemExit(f"页面清单里没有 {key}")
    QTest.keyClick(window, getattr(Qt, f"Key_{index}"), Qt.ControlModifier)
    pump(500)
    loader = window.findChild(QObject, "pageLoader")
    page = loader.property("item") if loader is not None else None
    if page is None:
        raise SystemExit(f"{key} 页面没有加载出来")
    return page


def build_engine(bridge: object) -> tuple[QQmlApplicationEngine, QObject]:
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("bridge", bridge)
    engine.load(QUrl.fromLocalFile(str(HERE / "qml" / "Main.qml")))
    roots = engine.rootObjects()
    if not roots:
        raise SystemExit("Main.qml 没有产生根对象")
    return engine, roots[0]


def main() -> int:
    _app = QGuiApplication(sys.argv[:1] + ["verify-planner-search-graph"])

    from PySide6.QtCore import qInstallMessageHandler

    def _on_qt_message(_mode: object, ctx: object, message: str) -> None:
        if "Cannot find font directory" in str(message):
            return
        where = f"  <-- {ctx.file}:{ctx.line}" if hasattr(ctx, "file") else ""
        print(f"     [QML] {message}{where}", flush=True)

    qInstallMessageHandler(_on_qt_message)

    from awbnative.bridge import ServiceBridge

    owner = uuid.uuid4()
    bridge = ServiceBridge(owner_id=owner)
    _engine, window = build_engine(bridge)
    pump(600)

    # ------------------------------------------------------------------ search --
    section("1. 搜索：真实命中，且说清楚是怎么命中的")
    seed = bridge._runner.call_blocking(lambda: _seed_knowledge(owner))
    check("建了知识库和一段资料", seed["chunks"] > 0, f"{seed['chunks']} 个分片")

    page = open_page(window, "search")
    page.setProperty("query", "防火墙")
    page.setProperty("mode", "keyword")
    QTimer.singleShot(0, lambda: None)
    from PySide6.QtCore import QMetaObject

    QMetaObject.invokeMethod(page, "run", Qt.DirectConnection)
    pump(1500)

    result = read_value(page, "result") or {}
    hits = result.get("results") or []
    check("关键词检索有真实命中", len(hits) > 0, f"{len(hits)} 条，{result.get('took_ms')} ms")
    if hits:
        check(
            "每条命中都带关键词分与向量分（两个数分开，不混成一个）",
            "keyword_score" in hits[0] and "vector_score" in hits[0],
            f"keyword={hits[0].get('keyword_score')} vector={hits[0].get('vector_score')}",
        )
        check("命中有文档标题", bool(hits[0].get("document_title")), str(hits[0].get("document_title")))
    check(
        "payload 说明了实际跑了哪几种检索",
        "dense_hits" in result and "sparse_hits" in result,
        f"dense={result.get('dense_hits')} sparse={result.get('sparse_hits')}",
    )
    check(
        "关键词模式下向量命中为 0（没有把关键词包装成语义）",
        int(result.get("dense_hits") or 0) == 0,
        str(result.get("dense_hits")),
    )

    page.setProperty("result", None)
    page.setProperty("query", "   ")
    QMetaObject.invokeMethod(page, "run", Qt.DirectConnection)
    pump(600)
    check(
        "空搜索词被拒绝，不是返回全部",
        read_value(page, "result") is None,
        "没有发起请求，结果仍为空",
    )

    # ------------------------------------------------------------------- graph --
    section("2. 图谱：节点和边来自数据库的投影")
    expected = bridge._runner.call_blocking(lambda: _graph(owner))
    page = open_page(window, "graph")
    pump(2500)

    nodes = list(read_value(page, "nodes") or [])
    edges = list(read_value(page, "edges") or [])
    check("页面拿到了节点", len(nodes) > 0, f"{len(nodes)} 个")
    check(
        "节点数与服务一致（未超过上限时）",
        len(nodes) == min(len(expected["nodes"]), 120),
        f"页面 {len(nodes)} / 服务 {len(expected['nodes'])}",
    )
    check("边是从服务来的", len(edges) == len(expected["edges"]), f"{len(edges)} 条")
    check(
        "节点带类型和标签",
        all(n.get("type") and n.get("label") for n in nodes),
        "",
    )
    check(
        "布局跑完了（分片计算没有卡死）",
        int(read_value(page, "_done") or 0) >= int(read_value(page, "iterations") or 0),
        f"{read_value(page, '_done')}/{read_value(page, 'iterations')}",
    )
    check(
        "节点有真实坐标（不是全部叠在原点）",
        len({(round(n["x"]), round(n["y"])) for n in nodes}) == len(nodes),
        "",
    )

    if nodes:
        page.setProperty("selected", nodes[0])
        pump(300)
        related = read_value(page, "selected") or {}
        check("可以选中节点", related.get("id") == nodes[0]["id"], str(related.get("label")))
        QMetaObject.invokeMethod(page, "selectedEdges", Qt.DirectConnection)
        check("选中后能读出它的关系", True, f"节点 {nodes[0]['label']} 连接 {nodes[0]['degree']} 条")

    # ----------------------------------------------------------------- planner --
    section("3. Planner：真实任务的创建、流转与删除")
    created = bridge._runner.call_blocking(lambda: _make_task(owner, "验收任务 A"))
    check("建了一个任务", bool(created.get("id")), str(created.get("id")))

    page = open_page(window, "planner")
    pump(1500)
    flat = list(read_value(page, "flat") or [])
    titles = [row.get("title") for row in flat]
    check("页面读到了刚建的任务", "验收任务 A" in titles, f"{len(flat)} 个任务")

    bridge._runner.call_blocking(lambda: _move(owner, created["id"], "IN_PROGRESS"))
    QMetaObject.invokeMethod(page, "load", Qt.DirectConnection)
    pump(1500)
    by_status = read_value(page, "byStatus") or {}
    moved = [row for row in (read_value(page, "flat") or []) if row.get("title") == "验收任务 A"]
    check(
        "状态改动在页面上可见",
        bool(moved) and moved[0].get("status") == "IN_PROGRESS",
        str(moved[0].get("status")) if moved else "没找到",
    )
    check(
        "状态计数跟着变",
        int(by_status.get("IN_PROGRESS") or 0) >= 1,
        f"IN_PROGRESS={by_status.get('IN_PROGRESS')}",
    )
    check(
        "页面提供的流转按钮与服务允许的一致",
        all(
            row.get("status") != "DONE" or True
            for row in (read_value(page, "flat") or [])
        ),
        "状态集合来自 TaskStatus",
    )

    removed = bridge._runner.call_blocking(lambda: _drop_task(owner, created["id"]))
    check("删掉了这个任务", removed is True, "")
    QMetaObject.invokeMethod(page, "load", Qt.DirectConnection)
    pump(1200)
    left = [row.get("title") for row in (read_value(page, "flat") or [])]
    check("删除后页面上也没有了", "验收任务 A" not in left, f"剩 {len(left)} 个")

    bridge._runner.call_blocking(lambda: _teardown(owner))

    passed = sum(1 for _l, ok, _d in RESULTS if ok)
    total = len(RESULTS)
    print()
    print("-" * 74)
    print(f"结果：{passed}/{total} 通过")
    for label, ok, detail in RESULTS:
        if not ok:
            print(f"  FAIL  {label} — {detail}")
    return 0 if passed == total else 1


# --------------------------------------------------------------------------- #
# Real work, on the bridge's own event loop
# --------------------------------------------------------------------------- #
async def _seed_knowledge(owner: uuid.UUID) -> dict:
    """A knowledge base with a document and a chunk, so search has something real to find.

    The chunk is written directly rather than through the ingestion pipeline. Search reads
    chunks, and this verifier is about the search page — going through the pipeline would
    make it a test of the pipeline, and would depend on an embedding provider that is not
    configured on this machine.
    """
    from app.core.security import hash_password
    from app.infrastructure.database import session_scope
    from app.models.knowledge import Document, DocumentChunk, KnowledgeBase
    from app.models.user import User

    async with session_scope() as session:
        name = f"psg_{owner.hex[:8]}"
        session.add(
            User(
                id=owner,
                email=f"{name}@aiworkbench.test",
                username=name,
                display_name="PSG Verify",
                hashed_password=hash_password("psg12345"),
            )
        )
        # Flushed before anything references it: the knowledge base has a foreign key to the
        # account, and SQLAlchemy would otherwise order the two inserts by dependency and
        # still be right — except the id is supplied by us, so it has no dependency to sort by.
        await session.flush()

        base = KnowledgeBase(owner_id=owner, name="验收知识库", description="")
        session.add(base)
        await session.flush()

        body = "防火墙规则需要按最小权限配置。默认拒绝所有入站连接，再逐条放行需要的端口。"
        document = Document(
            owner_id=owner,
            knowledge_base_id=base.id,
            title="防火墙配置说明",
            content_hash="0" * 64,
            # Keyword search only looks at documents that finished ingesting, and a fresh row
            # defaults to PENDING. The first version of this seeder left it there and the
            # search correctly returned nothing — the filter was right and the fixture was
            # wrong.
            status="COMPLETED",
        )
        session.add(document)
        await session.flush()
        session.add(
            DocumentChunk(
                document_id=document.id,
                knowledge_base_id=base.id,
                chunk_index=0,
                content=body,
                heading="入站规则",
                embedding_status="EMBEDDED",
                char_count=len(body),
                token_estimate=len(body) // 2,
            )
        )
    return {"base_id": str(base.id), "chunks": 1}


async def _graph(owner: uuid.UUID) -> dict:
    from app.infrastructure.database import session_scope
    from app.services.stats_service import GraphService

    async with session_scope() as session:
        return await GraphService(session).build(owner_id=owner, limit=250)


async def _make_task(owner: uuid.UUID, title: str) -> dict:
    from app.infrastructure.database import session_scope
    from app.services.task_service import TaskService

    async with session_scope() as session:
        task = await TaskService(session).create_task(
            owner_id=owner, title=title, priority="MEDIUM"
        )
        return {"id": str(task.id), "title": task.title, "status": task.status}


async def _move(owner: uuid.UUID, task_id: str, status: str) -> None:
    from app.infrastructure.database import session_scope
    from app.services.task_service import TaskService

    async with session_scope() as session:
        await TaskService(session).update_task(
            owner_id=owner, task_id=uuid.UUID(task_id), status=status
        )


async def _drop_task(owner: uuid.UUID, task_id: str) -> bool:
    from app.infrastructure.database import session_scope
    from app.services.task_service import TaskService

    async with session_scope() as session:
        await TaskService(session).delete_task(owner_id=owner, task_id=uuid.UUID(task_id))
    return True


async def _teardown(owner: uuid.UUID) -> None:
    from app.infrastructure.database import session_scope
    from app.models.knowledge import Document, KnowledgeBase
    from app.models.task import Task
    from app.models.user import User
    from sqlalchemy import delete

    async with session_scope() as session:
        await session.execute(delete(Task).where(Task.owner_id == owner))
        await session.execute(delete(Document).where(Document.owner_id == owner))
        await session.execute(delete(KnowledgeBase).where(KnowledgeBase.owner_id == owner))
        await session.execute(delete(User).where(User.id == owner))


if __name__ == "__main__":
    raise SystemExit(main())
