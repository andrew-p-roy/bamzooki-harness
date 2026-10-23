"""Probe: keep the engine focused while typing reaches this console's input().

Run in its own console (stdin must be a console):
  python relay_probe.py --log out.txt [--no-typist]

A low-level keyboard hook swallows keys while the engine window is in front and writes them into
this console's input buffer (WriteConsoleInputW). A reader thread calls input() like the arena's
Keyboard. The typist thread fakes typing with SendInput once the engine is running, so the probe
runs unattended; with --no-typist you type yourself.

Findings: all three typed lines (capitals, symbols, Backspace) reached input() exactly; the engine
stayed in front and the game ran at normal speed (10 s sim in 14.8 s wall vs 14.5 s untyped).
Untested: a physical keyboard, Ctrl+V, Ctrl+C, Alt+Tab (swallowed with the other Alt keys).
"""

from __future__ import annotations

import argparse
import ctypes
import queue
import subprocess
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from bamzooki.runner import Engine, Job, _win, contests  # noqa: E402

u32, k32 = ctypes.windll.user32, ctypes.windll.kernel32
LRESULT = ctypes.c_ssize_t
HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
u32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
u32.SetWindowsHookExW.restype = wintypes.HHOOK
u32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
u32.CallNextHookEx.restype = LRESULT
u32.GetForegroundWindow.restype = wintypes.HWND
k32.GetStdHandle.restype = wintypes.HANDLE

WH_KEYBOARD_LL, WM_KEYDOWN, WM_SYSKEYDOWN = 13, 0x100, 0x104
VK_SHIFT, VK_CONTROL, VK_MENU, VK_CAPITAL, VK_LWIN, VK_RWIN = 0x10, 0x11, 0x12, 0x14, 0x5B, 0x5C
MODS = {0xA0: VK_SHIFT, 0xA1: VK_SHIFT, 0xA2: VK_CONTROL, 0xA3: VK_CONTROL, 0xA4: VK_MENU, 0xA5: VK_MENU,
        VK_SHIFT: VK_SHIFT, VK_CONTROL: VK_CONTROL, VK_MENU: VK_MENU}


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class KEY_EVENT_RECORD(ctypes.Structure):
    _fields_ = [("bKeyDown", wintypes.BOOL), ("wRepeatCount", wintypes.WORD), ("wVirtualKeyCode", wintypes.WORD),
                ("wVirtualScanCode", wintypes.WORD), ("UnicodeChar", wintypes.WCHAR),
                ("dwControlKeyState", wintypes.DWORD)]


class INPUT_RECORD(ctypes.Structure):
    _fields_ = [("EventType", wintypes.WORD), ("Event", KEY_EVENT_RECORD)]


class KeyRelay:
    """While a window of `pid` is in front: swallow keys, write them into our console's input."""

    def __init__(self):
        self.pid = 0
        self.hin = k32.GetStdHandle(-10)  # STD_INPUT_HANDLE
        mode = wintypes.DWORD()
        if not k32.GetConsoleMode(self.hin, ctypes.byref(mode)):
            raise SystemExit("stdin is not a console")
        self.held: set[int] = set()
        self.relayed = 0
        self.out: queue.Queue = queue.Queue()
        self._proc = HOOKPROC(self._hook)  # keep a reference
        threading.Thread(target=self._writer, daemon=True).start()
        threading.Thread(target=self._loop, daemon=True).start()

    def _engine_in_front(self) -> bool:
        owner = wintypes.DWORD()
        u32.GetWindowThreadProcessId(u32.GetForegroundWindow(), ctypes.byref(owner))
        return bool(self.pid) and owner.value == self.pid

    def _hook(self, code, wparam, lparam):
        if code == 0 and self._engine_in_front():
            k = ctypes.cast(lparam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
            vk, down = k.vkCode, wparam in (WM_KEYDOWN, WM_SYSKEYDOWN)
            if vk not in (VK_LWIN, VK_RWIN):
                if vk in MODS:
                    (self.held.add if down else self.held.discard)(MODS[vk])
                self.out.put((vk, k.scanCode, down, frozenset(self.held)))
                return 1  # swallowed: the engine never sees it
        return u32.CallNextHookEx(None, code, wparam, lparam)

    def _loop(self):
        if not u32.SetWindowsHookExW(WH_KEYBOARD_LL, self._proc, None, 0):
            raise OSError(f"SetWindowsHookEx failed: {ctypes.GetLastError()}")
        msg = wintypes.MSG()
        while u32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            pass

    def _writer(self):
        state = (ctypes.c_ubyte * 256)()
        buf = ctypes.create_unicode_buffer(8)
        layout = u32.GetKeyboardLayout(0)
        while True:
            vk, scan, down, held = self.out.get()
            if vk in (VK_SHIFT, VK_CONTROL, VK_MENU) or vk in MODS:
                continue
            ctypes.memset(state, 0, 256)
            for m in held:
                state[m] = 0x80
            caps = u32.GetKeyState(VK_CAPITAL) & 1
            state[VK_CAPITAL] = caps
            ch = "\0"
            if down and VK_CONTROL not in held and VK_MENU not in held:
                n = u32.ToUnicodeEx(vk, scan, state, buf, 8, 0x4, layout)  # 0x4: keep dead-key state
                if n == 1:
                    ch = buf.value[0]
            ctl = (0x10 if VK_SHIFT in held else 0) | (0x8 if VK_CONTROL in held else 0) | (0x80 if caps else 0)
            rec = INPUT_RECORD(1, KEY_EVENT_RECORD(down, 1, vk, scan, ch, ctl))
            written = wintypes.DWORD()
            k32.WriteConsoleInputW(self.hin, ctypes.byref(rec), 1, ctypes.byref(written))
            self.relayed += written.value


# --- the fake typist (SendInput) ---------------------------------------------------------

class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]


