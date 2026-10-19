"""M0 feasibility helpers: sandbox copy of the install, backups, and engine launches.

  python tools/spike/engine.py sandbox [--fresh]       copy the install into <cache>/sandbox/install
  python tools/spike/engine.py backup                  back up the save dir and BonsaiErrorLog.txt
  python tools/spike/engine.py put SRC NAME [--plain]  install SRC (Lua) as sandbox Scripts/Boot/NAME.ssx
                                                       (encrypted with the he.skx public key unless --plain)
  python tools/spike/engine.py run [--timeout S] [--trace FILE] [--log FILE] [--screenshot S]... -- ARGS...
                                                       run the sandbox Bonsai.exe with ARGS

  --trace FILE   capture the engine's Trace output (OutputDebugString, "[ munki ]" lines)
  --screenshot S grab the engine window only (the 3D view comes out blank: DirectX surface)
  --keepalive    experimental window-message nudges; not needed once stdout is not a pipe

Examples (from the repo root):
  engine.py put tools/spike/lua/loader.lua loader
  engine.py run --trace .cache/bamzooki/logs/t.txt -- -init loader -videocfg aiharness.vcfg \\
      -script C:/abs/path/tools/spike/lua/contest_spike.lua

Never touches the real install.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from bamzooki.codec import script  # noqa: E402
from bamzooki.install import ENGINE_EXE, Install  # noqa: E402


def sandbox_dir(inst: Install) -> Path:
    return inst.config.cache_dir / "sandbox" / "install"


def error_log() -> Path | None:
    appdata = os.environ.get("APPDATA")
    return Path(appdata) / "BonsaiErrorLog.txt" if appdata else None


def cmd_sandbox(inst: Install, fresh: bool) -> None:
    dst = sandbox_dir(inst)
    if dst.exists() and fresh:
        shutil.rmtree(dst)
    if not dst.exists():
        shutil.copytree(inst.root, dst)
    print(dst)


def cmd_backup(inst: Install) -> None:
    dst = inst.config.cache_dir / "backup" / time.strftime("%Y%m%d-%H%M%S")
    dst.mkdir(parents=True)
    if inst.save_dir and inst.save_dir.is_dir():
        shutil.copytree(inst.save_dir, dst / inst.save_dir.name)
    if (log := error_log()) and log.is_file():
        shutil.copy2(log, dst / log.name)
    print(dst)


def cmd_put(inst: Install, src: Path, name: str, plain: bool, ext: str) -> None:
    source = src.read_bytes()
    data = source if plain else script.encode(source, inst.script_key.public_key())
    dst = sandbox_dir(inst) / "Scripts" / "Boot" / f"{name}{ext}"
    dst.write_bytes(data)
    print(f"{dst} ({'plain' if plain else 'encrypted'}, {len(data)} bytes)")


def snapshot(paths: list[Path]) -> dict[Path, tuple[int, float]]:
    out = {}
    for root in paths:
        if root.is_file():
            out[root] = (root.stat().st_size, root.stat().st_mtime)
        elif root.is_dir():
            for p in root.rglob("*"):
                if p.is_file():
                    st = p.stat()
                    out[p] = (st.st_size, st.st_mtime)
    return out


def screenshot(png: Path, pid: int) -> None:
    """Capture only the engine's own window (Windows only). Never grabs the rest of the desktop."""
    png.parent.mkdir(parents=True, exist_ok=True)
    ps = (
        "Add-Type -AssemblyName System.Drawing;"
        "Add-Type -TypeDefinition 'using System;using System.Runtime.InteropServices;"
        "public struct R{public int L,T,Ri,B;}"
        "public static class W{[DllImport(\"user32.dll\")]public static extern bool GetWindowRect(IntPtr h,out R r);"
        "[DllImport(\"user32.dll\")]public static extern bool PrintWindow(IntPtr h,IntPtr dc,uint f);}';"
        f"$h=(Get-Process -Id {pid}).MainWindowHandle; if($h -eq 0){{exit 1}};"
        "$r=New-Object R; [W]::GetWindowRect($h,[ref]$r)|Out-Null;"
        "$bmp=New-Object System.Drawing.Bitmap ($r.Ri-$r.L),($r.B-$r.T);"
        "$g=[System.Drawing.Graphics]::FromImage($bmp); $dc=$g.GetHdc();"
        "[W]::PrintWindow($h,$dc,2)|Out-Null; $g.ReleaseHdc($dc);"
        f"$bmp.Save('{png}');"
    )
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=False)


