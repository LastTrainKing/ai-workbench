"""窗口一打开就是聊天框 —— 截图存证。

用户的验收：

    贴出…聊天框直接可见的截图

    不要"Starting"那一屏挡在前面。窗口打开 → **直接是聊天框**，能看见、能打字。

So this launches the installed application, waits for its window to appear, and captures
it **while the services are still starting** — which is the only moment that proves
anything, because after the backend is up the window is the real app anyway.

## What this proves, and what it does not

**Proves:** the window that appears before the API is healthy looks like the chat screen —
sidebar, conversation area, a live composer — and not like a progress page. The screenshot
is the evidence and it is meant to be looked at.

**Does not prove:** anything about the DOM inside the WebView2. There is no supported way
to read it from outside, and pretending otherwise would be worse than saying so. The
composer being typable is asserted at the source level by
`scripts/verify_startup_screen.py`, and the draft hand-off is covered by
`verify_installer.py` (the URL the shell navigates to carries it).

    .venv/Scripts/python.exe scripts/capture_startup_screen.py
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SHOTS = ROOT / "screenshots" / "startup"
INSTALL_DIR = Path(os.environ["LOCALAPPDATA"]) / "AI Workbench"
EXE = INSTALL_DIR / "AI Workbench.exe"
HEALTH = "http://127.0.0.1:8000/api/v1/health"

user32 = ctypes.windll.user32

# **`SetProcessDPIAware()` is required here, and getting this wrong cost two rounds.**
#
# The two APIs report in different units unless the process is DPI-aware:
#
#     ImageGrab.grab()     1920x1200   (physical, always)
#     GetWindowRect()      1309x886    (logical, 125% scaling, when NOT aware)
#
# Cropping the first with the second's coordinates is off by a factor of 1.25 and by the
# window's offset, so the "screenshot of the startup screen" is a picture of the wrong
# rectangle — and it looks plausible enough that only reading it catches the mistake.
# Declaring the process DPI-aware puts both in physical pixels.
#
# `SetForegroundWindow` matters for the same reason in the other direction: the shell
# opens behind whatever the developer already had focused, and a screenshot of an
# overlapping window proves nothing about the window.
user32.SetProcessDPIAware()

#: The shell's own window class. Tauri names it this, and matching on it is what makes
#: this reliable: `FindWindowW(None, "AI Workbench")` returns the **first** window in
#: Z-order with that title, and it picked a 170x47 helper window instead of the real
#: 1942x1222 one — so the "screenshot of the startup screen" was a picture of a tooltip.
WINDOW_CLASS = "Tauri Window"


def find_window(title: str) -> int:
    """The largest *visible* window of the shell's class with this title.

    Enumerating and choosing beats `FindWindowW` here. There is more than one window
    carrying the title, they are not in a useful order, and the one that is not visible
    is the one whose screenshot proves nothing.
    """
    candidates: list[tuple[int, int]] = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def visit(handle: int, _param: int) -> bool:
        length = user32.GetWindowTextLengthW(handle)
        if not length:
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(handle, buffer, length + 1)
        if buffer.value != title:
            return True
        if not user32.IsWindowVisible(handle):
            return True
        name = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(handle, name, 256)
        if name.value != WINDOW_CLASS:
            return True
        rect = wintypes.RECT()
        user32.GetWindowRect(handle, ctypes.byref(rect))
        candidates.append((int(handle), (rect.right - rect.left) * (rect.bottom - rect.top)))
        return True

    user32.EnumWindows(callback_type(visit), 0)
    if not candidates:
        return 0
    return max(candidates, key=lambda pair: pair[1])[0]


def window_rect(handle: int) -> tuple[int, int, int, int]:
    rect = wintypes.RECT()
    user32.GetWindowRect(wintypes.HWND(handle), ctypes.byref(rect))
    return rect.left, rect.top, rect.right, rect.bottom


def api_is_up() -> bool:
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(HEALTH, timeout=2) as response:
            return response.status == 200
    except (urllib.error.URLError, OSError):
        return False


def main() -> int:
    if not EXE.is_file():
        print(f"  没装：{EXE}")
        print("  先装一份，再跑这个。")
        return 2

    if api_is_up():
        # Stop it rather than refusing. A capture run that only works from a cold machine
        # is a capture run nobody performs twice, and this is the artefact the acceptance
        # asks for.
        print("  后端已经在跑，先把它停掉，这样截到的才是启动画面…")
        stopper = INSTALL_DIR / "scripts" / "stop-desktop.bat"
        if stopper.is_file():
            subprocess.run(["cmd", "/C", str(stopper)], capture_output=True, check=False)
        for _ in range(40):
            if not api_is_up():
                break
            time.sleep(0.5)
        if api_is_up():
            print("  停不掉，先手动关掉再跑。")
            return 2

    SHOTS.mkdir(parents=True, exist_ok=True)

    # Close the shell itself, not just the backend.
    #
    # `stop-desktop.bat` stops the *services*; the window stays open. The first version of
    # this script only ran that, then launched a second instance and found — the old
    # window, already showing the running app. The screenshot was of the wrong run
    # entirely, and it looked plausible enough that only reading it caught the mistake.
    if find_window("AI Workbench"):
        subprocess.run(
            ["taskkill", "/F", "/IM", "AI Workbench.exe", "/T"],
            capture_output=True,
            check=False,
        )
        for _ in range(40):
            if not find_window("AI Workbench"):
                break
            time.sleep(0.25)

    print("  启动安装好的应用…")
    subprocess.Popen([str(EXE)], cwd=str(INSTALL_DIR), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    handle = 0
    deadline = time.time() + 40
    while time.time() < deadline:
        handle = find_window("AI Workbench")
        if handle:
            break
        time.sleep(0.2)

    if not handle:
        print("  窗口没出现")
        return 1

    # Grab repeatedly and keep the **last frame captured while the backend was still
    # down**.
    #
    # A single grab after a fixed delay does not work, and the first version of this
    # script proved it: it waited 1.5 s and photographed the login page, because on a
    # warm start the services are ready in about seven seconds and the shell navigates
    # the moment they are. What is wanted is the frame *before* that, and the only way to
    # know which frame that is, is to ask the API each time.
    from PIL import ImageGrab  # noqa: PLC0415 - only needed when actually capturing

    user32.SetForegroundWindow(wintypes.HWND(handle))
    time.sleep(0.4)

    left, top, right, bottom = window_rect(handle)
    candidate = None
    frames_before_ready = 0
    for _ in range(30):
        if api_is_up():
            break
        image = ImageGrab.grab()
        crop = image.crop((max(0, left), max(0, top), min(image.width, right), min(image.height, bottom)))
        if crop.width > 200 and crop.height > 200:
            candidate = crop
            frames_before_ready += 1
        time.sleep(0.3)

    out = SHOTS / "S1-chat-first.png"
    if candidate is None:
        print("  没抓到「服务还没起来」那一帧 —— 启动太快了。")
        print("  想再试一次：先关掉应用，再跑这个脚本。")
        return 1
    candidate.save(out)
    print(f"  窗口 {right - left}×{bottom - top}，在服务起来之前抓了 {frames_before_ready} 帧，存的是最后一帧")
    print(f"  截图存到 {out}")

    # A second frame once it is actually up, so the pair shows the transition.
    for _ in range(180):
        if api_is_up():
            break
        time.sleep(1)
    time.sleep(2.5)
    handle = find_window("AI Workbench") or handle
    user32.SetForegroundWindow(wintypes.HWND(handle))
    time.sleep(0.6)
    image = ImageGrab.grab()
    left, top, right, bottom = window_rect(handle)
    left, top = max(0, left), max(0, top)
    right, bottom = min(image.width, right), min(image.height, bottom)
    second = SHOTS / "S2-app-ready.png"
    image.crop((left, top, right, bottom)).save(second)
    print(f"  服务起来之后的第二张：{second}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
