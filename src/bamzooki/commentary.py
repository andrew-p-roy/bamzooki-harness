"""Live commentary for the show's games (--showtime --speak): while a knockout game plays, a fast
vision model looks at the latest captured frame with what the game reports (events, where the Zooks
are, the clock) and the Zooks' cards, and writes one short line; the announcer's voice reads it at
once (a newer line replaces one not yet spoken). A line every few seconds, and at once on an event;
the last one calls the result.

The engine side: the driver logs "report <event> at sim <t>" and, with state_log, "state <t>
<id>:<x>,<y>,<z> ..." every second; frames are the capture's frame_<k>.hex files.
"""

from __future__ import annotations

import base64
import json
import math
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

import httpx

API = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "google/gemini-3.5-flash-lite"  # 1.1 s a line with a picture (measured October 2026)

SYSTEM = (
    "You are George, a classic British sports commentator calling a live BAMZOOKi game on TV. BAMZOOKi: robot "
    "creatures called Zooks, built from body parts, legs and muscles and driven by a neural network, compete in "
    "the real BAMZOOKi engine; these Zooks were designed by AI models in a challenge. You get the latest frame "
    "from the camera, the game's own reports, where the Zooks are, the clock, and the tale of the tape.\n"
    "Speak like a real commentator: call what is happening right now, build tension, react to big moments, and "
    "in quiet spells fill in with colour from the tale of the tape (weight, legs, looks, the designer's plan). "
    "Reply with ONE spoken line of at most {words} words. No emoji, no stage directions, no quotation marks, "
    "never mention the picture, the camera or the data, and never repeat an earlier line. Trust the game's "
    "reports over the picture when they disagree (the camera can lose a Zook that falls).")

COLOUR_RE = re.compile(r"contestant (\d+) = (\w+)")
STATE_RE = re.compile(r"state (-?[\d.]+) (.*)")
REPORT_RE = re.compile(r"report (\w+) at sim (-?[\d.]+)")
GO_RE = re.compile(r"go at sim (-?[\d.]+)")
TIME_UP_RE = re.compile(r"time limit reached in real time at sim (-?[\d.]+)")


def jpeg(hex_frame: Path, width: int = 512, ffmpeg: str = "ffmpeg") -> bytes | None:
    """A captured frame (Camera.GetImageAsString text) as a small JPEG; None if unreadable."""
    from .runner.video import VideoError, decode_hex_frame

    try:
        w, h, rgb = decode_hex_frame(hex_frame.read_text("latin-1"))
        res = subprocess.run([ffmpeg, "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}",
                              "-i", "-", "-vf", f"scale={width}:-2", "-frames:v", "1", "-f", "mjpeg", "-q:v", "5", "-"],
                             input=rgb, capture_output=True, timeout=10,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, VideoError, subprocess.SubprocessError):
        return None
    return res.stdout or None


def latest_frame(frames: Path) -> Path | None:
    """The newest complete frame (the very newest may still be being written)."""
    try:
        names = sorted(p.name for p in frames.iterdir() if p.name.startswith("frame_"))
    except OSError:
        return None
    return frames / names[-2] if len(names) >= 2 else None


class GameLog:
    """What the driver log says so far: colours, Go, reports and positions (re-read as it grows)."""

    def __init__(self, path: Path):
        self.path = path
        self.colours: dict[str, str] = {}
        self.go: float | None = None
        self.reports: list[tuple[str, float]] = []
        self.states: list[tuple[float, dict[str, tuple[float, float, float]]]] = []
        self._read = 0

    def update(self) -> list[tuple[str, float]]:
        """Read new lines; returns the new reports."""
        try:
            lines = self.path.read_text("latin-1").splitlines()
        except OSError:
            return []
        new = []
        for line in lines[self._read:]:
            if m := COLOUR_RE.search(line):
                self.colours[m.group(1)] = m.group(2)
            elif m := GO_RE.search(line):
                self.go = float(m.group(1))
            elif m := REPORT_RE.search(line):
                new.append((m.group(1), float(m.group(2))))
            elif m := TIME_UP_RE.search(line):
                new.append(("time_up", float(m.group(1))))
            elif m := STATE_RE.search(line):
                pos = {}
                for part in m.group(2).split():
                    cid, _, xyz = part.partition(":")
                    try:
                        x, y, z = (float(v) for v in xyz.split(","))
                    except ValueError:
                        continue
                    pos[self.colours.get(cid, cid)] = (x, y, z)
                self.states.append((float(m.group(1)), pos))
        self._read = len(lines)
        self.reports += new
        return new

    def since_go(self, t: float) -> float:
        return t - (self.go or 0.0)

    def where(self) -> str:
        """Where the Zooks are now, against where they started (Sumo: distance from the table's centre,
        height; a drop means falling)."""
        if not self.states:
            return ""
        t, now = self.states[-1]
        start = self.states[0][1]
        out = []
        for colour, (x, y, z) in now.items():
            x0, y0, z0 = start.get(colour, (x, y, z))
            out.append(f"{colour.upper()} {math.hypot(x, z):.1f} m from the centre (started {math.hypot(x0, z0):.1f}), "
                       f"height {y - y0:+.1f} m from its start")
        if len(now) == 2:
            (xa, _, za), (xb, _, zb) = now.values()
            out.append(f"{math.hypot(xa - xb, za - zb):.1f} m apart")
        return "; ".join(out)


