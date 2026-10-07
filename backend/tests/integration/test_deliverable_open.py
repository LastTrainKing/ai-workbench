"""Opening a produced file: the endpoint resolves the path, and says why it failed.

The two implementations this replaces both failed silently, which is why the
assertions here are about *messages* as much as about success:

* ``<a href={downloadUrl(id)} download>`` — a navigation carries no
  `Authorization` header, so the endpoint answered 401 and the button did nothing.
* ``window.open(`file:///${path}`)`` — a WebView refuses `file://`. Nothing
  happened, and nothing said so.

So: the happy path must resolve to the file's real path, and the failure paths must
produce a message naming the reason. A silent no-op is the defect.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

from app.core.config import settings
from app.core.errors import NotFoundError, ValidationError
from app.models.delivery import BuildRun, Deliverable
from app.models.user import User
from app.core.security import hash_password
from app.infrastructure.database import session_scope
from app.services.os_open import open_path


@pytest.fixture
def scratch() -> Path:
    folder = Path(settings.storage_dir) / "open-endpoint"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


# --------------------------------------------------------------------------- #
# The service
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_a_missing_file_is_a_message_not_a_silence(scratch: Path):
    """The exact condition the old button could not report."""
    missing = scratch / "not-here-anymore.pptx"
    if missing.exists():
        missing.unlink()

    with pytest.raises(ValidationError) as caught:
        await open_path(missing)

    message = str(caught.value)
    assert "不在磁盘上" in message
    # Names the file, so the user knows which one.
    assert missing.name in message


@pytest.mark.asyncio
async def test_a_directory_is_refused_with_a_reason(scratch: Path):
    with pytest.raises(ValidationError) as caught:
        await open_path(scratch)
    assert "不是一个文件" in str(caught.value)


@pytest.mark.asyncio
async def test_the_os_is_asked_to_open_exactly_this_path(scratch: Path, monkeypatch):
    """The wiring, asserted at the OS boundary.

    `os.startfile` is replaced with a recorder rather than being allowed to run.

    **Deliberately not launched.** An earlier version of this check opened the file
    for real and looked for a new process, which is the only way to prove a window
    appeared — but it also means every test run throws a PowerPoint window onto the
    user's desktop, which is not a thing a test is allowed to do. The window is not
    verified here; it is recorded as NOT VERIFIED, and this asserts the part that
    can be asserted without taking over someone's screen: the right path reaches the
    OS entry point, once.
    """
    target = scratch / f"launch_{uuid.uuid4().hex[:6]}.pptx"
    target.write_bytes(b"x")

    calls: list[str] = []

    def recorder(path: str) -> None:
        calls.append(path)

    monkeypatch.setattr(os, "startfile", recorder, raising=False)

    result = await open_path(target)

    assert calls == [str(target)], f"expected exactly one launch of {target}, got {calls}"
    assert result["opened"] is True
    assert result["path"] == str(target)


@pytest.mark.asyncio
async def test_a_missing_file_never_reaches_the_os(scratch: Path, monkeypatch):
    """The guard runs before the launch, not after.

    Order matters: asking the OS to open a path that is gone is how a shell error
    dialog ends up on someone's screen instead of a message in the app.
    """
    calls: list[str] = []
    monkeypatch.setattr(os, "startfile", lambda p: calls.append(p), raising=False)

    with pytest.raises(ValidationError):
        await open_path(scratch / "gone.pptx")

    assert calls == [], "the OS must not be asked to open a file that is not there"


# --------------------------------------------------------------------------- #
# The endpoint
# --------------------------------------------------------------------------- #
async def _seed_deliverable(*, path: str, owner: bool = True) -> tuple[uuid.UUID, uuid.UUID]:
    async with session_scope() as session:
        suffix = uuid.uuid4().hex[:8]
        user = User(
            email=f"open_{suffix}@aiworkbench.dev",
            username=f"open_{suffix}",
            display_name="Open Test",
            hashed_password=hash_password("test1234"),
        )
        session.add(user)
        await session.flush()
        run = BuildRun(user_id=user.id, goal="[open-test] 打开一个文件", title="open-test")
        session.add(run)
        await session.flush()
        item = Deliverable(
            user_id=user.id, build_run_id=run.id, path=path, kind="pptx", title="t"
        )
        session.add(item)
        await session.flush()
        return user.id, item.id


@pytest.mark.asyncio
async def test_the_endpoint_404s_for_a_deliverable_that_is_not_yours():
    """Ownership is checked before anything is opened.

    A path-taking open endpoint reachable by id would be a way to open another
    user's file; this asserts the lookup is scoped, not just the response.
    """
    from app.services.delivery_service import DeliveryService

    _owner_id, item_id = await _seed_deliverable(path="C:/does/not/matter.pptx")

    async with session_scope() as session:
        stranger = User(
            email=f"stranger_{uuid.uuid4().hex[:8]}@aiworkbench.dev",
            username=f"stranger_{uuid.uuid4().hex[:8]}",
            display_name="Stranger",
            hashed_password=hash_password("test1234"),
        )
        session.add(stranger)
        await session.flush()
        with pytest.raises(NotFoundError):
            await DeliveryService(session).get_deliverable(
                owner_id=stranger.id, deliverable_id=item_id
            )


@pytest.mark.asyncio
async def test_the_endpoint_resolves_the_path_from_its_own_record(scratch: Path):
    """The client sends an id; the path comes from the row.

    Asserted by pointing the row at a path the client never sees and checking the
    service returns that one. If the path could come from the caller, this is where
    it would show.
    """
    from app.services.delivery_service import DeliveryService

    target = scratch / f"resolved_{uuid.uuid4().hex[:6]}.pptx"
    target.write_bytes(b"x")
    owner_id, item_id = await _seed_deliverable(path=str(target))

    async with session_scope() as session:
        item = await DeliveryService(session).get_deliverable(
            owner_id=owner_id, deliverable_id=item_id
        )
    assert Path(item.path) == target
