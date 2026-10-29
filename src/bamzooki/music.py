"""The arena's music (live under each qualifying and knockout game, in the highlights reel, and
through the design phases): the tracks in Music/, their loop points, and which one a game gets.

    Music/tracks.toml      one [tracks.<name>] table per track: file, loop points, tempo, tags;
                           [contests]: each contest's own track
    pick_track(tracks, challenge, contest)   the challenge's own `music = "<name>"`, else the
                                    contest's track in [contests], else the first track tagged
                                    with the contest, else the default track
    design_songs(tracks)   the design phases' songs (tagged "design"), in the order listed
    loop_wav(track, out)   the part that repeats, as a WAV
    analyse(mp3)           tempo, first beat, fade start and a loop of whole bars before the fade
                           (python tools/music.py analyse Music/<file>.mp3 prints a table to paste)

Tracks are original clips made with Google's Lyria 3 (through OpenRouter), so they carry no
copyright claims. Everything here is plain Python over audio decoded by ffmpeg: no model calls.
"""

from __future__ import annotations

import math
import subprocess
import tomllib
from array import array
from dataclasses import dataclass, field
from pathlib import Path

from .config import REPO_ROOT

MUSIC_DIR = REPO_ROOT / "Music"
TRACKS_FILE = MUSIC_DIR / "tracks.toml"
RATE = 8000  # Hz, mono: plenty for tempo and level
HOP = 80  # samples per envelope frame: 10 ms


@dataclass
class Track:
    name: str
    file: Path
    loop_start: float = 0.0  # seconds: the part that repeats under a game
    loop_end: float | None = None  # None = the end of the file
    bpm: float | None = None
    tags: list[str] = field(default_factory=list)  # contests, moods: for choosing a track by challenge
    default: bool = False
    volume: float = 1.0  # gain under the games
    contests: list[str] = field(default_factory=list)  # the contests it's chosen for ([contests])


def load_tracks(path: Path = TRACKS_FILE) -> dict[str, Track]:
    """The tracks listed in tracks.toml whose files exist (empty if there's no list)."""
    try:
        raw = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    chosen: dict[str, list[str]] = {}
    for contest, name in (raw.get("contests") or {}).items():
        chosen.setdefault(str(name), []).append(str(contest).lower())
    out = {}
    for name, t in (raw.get("tracks") or {}).items():
        file = Path(path).parent / str(t.get("file", f"{name}.mp3"))
        if file.is_file():
            out[name] = Track(name, file, float(t.get("loop_start", 0.0)),
                              float(t["loop_end"]) if t.get("loop_end") is not None else None,
                              t.get("bpm"), [str(x).lower() for x in t.get("tags", [])], bool(t.get("default")),
                              float(t.get("volume", 1.0)), chosen.get(name, []))
    return out


def pick_track(tracks: dict[str, Track], challenge=None, contest: str = "") -> Track | None:
    """The music for a challenge's game: the rules' own `music = "<track>"` (or "none" for silence),
    else the contest's own track ([contests] in tracks.toml), else the first track tagged with the
    game's contest (e.g. "zook sumo"), else the default track, else any track."""
    if not tracks:
        return None
    wanted = str(getattr(challenge, "music", "") or "").strip()
    if wanted.lower() == "none":
        return None
    if wanted in tracks:
        return tracks[wanted]
    contest = contest.lower()
    if contest:
        for listed in ("contests", "tags"):
            for t in tracks.values():
                if contest in getattr(t, listed):
                    return t
    return next((t for t in tracks.values() if t.default), next(iter(tracks.values())))


def design_songs(tracks: dict[str, Track]) -> list[Track]:
    """The songs played one after another through the design phases: the tracks tagged "design"."""
    return [t for t in tracks.values() if "design" in t.tags]


def loop_wav(track: Track, out: Path, ffmpeg: str | None = None) -> Path | None:
    """The track's loop (loop_start to loop_end) as a 48 kHz stereo WAV at `out`, or None."""
    from .runner.video import VideoError, find_ffmpeg

    end = f":end={track.loop_end}" if track.loop_end else ""
    try:
        res = subprocess.run([ffmpeg or find_ffmpeg(), "-y", "-loglevel", "error", "-i", str(Path(track.file).resolve()),
                              "-af", f"atrim=start={track.loop_start}{end},asetpts=PTS-STARTPTS", "-ar", "48000",
                              "-ac", "2", str(out)], capture_output=True)
    except (VideoError, OSError):
        return None
    return out if res.returncode == 0 and Path(out).exists() else None


# --- analysis -------------------------------------------------------------------------------

