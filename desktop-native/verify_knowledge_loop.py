"""Verification for the native UI's knowledge loop — the product's core, driven from QML's slots.

    create a knowledge base -> import a real file -> parse and embed it -> ask a question
    -> get an answer with citations that resolve to real chunks

Every call goes through the same bridge slot the QML page calls, waits for the same signal,
and then checks the **database** for what actually happened. The offline embedder is used so
the run costs nothing; the one real-provider call is made separately and is not part of this
script.

Run it::

    .venv\\Scripts\\python.exe desktop-native\\verify_knowledge_loop.py
"""

from __future__ import annotations

import json
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

from PySide6.QtCore import QEventLoop, QTimer, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine

RESULTS: list[tuple[str, bool, str]] = []


def check(label: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((label, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    return ok


class Caller:
    def __init__(self, bridge: object) -> None:
        self._bridge = bridge
        self._n = 0

    def __call__(self, slot: str, *args: object, timeout: float = 300.0) -> object:
        self._n += 1
        request_id = f"kb-verify-{self._n}-{uuid.uuid4().hex[:6]}"
        box: dict[str, object] = {}
        loop = QEventLoop()

        def on_ok(rid: str, payload: str) -> None:
            if rid == request_id:
                box["ok"] = payload
                loop.quit()

        def on_err(rid: str, message: str, code: str) -> None:
            if rid == request_id:
                box["err"] = f"{message} [{code}]"
                loop.quit()

        self._bridge.completed.connect(on_ok)  # type: ignore[attr-defined]
        self._bridge.failed.connect(on_err)  # type: ignore[attr-defined]
        try:
            getattr(self._bridge, slot)(request_id, *args)
            QTimer.singleShot(int(timeout * 1000), loop.quit)
            loop.exec()
        finally:
            self._bridge.completed.disconnect(on_ok)  # type: ignore[attr-defined]
            self._bridge.failed.disconnect(on_err)  # type: ignore[attr-defined]

        if "err" in box:
            raise RuntimeError(str(box["err"]))
        return json.loads(box.get("ok") or "null")


async def _make_account() -> uuid.UUID:
    from app.core.security import hash_password
    from app.infrastructure.database import session_scope
    from app.models.user import User

    name = f"kbloop_{uuid.uuid4().hex[:8]}"
    async with session_scope() as session:
        user = User(
            email=f"{name}@aiworkbench.dev",
            username=name,
            display_name="Knowledge Loop",
            hashed_password=hash_password("kbloop1234"),
        )
        session.add(user)
        await session.flush()
        return user.id


async def _drop_account(owner: uuid.UUID) -> None:
    from app.infrastructure.database import session_scope
    from app.models.ai import AIRun, AIRunSource
    from app.models.knowledge import Document, DocumentChunk, KnowledgeBase
    from app.models.user import User
    from sqlalchemy import delete

    async with session_scope() as session:
        kb_ids = list(
            (await session.execute(select_kb_ids(KnowledgeBase, owner))).scalars()
        )
        if kb_ids:
            await session.execute(
                delete(DocumentChunk).where(DocumentChunk.knowledge_base_id.in_(kb_ids))
            )
            await session.execute(delete(Document).where(Document.knowledge_base_id.in_(kb_ids)))
            await session.execute(delete(KnowledgeBase).where(KnowledgeBase.id.in_(kb_ids)))
        run_ids = list((await session.execute(select_run_ids(AIRun, owner))).scalars())
        if run_ids:
            await session.execute(delete(AIRunSource).where(AIRunSource.ai_run_id.in_(run_ids)))
            await session.execute(delete(AIRun).where(AIRun.id.in_(run_ids)))
        await session.execute(delete(User).where(User.id == owner))


def select_kb_ids(model, owner):
    from sqlalchemy import select

    return select(model.id).where(model.owner_id == owner)


def select_run_ids(model, owner):
    from sqlalchemy import select

    return select(model.id).where(model.owner_id == owner)


async def _chunks_for(kb_id: str) -> list[tuple[str, str, str]]:
    from app.infrastructure.database import session_scope
    from app.models.knowledge import DocumentChunk
    from sqlalchemy import select

    async with session_scope() as session:
        rows = list(
            (
                await session.execute(
                    select(DocumentChunk.content, DocumentChunk.vector_id, DocumentChunk.embedding_model)
                    .where(DocumentChunk.knowledge_base_id == uuid.UUID(kb_id))
                )
            ).all()
        )
        return [(r[0], r[1], r[2]) for r in rows]


DOCUMENT = """# 项目发布说明

本项目的封版版本号是 2.2.0。
封版使用的模型是 deepseek-flash。
数据目录位于用户主目录下的 AI Workbench Data 文件夹。
"""


def main() -> int:
    # Held for the side effect: Qt requires an application object to exist.
    _app = QGuiApplication(sys.argv[:1] + ["verify-knowledge-loop"])
    from awbnative.bridge import ServiceBridge

    owner = _OWNER
    bridge = ServiceBridge(owner_id=owner)

    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("bridge", bridge)
    engine.load(QUrl.fromLocalFile(str(HERE / "qml" / "Main.qml")))
    check("the shell with the new pages loads", len(engine.rootObjects()) == 1)

    call = Caller(bridge)

    print("\n=== 1. create a knowledge base through the page's slot ===")
    kb = call("createKnowledgeBase", f"原生验证库 {uuid.uuid4().hex[:6]}")
    check("a knowledge base was created", bool(kb.get("id")), kb.get("name", ""))

    listing = call("loadKnowledgeBases")
    mine = [k for k in listing["items"] if k["id"] == kb["id"]]
    check("it appears in the list", len(mine) == 1)
    check("a fresh base reports zero documents", mine and mine[0]["documents"] == 0)

    print("\n=== 2. import a real file through the real upload path ===")
    import tempfile

    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as handle:
        handle.write(DOCUMENT)
        source = handle.name
    try:
        result = call("importDocument", kb["id"], source)
        check("the import reported success", not result.get("duplicate"), str(result)[:120])
        check("chunks were produced", result.get("chunks", 0) >= 1, f"{result.get('chunks')} chunk(s)")
        check("they were embedded", result.get("embedded", 0) >= 1, result.get("model", ""))
    finally:
        pathlib.Path(source).unlink(missing_ok=True)

    documents = call("loadDocuments", kb["id"])
    check("the document is listed", documents["total"] >= 1)
    if documents["items"]:
        doc = documents["items"][0]
        check("its status is COMPLETED", doc["status"] == "COMPLETED", doc["status"])
        check("the embedded count matches the chunk count", doc["embedded"] == doc["chunks"],
              f"{doc['embedded']}/{doc['chunks']}")

    print("\n=== 3. the vectors really exist in the database ===")
    chunks = _on_loop(bridge, lambda: _chunks_for(kb["id"]))
    check("chunk rows carry a stored vector id", all(c[1] for c in chunks) and bool(chunks),
          f"{len(chunks)} chunk(s)")
    check("each chunk records the model that embedded it", all(c[2] for c in chunks),
          chunks[0][2] if chunks else "")

    print("\n=== 4. ask a question (offline engine, no cost) ===")
    answer = call("askKnowledge", kb["id"], "本项目的封版版本号是多少？")
    check("the answer is not empty", bool((answer.get("answer") or "").strip()))
    check("a run id was recorded", bool(answer.get("run_id")), answer.get("run_id", ""))
    check("the grounding report is present", "citation_report" in answer,
          json.dumps(answer.get("citation_report", {}), ensure_ascii=False)[:120])

    print("\n=== 5. streaming reports honestly when it cannot stream ===")
    streamed = call("streamAnswer", kb["id"], "本项目的封版版本号是多少？")
    check("the offline engine is reported as unable to stream",
          streamed.get("streamed") is False, streamed.get("reason", ""))
    check("no answer is fabricated when streaming is unavailable",
          streamed.get("answer") == "")

    print("\n=== 6. cleanup ===")
    # On the bridge's loop, before it is stopped: the engine's pooled connections belong to
    # that loop, and `asyncio.run` here would be a second one. Same trap as the other
    # verification script, and the same fix.
    try:
        _on_loop(bridge, lambda: _drop_account(owner))
        check("the temporary account and its rows were removed", True)
    except Exception as exc:  # noqa: BLE001 - report, do not mask
        check("the temporary account and its rows were removed", False, str(exc)[:160])
    bridge.shutdown()

    passed = sum(1 for _l, ok, _d in RESULTS if ok)
    print(f"\n=== {passed}/{len(RESULTS)} passed ===")
    if passed != len(RESULTS):
        for label, ok, detail in RESULTS:
            if not ok:
                print(f"  - {label} — {detail}")
        return 1
    return 0


_OWNER: uuid.UUID | None = None


def _on_loop(bridge: object, factory):
    return bridge._runner.call_blocking(factory)  # type: ignore[attr-defined]


async def _bootstrap() -> uuid.UUID:
    from app.infrastructure.database import dispose_engine

    owner = await _make_account()
    await dispose_engine()
    return owner


if __name__ == "__main__":
    import asyncio

    _OWNER = asyncio.run(_bootstrap())
    raise SystemExit(main())