class Commentator:
    """Calls one game live. brief: the round, the Zooks' cards and who made them. speak(line) shows and
    says a line (live: replacing any line not yet spoken)."""

    def __init__(self, key: str, brief: str, speak: Callable[[str], None], model: str = DEFAULT_MODEL,
                 duration: float = 120.0, interval: float = 4.5, words: int = 18):
        self.key, self.brief, self.speak, self.model = key, brief, speak, model
        self.duration, self.interval, self.words = duration, interval, words
        self.said: list[str] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.errors = 0

    def start(self, run_dir: Path) -> None:
        self._thread = threading.Thread(target=self._run, args=(run_dir,), daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _run(self, run_dir: Path) -> None:
        log = GameLog(run_dir / "driver_log.txt")
        with httpx.Client(timeout=8) as http:
            while not self._stop.is_set() and log.go is None:  # the countdown: the show already hyped it
                log.update()
                self._stop.wait(0.25)
            last = 0.0
            while not self._stop.is_set():
                new = log.update()
                finished = next((name for name, _ in new if name in ("finish", "time_up")), None)
                due = time.time() - last >= self.interval
                if new or due:
                    last = time.time()
                    self._line(http, log, run_dir / "frames", new, finished)
                if finished or self.errors >= 3:
                    return
                self._stop.wait(0.25)

    def _line(self, http: httpx.Client, log: GameLog, frames: Path, new: list, finished: str | None) -> None:
        now = log.states[-1][0] if log.states else (log.reports[-1][1] if log.reports else log.go or 0.0)
        elapsed = max(0.0, log.since_go(now))
        events = ", ".join(f"{name} at {log.since_go(t):.0f} s" for name, t in log.reports[-8:]) or "none yet"
        text = (f"{self.brief}\n\nClock: {elapsed:.0f} s of {self.duration:.0f} s played. "
                f"Game reports so far: {events}. {('NEW: ' + ', '.join(n for n, _ in new) + '. ') if new else ''}"
                f"Positions: {log.where() or 'unknown'}.\n"
                + ("The game has just ENDED: call the result and the winner. " if finished == "finish" else
                   "TIME IS UP: the game ends with no winner, a draw. Call it. " if finished == "time_up" else "")
                + "Your earlier lines: " + (" | ".join(self.said[-5:]) or "(none: open the action)"))
        content: list[dict] = [{"type": "text", "text": text}]
        frame = latest_frame(frames)
        if frame and (img := jpeg(frame)):
            content.append({"type": "image_url",
                            "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(img).decode()}})
        body = {"model": self.model, "max_tokens": 80, "temperature": 0.9, "reasoning": {"effort": "minimal"},
                "messages": [{"role": "system", "content": SYSTEM.format(words=self.words)},
                             {"role": "user", "content": content}]}
        try:
            r = http.post(API, json=body, headers={"Authorization": f"Bearer {self.key}"})
            r.raise_for_status()
            line = (r.json()["choices"][0]["message"].get("content") or "").strip()
        except (httpx.HTTPError, KeyError, IndexError, ValueError, json.JSONDecodeError):
            self.errors += 1
            return
        line = " ".join(line.strip('"“”').split())
        if line and not self._stop.is_set():
            self.errors = 0
            self.said.append(line)
            self.speak(line)
