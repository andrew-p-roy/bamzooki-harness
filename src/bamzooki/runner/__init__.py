"""Run contests in the real Bonsai engine (milestone M2).

    from bamzooki.runner import run_contest
    result = run_contest("Zook Sumo", green="path/to/a.zook", red="path/to/b.zook", seed=1)
    print(result.winner, result.scores)

How it works: the engine runs from a sandbox copy of the user's install (the install
itself is never modified). Setup encrypts our tiny loader into the sandbox as
Scripts/Boot/aiharness.ssx; everything else is plain-text Lua in runner/lua/. Each run
writes a job file (Bonsai text table), launches Bonsai.exe on screen, gives its window
focus (the engine only advances while focused), waits, and parses the result table.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..codec import script, table
from ..install import ENGINE_EXE, Install
from . import _win
from .contests import CONTEST_PATCHES, DEFAULT_OVERRIDES, ContestInfo, Verdict, find, judge, trajectories

LUA_DIR = Path(__file__).with_name("lua")
BOOT_NAME = "aiharness"
VIDEO_CFG = "aiharness.vcfg"
CLASSES = ["AIHarnessDriver"]  # governor classes, defined at runtime by boot.lua
# World-agent classes must exist as files under Agents/World (runtime classes are invisible
# inside worlds), so prepare() encrypts these into the sandbox.
WORLD_CLASSES = ["HarnessCamera"]
# Client area of the engine's default 900x700 window (the camera re-fits to the real area anyway).
DEFAULT_CLIENT = (884, 662)



# Views for camera="single" (and replay rendering). zook: -1 fixed target, 0 both Zooks,
# 1 green, 5 red (the engine's visual groups). offset = camera position relative to the target.
VIEWS = {
    # Both Zooks, pulled back with a wide minimum angle so the arena stays readable.
    "follow": {"zook": 0, "target": table.Vec(0, 0, 0), "offset": table.Vec(18, 20, 65), "angle": 45,
               "min_angle": 30},
    # The game's main overview camera ("camera1": whole arena from high up).
    "overview": {"zook": -1, "target": table.Vec(0, 0, 0), "offset": table.Vec(-200, 120, 200), "angle": 18,
                 "min_angle": 10},
    # Chase cams (the game's small views 3 and 4).
    "green": {"zook": 1, "target": table.Vec(0, 0, 0), "offset": table.Vec(5, 14, 40), "angle": 45, "min_angle": 10},
    "red": {"zook": 5, "target": table.Vec(0, 0, 0), "offset": table.Vec(5, 14, 40), "angle": 45, "min_angle": 10},
}


def view_spec(view: str | dict) -> dict:
    return dict(VIEWS[view]) if isinstance(view, str) else dict(view)


@dataclass
class Job:
    contest: ContestInfo
    green: Path | None = None
    red: Path | None = None
    seed: int | None = 1
    duration: float = 90.0  # sim seconds after Go before giving up
    go_time: float = 4.0  # sim second at which contestants start (the game uses 4)
    stop_on_finish: bool = True
    finish_hold: float = 3.0  # keep running this many real seconds after a finish
    overrides: dict = field(default_factory=lambda: {k: dict(v) for k, v in DEFAULT_OVERRIDES.items()})
    patches: dict | None = None  # {behaviour_id: {param: value}}; None = CONTEST_PATCHES for the contest
    record: bool = False  # write a .bvz replay
    video: bool = False  # capture frames and encode an MP4 (implies camera="single")
    video_fps: float = 25.0
    camera: str = "split"  # "split" = the game's 5-view layout, "single" = one follow camera
    view: str | dict = "follow"  # for camera="single": a VIEWS name or a view dict
    window: tuple[int, int] | None = None  # client size; None = engine default (~884x662). Capture cost scales with pixels
    position: str = "none"  # move the window: right / left / center / none (stay where it opens)
    config: dict = field(default_factory=dict)  # extra Config.Set values
    track_interval: float = 0.5


@dataclass
class Result:
    status: str  # "finished", "timeout", "engine_error", "no_result"
    contest: str
    winner: str | None
    scores: dict
    verdict: Verdict | None
    finish_time: float | None  # sim seconds after Go
    sim_time: float
    wall_time: float
    run_dir: Path
    replay: Path | None = None
    video: Path | None = None
    error: str | None = None
    raw: dict = field(default_factory=dict)

    def trajectories(self) -> dict:
        return trajectories(self.raw) if self.raw else {}


def _posix(p: Path | str) -> str:
    return Path(p).resolve().as_posix()


class Engine:
    """A sandboxed copy of the user's install plus the harness boot scripts."""

    def __init__(self, install: Install | None = None):
        self.install = install or Install.discover()
        self.cache = self.install.config.cache_dir
        self.root = self.cache / "sandbox" / "install"
        self.runs = self.cache / "runs"

    # --- setup -------------------------------------------------------------------
    def prepare(self) -> None:
        if not (self.root / ENGINE_EXE).is_file():
            self.root.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(self.install.root, self.root, dirs_exist_ok=True)
        self._install_script(LUA_DIR / "loader.lua", self.root / "Scripts" / "Boot" / f"{BOOT_NAME}.ssx")
        for name in WORLD_CLASSES:
            self._install_script(LUA_DIR / f"{name}.munki.lua", self.root / "Agents" / "World" / name / f"{name}.sax")

    def _install_script(self, src_path: Path, dst: Path) -> None:
        """Encrypt our Lua into the sandbox (only when it changed)."""
        src = src_path.read_bytes()
        stamp = dst.with_suffix(".src")
        if not dst.exists() or not stamp.exists() or stamp.read_bytes() != src:
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(script.encode(src, self.install.script_key.public_key()))
            stamp.write_bytes(src)

    def write_video_cfg(self, size: tuple[int, int] | None) -> None:
        # Windowed mode ignores the size here (the runner resizes the window itself when asked);
        # the file mainly keeps the engine out of the user's fullscreen Video.cfg.
        if not self.install.save_dir:
            return
        size = size or DEFAULT_CLIENT
        cfg = {"bit_depth": 1, "quality": 1, "resolution_x": size[0],
               "resolution_y": size[1], "shadow_quality": 1, "windowed": 1}
        (self.install.save_dir / VIDEO_CFG).write_text(table.dumps(cfg), encoding="latin-1")

    def sandbox_zook(self, rel: str) -> Path:
        return self.root / rel

    # --- running -----------------------------------------------------------------
    def run(self, job: Job, timeout: float | None = None, focus: str = "idle", name: str | None = None) -> Result:
        """Run one contest. focus: "launch" (focus once), "idle" (also re-take focus when the
        user has been idle 3 s), "always" (re-take whenever lost)."""
        self.prepare()
        self.write_video_cfg(job.window)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        run_dir = self.runs / f"{stamp}-{name or job.contest.contest_file}"
        run_dir.mkdir(parents=True, exist_ok=True)
        out = run_dir / "result.dat"
        replay = run_dir / "replay.bvz" if job.record else None
        frames_dir = run_dir / "frames"
        single = job.video or job.camera == "single"
        if job.video:
            frames_dir.mkdir(exist_ok=True)
        spec = {
            "contest_file": job.contest.contest_file,
            "green": _posix(job.green) if job.green else None,
            "red": _posix(job.red) if job.red else None,
            "seed": job.seed,
            "go_time": job.go_time,
            "max_sim_time": job.go_time + job.duration,
            "stop_on_finish": 1 if job.stop_on_finish else 0,
            "finish_hold": job.finish_hold,
            "update_interval": 0.1,
            "track_interval": job.track_interval,
            "camera": 3 if single else 1,
            "camera_def": job.contest.camera_def,
            "view": view_spec(job.view),
            "window_w": (job.window or DEFAULT_CLIENT)[0],
            "window_h": (job.window or DEFAULT_CLIENT)[1],
            "capture": 1 if job.video else 0,
            "capture_dir": _posix(frames_dir),
            "capture_fps": job.video_fps,
            "capture_start": max(0.0, job.go_time - 1.0),
            "capture_ext": "hex",
            "overrides": job.overrides or None,
            "patches": (job.patches if job.patches is not None
                        else CONTEST_PATCHES.get(job.contest.contest_file)) or None,
            "record": 1 if job.record else 0,
            "record_bmv": _posix(run_dir / "replay.bmv"),
            "record_bvz": _posix(replay) if replay else None,
            "config": job.config or None,
            "classes": {i + 1: c for i, c in enumerate(CLASSES)},
            "window_title": f"BAMZOOKi harness - {job.contest.name}",
            "out": _posix(out),
            "log": _posix(run_dir / "driver_log.txt"),
            "boot_log": _posix(run_dir / "boot_log.txt"),
        }
        (run_dir / "job.dat").write_text(table.dumps(spec), encoding="latin-1")
        args = ["-init", BOOT_NAME, "-videocfg", VIDEO_CFG,
                "-script", _posix(LUA_DIR / "boot.lua"), "-job", _posix(run_dir / "job.dat")]
        if timeout is None:
            timeout = 60 + 2.0 * (job.go_time + job.duration)
        status, error, wall = self._launch(args, timeout, focus, job.window, job.position)
        raw: dict = {}
        if out.exists():
            raw = table.loads(out.read_text("latin-1"))
            status = raw.get("status", status)
        elif status == "exited":
            status = "no_result"
        verdict = judge(raw, job.contest.players) if raw else None
        video = None
        if job.video and frames_dir.exists() and any(frames_dir.iterdir()):
            from .video import VideoError, frames_to_mp4
            try:
                video = frames_to_mp4(frames_dir, run_dir / "contest.mp4", job.video_fps)
            except VideoError as e:
                error = f"{error or ''}\nvideo: {e}".strip()
        go = raw.get("go_time", job.go_time)
        ft = raw.get("finish_time", -1)
        return Result(
            status=status, contest=job.contest.name,
            winner=verdict.winner if verdict else None,
            scores=verdict.scores if verdict else {},
            verdict=verdict,
            finish_time=(ft - go) if isinstance(ft, (int, float)) and ft >= 0 else None,
            sim_time=raw.get("sim_time", 0.0), wall_time=wall, run_dir=run_dir,
            replay=replay if replay and replay.exists() else None, video=video, error=error, raw=raw)

    # --- replays -------------------------------------------------------------------
    def render_replay(self, replay: Path, video: bool = True, fps: float = 25.0, speed: float = 2.0,
                      view: str | dict | None = None, window: tuple[int, int] | None = None,
                      position: str = "none",
                      timeout: float | None = None, focus: str = "idle") -> dict:
        """Play a .bvz/.bmv through our camera; with video=True also capture frames and encode
        replay.mp4 next to the run's other files. Playback is frame-stepped: every update (0.07 s)
        advances speed x 0.02 s of replay time (speed 2 = one 25 fps frame per update, about
        0.6x real time). Returns the player's result table plus "video" and "run_dir"."""
        self.prepare()
        self.write_video_cfg(window)
        replay = Path(replay).resolve()
        run_dir = self.runs / f"{time.strftime('%Y%m%d-%H%M%S')}-replay-{replay.stem}"
        frames_dir = run_dir / "frames"
        frames_dir.mkdir(parents=True, exist_ok=True)
        spec = {
            "profile": "player", "driver": "HarnessPlayer",
            "classes": {1: "HarnessPlayer"}, "camera": 3,
            "replay": _posix(replay), "tmp_bmv": _posix(run_dir / "replay_tmp.bmv"), "speed": speed, "read_interval": 0.07,
            "view": view_spec(view or "follow"), "window_w": (window or DEFAULT_CLIENT)[0],
            "window_h": (window or DEFAULT_CLIENT)[1],
            "capture": 1 if video else 0, "capture_dir": _posix(frames_dir), "capture_fps": fps,
            "window_title": f"BAMZOOKi harness - replay {replay.stem}",
            "out": _posix(run_dir / "result.dat"), "log": _posix(run_dir / "player_log.txt"),
            "boot_log": _posix(run_dir / "boot_log.txt"),
        }
        (run_dir / "job.dat").write_text(table.dumps(spec), encoding="latin-1")
        args = ["-init", BOOT_NAME, "-videocfg", VIDEO_CFG,
                "-script", _posix(LUA_DIR / "boot.lua"), "-job", _posix(run_dir / "job.dat")]
        status, error, wall = self._launch(args, timeout or 600, focus, window, position)
        out = run_dir / "result.dat"
        res = table.loads(out.read_text("latin-1")) if out.exists() else {"status": status}
        res.update(run_dir=run_dir, wall_time=wall, error=error, video=None)
        if video and any(frames_dir.iterdir()):
            from .video import frames_to_mp4
            res["video"] = frames_to_mp4(frames_dir, run_dir / "replay.mp4", fps)
        return res

    def play_replay(self, replay: Path, wait: bool = False) -> subprocess.Popen:
        """Open a .bvz in the game's own Motion Player (interactive UI: pause, seek, replay)."""
        self.prepare()
        self.write_video_cfg((960, 540))
        cmd = self.install.config.wine_command + [str(self.root / ENGINE_EXE), "-init", "contestfileplayer",
                                                  "-videocfg", VIDEO_CFG, "-f", str(Path(replay).resolve())]
        proc = subprocess.Popen(cmd, cwd=self.root, stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if wait:
            proc.wait()
        return proc

    def _launch(self, args: list[str], timeout: float, focus: str,
                size: tuple[int, int] | None = None,
                position: str = "none") -> tuple[str, str | None, float]:
        log = Path(os.environ.get("APPDATA", "")) / "BonsaiErrorLog.txt"
        log_mtime = log.stat().st_mtime if log.exists() else None
        cmd = self.install.config.wine_command + [str(self.root / ENGINE_EXE)] + args
        t0 = time.time()
        # stdin/stdout must not be pipes; the engine is a GUI app.
        proc = subprocess.Popen(cmd, cwd=self.root, stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        stop = threading.Event()
        state = {"error_window": None}

        def watch():
            focused_once = False
            sized = size is None and position == "none"  # default: leave the window as it opens
            while not stop.is_set() and proc.poll() is None:
                wins = _win.windows_of(proc.pid)
                if not sized:
                    # The engine ignores the .vcfg size in windowed mode; set the client area
                    # once our boot script has titled the window (SimpleFrame exists by then).
                    for hwnd, title in wins:
                        if title.startswith("BAMZOOKi harness"):
                            _win.place_window(hwnd, *(size or _win.client_size(hwnd)), position)
                            sized = True
                for hwnd, title in wins:
                    if "Error" in title:
                        state["error_window"] = title
                main = [h for h, t in wins if "Error" not in t]
                if main and _win.foreground_window() not in [h for h, _ in wins]:
                    if not focused_once or focus == "always" or (focus == "idle" and _win.user_idle_seconds() > 3):
                        _win.focus(main[0])
                        focused_once = True
                elif main:
                    focused_once = True
                if state["error_window"]:
                    proc.kill()
                    return
                stop.wait(0.25)

        threading.Thread(target=watch, daemon=True).start()
        status = "exited"
        try:
            proc.wait(timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            status = "timeout"
        stop.set()
        error = None
        if log.exists() and log.stat().st_mtime != log_mtime:
            error = log.read_text("latin-1")[-4000:]
        if state["error_window"]:
            status = "engine_error"
            error = f"[{state['error_window']}]\n{error or ''}"
        return status, error, time.time() - t0


def run_contest(contest: str, green: str | Path | None = None, red: str | Path | None = None,
                engine: Engine | None = None, **job_kw) -> Result:
    """Run one contest by name (pack name like "Zook Sumo" or a contest file like CP_ZOOK_SUMO)."""
    engine = engine or Engine()
    info = find(contest, engine.install)
    timeout = job_kw.pop("timeout", None)
    focus = job_kw.pop("focus", "idle")
    job = Job(info, Path(green) if green else None, Path(red) if red else None, **job_kw)
    return engine.run(job, timeout=timeout, focus=focus)
