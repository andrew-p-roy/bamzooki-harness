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


def client_size(hwnd: int) -> tuple[int, int]:
    if not IS_WINDOWS:
        return (0, 0)
    cli = wintypes.RECT()
    _user32.GetClientRect(hwnd, ctypes.byref(cli))
    return cli.right - cli.left, cli.bottom - cli.top


def place_window(hwnd: int, width: int, height: int, position: str = "right") -> None:
    """Give a window a client (drawable) area of width x height and dock it in the primary
    monitor's work area: "right" (top-right, leaving the left free for e.g. a terminal),
    "left", "center", or "none" (keep where Windows put it)."""
    if not IS_WINDOWS:
        return
    win, cli, work = wintypes.RECT(), wintypes.RECT(), wintypes.RECT()
    _user32.GetWindowRect(hwnd, ctypes.byref(win))
    _user32.GetClientRect(hwnd, ctypes.byref(cli))
    outer_w = width + (win.right - win.left) - (cli.right - cli.left)
    outer_h = height + (win.bottom - win.top) - (cli.bottom - cli.top)
    _user32.SystemParametersInfoW(0x30, 0, ctypes.byref(work), 0)  # SPI_GETWORKAREA
    x, y, flags = win.left, win.top, 0x4 | 0x10  # SWP_NOZORDER | SWP_NOACTIVATE
    if position == "right":
        x, y = work.right - outer_w, work.top
    elif position == "left":
        x, y = work.left, work.top
    elif position == "center":
        x = work.left + (work.right - work.left - outer_w) // 2
        y = work.top + (work.bottom - work.top - outer_h) // 2
    _user32.SetWindowPos(hwnd, 0, x, y, outer_w, outer_h, flags)


def user_idle_seconds() -> float:
    """Seconds since the user's last keyboard/mouse input."""
    if not IS_WINDOWS:
        return 0.0
    info = _LASTINPUTINFO(cbSize=ctypes.sizeof(_LASTINPUTINFO))
    _user32.GetLastInputInfo(ctypes.byref(info))
    return (_kernel32.GetTickCount() - info.dwTime) / 1000.0
