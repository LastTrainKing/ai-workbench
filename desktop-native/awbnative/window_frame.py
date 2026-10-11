"""The window's frame, drawn in the application's colours instead of Windows'.

The window shipped with the default light title bar: a white band across the top of a dark
application, which is what the screenshot showed. Qt draws no frame of its own here — the
frame is the operating system's, and the way to change it is to ask DWM.

**Why not a custom title bar.** A frameless window plus hand-drawn buttons means
reimplementing dragging, the snap layouts, double-click-to-maximise, the system menu,
per-monitor DPI changes and the resize borders. All of that already works and would have to
keep working. `DwmSetWindowAttribute` changes the colours and nothing else, so every one of
those behaviours is untouched — and the minimise, maximise, restore and close buttons stay
the real ones.

Windows 11 (this machine is build 26300) honours the caption, text and border colours
directly, which is what makes the frame match the rail rather than merely turning dark.
Every call is best-effort: on a build that does not know an attribute, DWM returns an error
and the window keeps a usable frame.
"""

from __future__ import annotations

import ctypes
import sys
from typing import Any

#: Windows 11. `19` is the same flag on Windows 10 1809-1909, before the number changed.
DWMWA_USE_IMMERSIVE_DARK_MODE = 20
DWMWA_USE_IMMERSIVE_DARK_MODE_LEGACY = 19
DWMWA_BORDER_COLOR = 34
DWMWA_CAPTION_COLOR = 35
DWMWA_TEXT_COLOR = 36

#: `DWMWA_COLOR_NONE` — "let Windows decide".
COLOR_DEFAULT = 0xFFFFFFFE

#: The frame, from `qml/theme.js`. `bgElevated` is the rail, which is what the top edge of
#: the window is: the logo sits at the very top-left of the rail.
CAPTION = "#0a0e17"
TEXT = "#e8eef8"
BORDER = "#1b2432"


def _colorref(value: str) -> int:
    """`#rrggbb` as a Windows COLORREF, which is `0x00bbggrr` — the bytes are reversed."""
    raw = value.lstrip("#")
    red, green, blue = (int(raw[i : i + 2], 16) for i in (0, 2, 4))
    return (blue << 16) | (green << 8) | red


def _dwm() -> Any | None:
    """`dwmapi` with its prototypes declared.

    **Declared, not assumed.** With no `argtypes`, ctypes converts a Python int to a C
    `int` — 32 bits — and an HWND is a 64-bit handle. The window handle was truncated on
    every call, DWM answered `E_INVALIDARG`, and the frame read back as `None`: a failure
    of the measuring code that looked exactly like a failure of the window.
    """
    if sys.platform != "win32":
        return None
    try:
        dwm = ctypes.windll.dwmapi
    except (AttributeError, OSError):  # pragma: no cover - only on a broken install
        return None
    handle = ctypes.c_void_p
    for name in ("DwmSetWindowAttribute", "DwmGetWindowAttribute"):
        function = getattr(dwm, name, None)
        if function is None:  # pragma: no cover - older Windows
            return None
        function.argtypes = [handle, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint]
        function.restype = ctypes.c_long
    return dwm


def _set(dwm: Any, hwnd: int, attribute: int, value: int) -> bool:
    payload = ctypes.c_int(value)
    result = dwm.DwmSetWindowAttribute(
        ctypes.c_void_p(hwnd), attribute, ctypes.byref(payload), ctypes.sizeof(payload)
    )
    return result == 0


def _get(dwm: Any, hwnd: int, attribute: int) -> int | None:
    """`DwmGetWindowAttribute(HWND, DWORD, PVOID, DWORD)` — **four** arguments.

    An earlier version passed a fifth out-parameter that the function does not have. On
    x64 that is not harmless: the call returned `E_INVALIDARG` for every attribute, so the
    frame read back as `None` and the check reported a failure that was the measuring
    code's, not the window's.
    """
    payload = ctypes.c_int(0)
    result = dwm.DwmGetWindowAttribute(
        ctypes.c_void_p(hwnd), attribute, ctypes.byref(payload), ctypes.sizeof(payload)
    )
    return payload.value if result == 0 else None


def apply_dark_frame(window: object, *, caption: str = CAPTION, text: str = TEXT,
                     border: str = BORDER) -> dict[str, object]:
    """Recolour this window's system frame. Returns what was accepted, for the report."""
    dwm = _dwm()
    if dwm is None:
        return {"applied": False, "reason": "not Windows"}
    try:
        hwnd = int(window.winId())  # type: ignore[attr-defined]
    except (AttributeError, TypeError, ValueError) as exc:
        return {"applied": False, "reason": f"{type(exc).__name__}: {exc}"}
    if not hwnd:
        return {"applied": False, "reason": "no window handle"}

    accepted: dict[str, object] = {"applied": False, "hwnd": hwnd}
    # Dark mode first: it is what makes Windows draw light glyphs in the caption buttons.
    # The attribute number moved from 19 to 20, and a build that does not know one returns
    # a failure for it rather than for the other.
    for attribute in (DWMWA_USE_IMMERSIVE_DARK_MODE, DWMWA_USE_IMMERSIVE_DARK_MODE_LEGACY):
        if _set(dwm, hwnd, attribute, 1):
            accepted["dark_mode_attribute"] = attribute
            accepted["applied"] = True
            break

    for name, attribute, colour in (
        ("caption", DWMWA_CAPTION_COLOR, caption),
        ("text", DWMWA_TEXT_COLOR, text),
        ("border", DWMWA_BORDER_COLOR, border),
    ):
        accepted[name] = _set(dwm, hwnd, attribute, _colorref(colour))
    return accepted


def read_frame(window: object) -> dict[str, object]:
    """What the frame is actually set to — the only way to check the calls above landed."""
    dwm = _dwm()
    if dwm is None:
        return {"platform": sys.platform}
    try:
        hwnd = int(window.winId())  # type: ignore[attr-defined]
    except (AttributeError, TypeError, ValueError):
        return {"error": "no window handle"}
    dark = _get(dwm, hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE)
    if dark is None:
        dark = _get(dwm, hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE_LEGACY)
    return {
        "dark_mode": dark,
        "caption": _get(dwm, hwnd, DWMWA_CAPTION_COLOR),
        "text": _get(dwm, hwnd, DWMWA_TEXT_COLOR),
        "border": _get(dwm, hwnd, DWMWA_BORDER_COLOR),
        "expected": {"caption": _colorref(CAPTION), "text": _colorref(TEXT),
                     "border": _colorref(BORDER)},
    }
