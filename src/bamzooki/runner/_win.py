"""Windows helpers for driving the engine window (ctypes; no-ops elsewhere).

The engine's main loop only advances while its window is in the foreground, so the
runner gives it focus at launch and can take it back when the user is idle.
"""

from __future__ import annotations

import sys

IS_WINDOWS = sys.platform == "win32"

if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    _user32 = ctypes.windll.user32
    _kernel32 = ctypes.windll.kernel32

    class _LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


def windows_of(pid: int) -> list[tuple[int, str]]:
    """Visible top-level windows of a process as (hwnd, title)."""
    if not IS_WINDOWS:
        return []
    found: list[tuple[int, str]] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        owner = wintypes.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and _user32.IsWindowVisible(hwnd):
            buf = ctypes.create_unicode_buffer(256)
            _user32.GetWindowTextW(hwnd, buf, 256)
            found.append((hwnd, buf.value))
        return True

    _user32.EnumWindows(cb, 0)
    return found


def foreground_window() -> int:
    return _user32.GetForegroundWindow() if IS_WINDOWS else 0


def focus(hwnd: int) -> None:
    """Bring a window to the front without synthesising keystrokes.

    (Never tap Alt to pass the foreground lock: it puts the engine window into
    system-menu mode, a modal loop that freezes the engine.)"""
    if not IS_WINDOWS:
        return
    me = _kernel32.GetCurrentThreadId()
    fg_thread = _user32.GetWindowThreadProcessId(_user32.GetForegroundWindow(), None)
    _user32.AttachThreadInput(me, fg_thread, True)
    _user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    _user32.SetForegroundWindow(hwnd)
    _user32.AttachThreadInput(me, fg_thread, False)


def user_idle_seconds() -> float:
    """Seconds since the user's last keyboard/mouse input."""
    if not IS_WINDOWS:
        return 0.0
    info = _LASTINPUTINFO(cbSize=ctypes.sizeof(_LASTINPUTINFO))
    _user32.GetLastInputInfo(ctypes.byref(info))
    return (_kernel32.GetTickCount() - info.dwTime) / 1000.0