def engine_windows(pid: int) -> list[int]:
    """Top-level window handles owned by pid (Windows only)."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    found: list[int] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(hwnd):
            found.append(hwnd)
        return True

    user32.EnumWindows(cb, 0)
    return found


def keepalive(proc: subprocess.Popen, mode: str, stop) -> None:
    """Keep the engine's main loop ticking while its window is in the background.
    activate: post WM_ACTIVATEAPP/WM_ACTIVATE once the window exists (no real focus change).
    pump:     post WM_NULL every 10 ms.
    foreground: really bring the window to the front once (takes focus from the user!)."""
    import ctypes

    user32 = ctypes.windll.user32
    WM_NULL, WM_ACTIVATE, WM_ACTIVATEAPP, WA_ACTIVE = 0x0000, 0x0006, 0x001C, 1
    activated = set()
    while not stop.is_set() and proc.poll() is None:
        for hwnd in engine_windows(proc.pid):
            if mode == "activate" and hwnd not in activated:
                user32.PostMessageW(hwnd, WM_ACTIVATEAPP, 1, 0)
                user32.PostMessageW(hwnd, WM_ACTIVATE, WA_ACTIVE, 0)
                activated.add(hwnd)
            elif mode == "foreground" and hwnd not in activated:
                VK_MENU, KEYUP = 0x12, 0x2
                user32.keybd_event(VK_MENU, 0, 0, 0)  # lets this process pass the foreground lock
                user32.SetForegroundWindow(hwnd)
                user32.keybd_event(VK_MENU, 0, KEYUP, 0)
                activated.add(hwnd)
            elif mode == "pump":
                user32.PostMessageW(hwnd, WM_NULL, 0, 0)
        stop.wait(0.01 if mode == "pump" else 0.2)


def debug_listener(pid_box: list, out: Path, stop) -> None:
    """Capture OutputDebugString text (the engine's Trace channel) like DebugView does (Windows only)."""
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.windll.kernel32
    k32.CreateFileMappingW.restype = wintypes.HANDLE
    k32.MapViewOfFile.restype = ctypes.c_void_p
    k32.CreateEventW.restype = wintypes.HANDLE
    buf_ready = k32.CreateEventW(None, False, False, "DBWIN_BUFFER_READY")
    data_ready = k32.CreateEventW(None, False, False, "DBWIN_DATA_READY")
    mapping = k32.CreateFileMappingW(wintypes.HANDLE(-1), None, 0x04, 0, 4096, "DBWIN_BUFFER")
    view = k32.MapViewOfFile(wintypes.HANDLE(mapping), 0x0004, 0, 0, 4096)
    if not (buf_ready and data_ready and view):
        print("debug listener: could not attach (another debug viewer running?)")
        return
    with open(out, "w", encoding="latin-1", newline="") as f:
        while not stop.is_set():
            k32.SetEvent(wintypes.HANDLE(buf_ready))
            if k32.WaitForSingleObject(wintypes.HANDLE(data_ready), 100) != 0:
                continue
            pid = ctypes.c_uint32.from_address(view).value
            if pid_box and pid == pid_box[0]:
                f.write(ctypes.string_at(view + 4).decode("latin-1"))
                f.flush()


def cmd_run(inst: Install, args: list[str], timeout: float, shots: list[float] = (), keep: str = "none",
            log: Path | None = None, trace: Path | None = None) -> int:
    root = sandbox_dir(inst)
    watch = [root] + ([inst.save_dir] if inst.save_dir else []) + ([error_log()] if error_log() else [])
    watch += [Path.home() / "Documents" / "My BAMZOOKi"]
    before = snapshot(watch)
    cmd = inst.config.wine_command + [str(root / ENGINE_EXE)] + args
    print("$", " ".join(cmd))
    t0 = time.time()
    # Never give the engine a pipe for stdout: it stalls its main loop after ~1 s when it does.
    sink = open(log, "wb") if log else subprocess.DEVNULL
    stop = threading.Event()
    pid_box: list[int] = []
    if trace:
        trace.parent.mkdir(parents=True, exist_ok=True)
        listener = threading.Thread(target=debug_listener, args=(pid_box, trace, stop), daemon=True)
        listener.start()
        time.sleep(0.3)
    proc = subprocess.Popen(cmd, cwd=root, stdin=subprocess.DEVNULL, stdout=sink, stderr=sink)
    pid_box.append(proc.pid)
    if keep != "none":
        threading.Thread(target=keepalive, args=(proc, keep, stop), daemon=True).start()
    for at in shots:
        try:
            proc.wait(max(0.0, at - (time.time() - t0)))
            break  # exited before the screenshot was due
        except subprocess.TimeoutExpired:
            png = inst.config.cache_dir / "screens" / f"shot-{int(t0)}-{int(at)}s.png"
            screenshot(png, proc.pid)
            print(f"screenshot: {png}")
    try:
        proc.wait(timeout=max(0.0, timeout - (time.time() - t0)))
        status = f"exit {proc.returncode}"
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        status = f"TIMEOUT after {timeout}s (killed)"
    time.sleep(0.3)
    stop.set()
    print(f"{status} in {time.time() - t0:.1f}s")
    if trace:
        listener.join(2)
        print("--- Trace / OutputDebugString (tail) ---\n" + trace.read_text("latin-1")[-4000:])
    if log:
        sink.close()
        print("--- stdout/stderr (tail) ---\n" + log.read_text("latin-1")[-3000:])
    after = snapshot(watch)
    changed = [p for p in after if before.get(p) != after[p]]
    print("--- new/changed files ---")
    for p in sorted(changed):
        print(f"  {p} ({after[p][0]} bytes)")
    elog = error_log()
    if elog in changed:
        print("--- BonsaiErrorLog.txt (tail) ---")
        print(elog.read_text("latin-1")[-3000:])
    return proc.returncode or 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sandbox"); s.add_argument("--fresh", action="store_true")
    sub.add_parser("backup")
    p = sub.add_parser("put"); p.add_argument("src", type=Path); p.add_argument("name")
    p.add_argument("--plain", action="store_true"); p.add_argument("--ext", default=".ssx")
    r = sub.add_parser("run"); r.add_argument("--timeout", type=float, default=30)
    r.add_argument("--screenshot", type=float, action="append", default=[], help="seconds after launch (repeatable)")
    r.add_argument("--trace", type=Path, help="capture OutputDebugString (Trace) to this file")
    r.add_argument("--log", type=Path, help="redirect engine stdout/stderr to this file")
    r.add_argument("--keepalive", choices=["none", "activate", "pump", "foreground"], default="none")
    r.add_argument("args", nargs=argparse.REMAINDER)
    a = ap.parse_args()

    inst = Install.discover()
    if a.cmd == "sandbox":
        cmd_sandbox(inst, a.fresh)
    elif a.cmd == "backup":
        cmd_backup(inst)
    elif a.cmd == "put":
        cmd_put(inst, a.src, a.name, a.plain, a.ext)
    elif a.cmd == "run":
        args = a.args[1:] if a.args[:1] == ["--"] else a.args
        return cmd_run(inst, args, a.timeout, sorted(a.screenshot), a.keepalive, a.log, a.trace)
    return 0


if __name__ == "__main__":
    sys.exit(main())
