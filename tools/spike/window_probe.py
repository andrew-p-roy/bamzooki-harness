"""Probe: how fast can the engine window be right-aligned?

  python window_probe.py baseline|A|B1 [--duration 8]

baseline: today's runner (place once titled "BAMZOOKi harness", blocking SetWindowPos)
A:        move the first visible window of the pid at once, async, no resize
B1:       launch with STARTUPINFO STARTF_USEPOSITION (dwX/dwY) so it opens there; A as a fallback
A 20 ms sampler logs every window of the engine (title, rect) so the timeline shows where it was when.

Findings (Windows 11, 1920x1032 work area):
- The BonsaiSingleFrame window shows at ~0.07 s at a cascade spot, but its thread pumps no messages
  until the main loop starts (~2.2 s warm; ~13 s on a cold first launch, not disk: the sandbox is 17 MB).
  Only the owning thread can move a window, so every variant moves at the same moment (warm ~2.18 s).
- baseline already places it ~30 ms after the loop starts (SetWindowPos call 0.8 ms). A gains no time
  but never blocks and doesn't resize.
- B1 is a dead end: a hidden "Bonsai DirectInput Feeder" window is created first and takes the
  STARTUPINFO position; the frame then opens at the next cascade spot.
"""

from __future__ import annotations

import argparse
import ctypes
import subprocess
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from bamzooki.runner import ENGINE_EXE, Engine, Job, _win, contests  # noqa: E402

u32, k32 = ctypes.windll.user32, ctypes.windll.kernel32
SWP_NOSIZE, SWP_NOZORDER, SWP_NOACTIVATE, SWP_ASYNC = 0x1, 0x4, 0x10, 0x4000
DEFAULT_OUTER = (900, 700)


def work_area() -> wintypes.RECT:
    r = wintypes.RECT()
    u32.SystemParametersInfoW(0x30, 0, ctypes.byref(r), 0)
    return r


def all_windows_of(pid: int) -> list[tuple[int, str, bool, str]]:
    """Every top-level window of a process, hidden ones too: (hwnd, title, visible, class)."""
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        owner = wintypes.DWORD()
        u32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid:
            t, c = ctypes.create_unicode_buffer(256), ctypes.create_unicode_buffer(256)
            u32.GetWindowTextW(hwnd, t, 256)
            u32.GetClassNameW(hwnd, c, 256)
            found.append((hwnd, t.value, bool(u32.IsWindowVisible(hwnd)), c.value))
        return True

    u32.EnumWindows(cb, 0)
    return found


def rect(hwnd) -> tuple[int, int, int, int]:
    r = wintypes.RECT()
    u32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right - r.left, r.bottom - r.top