def _decode(path: Path) -> array:
    from .runner.video import find_ffmpeg

    raw = subprocess.run([find_ffmpeg(), "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(RATE), "-f", "s16le",
                          "-"], capture_output=True, check=True).stdout
    pcm = array("h")
    pcm.frombytes(raw[: len(raw) // 2 * 2])
    return pcm


def _envelope(pcm: array) -> list[float]:
    """RMS level per 10 ms frame."""
    out = []
    for i in range(0, len(pcm) - HOP + 1, HOP):
        out.append(math.sqrt(sum(x * x for x in pcm[i:i + HOP]) / HOP))
    return out


def _db(x: float) -> float:
    return 20 * math.log10(max(x, 1e-3) / 32768)


def tempo(env: list[float], lo_bpm: float = 70, hi_bpm: float = 180) -> tuple[float, float]:
    """(bpm, first beat in seconds) from the onset strength's autocorrelation; tempos are folded
    into 90-180 BPM where the beat is felt."""
    logs = [_db(x) for x in env]
    onset = [max(0.0, b - a) for a, b in zip(logs, logs[1:])]
    fps = RATE / HOP
    lags = range(int(fps * 60 / hi_bpm), int(fps * 60 / lo_bpm) + 1)
    scores = {lag: sum(onset[i] * onset[i + lag] for i in range(len(onset) - lag)) / (len(onset) - lag) for lag in lags}
    best = max(scores, key=scores.get)
    # refine between frames (parabola through the peak and its neighbours)
    a, b, c = scores.get(best - 1, 0), scores[best], scores.get(best + 1, 0)
    shift = 0.5 * (a - c) / (a - 2 * b + c) if (a - 2 * b + c) else 0.0
    period = best + max(-0.5, min(0.5, shift))
    bpm = 60 * fps / period
    while bpm < 90:
        bpm *= 2
    while bpm > 180:
        bpm /= 2
    period = 60 * fps / bpm
    # the beat phase: where onsets line up best with the period, within the first beat
    phase = max(range(int(period) + 1),
                key=lambda p: sum(onset[int(p + k * period)] for k in range(int((len(onset) - p) / period))))
    return round(bpm, 2), round((phase + 1) / fps, 3)  # +1: onset[i] is the change into frame i + 1


def fade_start(env: list[float], window: float = 0.5, drop_db: float = 2.0, min_windows: int = 2) -> float | None:
    """Where the level falls away for good near the end: the start of the trailing run of windows
    more than `drop_db` below the track's median, if it lasts at least `min_windows` (one quiet
    window is just a gap between notes); None if it doesn't fade."""
    fps = RATE / HOP
    n = int(window * fps)
    levels = [_db(math.sqrt(sum(x * x for x in env[i:i + n]) / n)) for i in range(0, len(env) - n + 1, n)]
    if len(levels) < 4:
        return None
    median = sorted(levels)[len(levels) // 2]
    i = len(levels)
    while i > 0 and levels[i - 1] < median - drop_db:
        i -= 1
    return None if len(levels) - i < min_windows else round(i * window, 2)


def analyse(path: Path, beats_per_bar: int = 4, phrase: int = 4) -> dict:
    """Tempo, first beat, fade start, and the loop: from the earliest beat, whole phrases (`phrase`
    bars; whole bars if not even one phrase fits) that end before the fade or the end of the file,
    so the melody restarts where a phrase would."""
    env = _envelope(_decode(path))
    duration = len(env) * HOP / RATE
    bpm, first = tempo(env)
    beat = 60 / bpm
    first -= beat * math.floor((first + 0.06) / beat)  # back to the earliest beat (onsets at 0 s aren't seen)
    start = round(max(0.0, first), 3)
    fade = fade_start(env)
    usable = (fade if fade is not None else duration) - start
    bar = beats_per_bar * beat
    bars = int(usable / bar + 1e-6)
    if bars >= phrase:
        bars -= bars % phrase
    return {"duration": round(duration, 2), "bpm": bpm, "first_beat": start, "fade_start": fade,
            "bars": bars, "loop_start": start, "loop_end": round(start + bars * bar, 3)}


def toml_entry(name: str, file: str, info: dict, tags: list[str] | None = None, default: bool = False) -> str:
    """A [tracks.<name>] table for tracks.toml."""
    lines = [f"[tracks.{name}]", f'file = "{file}"', f"bpm = {info['bpm']}",
             f"loop_start = {info['loop_start']}", f"loop_end = {info['loop_end']}  "
             f"# {info['bars']} bars; fade starts at {info['fade_start']}, file is {info['duration']} s",
             f"tags = {list(tags or [])!r}".replace("'", '"')]
    if default:
        lines.append("default = true")
    return "\n".join(lines) + "\n"