def tap(vk: int, shift: bool = False) -> None:
    seq = ([(VK_SHIFT, 0)] if shift else []) + [(vk, 0), (vk, 2)] + ([(VK_SHIFT, 2)] if shift else [])
    for v, flags in seq:
        inp = INPUT(type=1)
        inp.ki = KEYBDINPUT(v, u32.MapVirtualKeyW(v, 0), flags, 0, 0)
        u32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


def type_text(text: str, gap: float = 0.05) -> None:
    for ch in text:
        if ch == "\n":
            tap(0x0D)
        elif ch == "\b":
            tap(0x08)
        else:
            code = u32.VkKeyScanW(ord(ch))
            tap(code & 0xFF, bool(code & 0x100))
        time.sleep(gap)


TYPED = ["Hello Engine, are you still RUNNING?\n", "/stop typoo\b\b\b\b\bok\n", "Mixed CASE 123 !@# (done)\n"]
EXPECTED = ["Hello Engine, are you still RUNNING?", "/stop ok", "Mixed CASE 123 !@# (done)"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", required=True)
    ap.add_argument("--no-typist", action="store_true")
    ap.add_argument("--duration", type=float, default=8)
    a = ap.parse_args()
    log = open(a.log, "w", encoding="utf-8")

    def say(s):
        print(s, flush=True)
        log.write(s + "\n")
        log.flush()

    relay = KeyRelay()
    got: list[tuple[float, str]] = []
    t0 = time.perf_counter()

    def reader():
        while True:
            try:
                line = input()
                got.append((time.perf_counter() - t0, line))
            except EOFError:
                return

    threading.Thread(target=reader, daemon=True).start()

    # Learn the engine's pid by wrapping Popen (the runner calls subprocess.Popen).
    real_popen = subprocess.Popen

    class CapturePopen(real_popen):
        def __init__(self, *args, **kw):
            super().__init__(*args, **kw)
            relay.pid = self.pid

    subprocess.Popen = CapturePopen

    fg = {"engine": 0, "total": 0, "typing": False, "typed_at": None}

    def watch_and_type():
        while not relay.pid:
            time.sleep(0.05)
        while True:  # wait for the engine's main loop (the harness title) and focus
            wins = _win.windows_of(relay.pid)
            if any(t.startswith("BAMZOOKi harness") for _, t in wins) and relay._engine_in_front():
                break
            time.sleep(0.05)
        fg["typed_at"] = time.perf_counter() - t0
        fg["typing"] = True
        if not a.no_typist:
            for line in TYPED:
                type_text(line)
                time.sleep(0.3)
        fg["typing"] = False

    def sample_fg():
        while True:
            if relay.pid and fg["typed_at"] is not None:
                fg["total"] += 1
                fg["engine"] += relay._engine_in_front()
            time.sleep(0.1)

    threading.Thread(target=watch_and_type, daemon=True).start()
    threading.Thread(target=sample_fg, daemon=True).start()

    engine = Engine()
    engine.prepare()
    ex = engine.root / "Teams" / "examples" / "Creatures"
    green, red = (sorted((ex / n).glob("*.zook"))[-1] for n in ("Ant", "Spider"))
    job = Job(contests.find("Zook Sumo", engine.install), green, red, seed=1, duration=a.duration)
    r = engine.run(job, name="relay-probe", focus="idle")
    subprocess.Popen = real_popen
    time.sleep(0.5)

    say(f"result: {r.status} winner={r.winner} sim={r.sim_time:.1f}s wall={r.wall_time:.1f}s")
    say(f"typing started at {fg['typed_at']}s; key records written to console: {relay.relayed}")
    say(f"engine in front for {fg['engine']}/{fg['total']} samples after typing started")
    for t, line in got:
        say(f"input() at {t:6.2f}s: {line!r}")
    if not a.no_typist:
        lines = [line for _, line in got]
        say("MATCH" if lines == EXPECTED else f"MISMATCH: expected {EXPECTED}")
    log.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