class PosProcess:
    """Bonsai.exe via CreateProcessW with STARTF_USEPOSITION; Popen-like (pid, poll, wait, kill)."""

    def __init__(self, cmd: list[str], cwd: Path, x: int, y: int):
        class STARTUPINFOW(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("lpReserved", wintypes.LPWSTR), ("lpDesktop", wintypes.LPWSTR),
                        ("lpTitle", wintypes.LPWSTR), ("dwX", wintypes.DWORD), ("dwY", wintypes.DWORD),
                        ("dwXSize", wintypes.DWORD), ("dwYSize", wintypes.DWORD),
                        ("dwXCountChars", wintypes.DWORD), ("dwYCountChars", wintypes.DWORD),
                        ("dwFillAttribute", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                        ("wShowWindow", wintypes.WORD), ("cbReserved2", wintypes.WORD),
                        ("lpReserved2", ctypes.c_void_p), ("hStdInput", wintypes.HANDLE),
                        ("hStdOutput", wintypes.HANDLE), ("hStdError", wintypes.HANDLE)]

        class PROCESS_INFORMATION(ctypes.Structure):
            _fields_ = [("hProcess", wintypes.HANDLE), ("hThread", wintypes.HANDLE),
                        ("dwProcessId", wintypes.DWORD), ("dwThreadId", wintypes.DWORD)]

        si = STARTUPINFOW()
        si.cb = ctypes.sizeof(si)
        si.dwX, si.dwY = x & 0xFFFFFFFF, y & 0xFFFFFFFF
        si.dwFlags = 0x4  # STARTF_USEPOSITION
        pi = PROCESS_INFORMATION()
        if not k32.CreateProcessW(None, ctypes.create_unicode_buffer(subprocess.list2cmdline(cmd)), None, None,
                                  False, 0x08000000, None, str(cwd), ctypes.byref(si), ctypes.byref(pi)):
            raise OSError(f"CreateProcess failed: {ctypes.GetLastError()}")
        k32.CloseHandle(pi.hThread)
        self._h, self.pid, self.returncode = pi.hProcess, pi.dwProcessId, None

    def poll(self):
        code = wintypes.DWORD()
        k32.GetExitCodeProcess(self._h, ctypes.byref(code))
        if code.value != 259:
            self.returncode = code.value
        return self.returncode

    def wait(self, timeout=None):
        k32.WaitForSingleObject(self._h, 0xFFFFFFFF if timeout is None else int(timeout * 1000))
        return self.poll()

    def kill(self):
        k32.TerminateProcess(self._h, 1)


class ProbeEngine(Engine):
    variant = "A"
    events: list[str] = []

    def _launch(self, args, timeout, focus, size=None, position="none", after_go=None):
        ev = self.events
        work = work_area()
        tx, ty = work.right - DEFAULT_OUTER[0], work.top
        ev.append(f"work area {work.left},{work.top}-{work.right},{work.bottom}; target x,y = {tx},{ty}")
        cmd = self.install.config.wine_command + [str(self.root / ENGINE_EXE)] + args
        t0 = time.perf_counter()
        T = lambda: f"{time.perf_counter() - t0:7.3f}s"  # noqa: E731
        if self.variant == "B1":
            proc = PosProcess(cmd, self.root, tx, ty)
        else:
            proc = subprocess.Popen(cmd, cwd=self.root, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        ev.append(f"{T()} launched pid {proc.pid} ({self.variant})")
        stop = threading.Event()
        seen: dict[int, tuple] = {}
        placed: set[int] = set()

        def sample():
            while not stop.is_set():
                for hwnd, title, vis, cls in all_windows_of(proc.pid):
                    state = (title, rect(hwnd), vis)
                    if seen.get(hwnd) != state:
                        ev.append(f"{T()} hwnd {hwnd:#x} [{cls}] {title!r} rect={state[1]}"
                                  + ("" if vis else " HIDDEN") + ("  [new]" if hwnd not in seen else ""))
                        seen[hwnd] = state
                    if not vis:
                        continue
                    if self.variant in ("A", "B1") and hwnd not in placed and "Error" not in title:
                        x, y, w, h = state[1]
                        placed.add(hwnd)
                        if (x, y) != (work.right - w, work.top):
                            c = time.perf_counter()
                            u32.SetWindowPos(hwnd, 0, work.right - w, work.top, 0, 0,
                                             SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_ASYNC)
                            ev.append(f"{T()} async move {hwnd:#x} -> {work.right - w},{work.top} "
                                      f"(call {1000 * (time.perf_counter() - c):.1f} ms)")
                        else:
                            ev.append(f"{T()} {hwnd:#x} already in place")
                    if self.variant == "baseline" and hwnd not in placed and title.startswith("BAMZOOKi harness"):
                        placed.add(hwnd)
                        c = time.perf_counter()
                        _win.place_window(hwnd, *_win.client_size(hwnd), "right")
                        ev.append(f"{T()} place_window {hwnd:#x} (call {1000 * (time.perf_counter() - c):.1f} ms)")
                stop.wait(0.02)

        def keep_focus():
            focused_once = False
            while not stop.is_set() and proc.poll() is None:
                wins = _win.windows_of(proc.pid)
                main = [h for h, t in wins if "Error" not in t]
                if main and _win.foreground_window() not in [h for h, _ in wins]:
                    if not focused_once or _win.user_idle_seconds() > 3:
                        _win.focus(main[0])
                        focused_once = True
                elif main:
                    focused_once = True
                stop.wait(0.25)

        threading.Thread(target=sample, daemon=True).start()
        threading.Thread(target=keep_focus, daemon=True).start()
        status = "exited"
        while proc.poll() is None:
            if time.perf_counter() - t0 > timeout:
                status = "timeout"
                proc.kill()
                proc.wait()
                break
            time.sleep(0.1)
        stop.set()
        ev.append(f"{T()} engine exited ({status})")
        return status, None, time.perf_counter() - t0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("variant", choices=["baseline", "A", "B1"])
    ap.add_argument("--duration", type=float, default=8)
    a = ap.parse_args()
    engine = ProbeEngine()
    engine.variant = a.variant
    engine.prepare()
    ex = engine.root / "Teams" / "examples" / "Creatures"
    green, red = (sorted((ex / n).glob("*.zook"))[-1] for n in ("Ant", "Spider"))
    job = Job(contests.find("Zook Sumo", engine.install), green, red, seed=1, duration=a.duration,
              position="right")
    r = engine.run(job, name=f"window-probe-{a.variant}")
    print("\n".join(engine.events))
    print(f"result: {r.status} winner={r.winner} sim={r.sim_time:.1f}s wall={r.wall_time:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
