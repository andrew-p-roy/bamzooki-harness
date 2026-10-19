"""Turn captured engine frames into an MP4 with ffmpeg.

The engine's HarnessCamera writes frame_<k>.hex whenever its clock crosses frame boundary k.
Each file is Camera.GetImageAsString(): "08080800" + width (4 hex) + height (4 hex) + RGB bytes
as hex. When the engine renders slower than the target fps some indices are missing; we
repeat the previous frame so the video's timing matches sim (or replay) time exactly.
Frames are decoded and streamed to ffmpeg as raw RGB (no image library needed).
"""

from __future__ import annotations

import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

FRAME_RE = re.compile(r"frame_(\d{6})\.(hex|jpg|png|bmp)$", re.I)


class VideoError(RuntimeError):
    pass


def slug(text: str) -> str:
    """Filesystem-safe name part: letters, digits, '.', '-'; everything else becomes '-'."""
    return re.sub(r"-{2,}", "-", re.sub(r"[^A-Za-z0-9.\-]+", "-", str(text))).strip("-.") or "unnamed"


def render_path(render_dir: Path, model: str, parts: list[str], when: datetime | None = None,
                suffix: str = ".mp4") -> Path:
    """<render_dir>/<model>/<YYYY-MM-DD_HH-MM-SS>_<part>_<part>....mp4 (folder created)."""
    folder = Path(render_dir) / slug(model)
    folder.mkdir(parents=True, exist_ok=True)
    stamp = (when or datetime.now()).strftime("%Y-%m-%d_%H-%M-%S")
    return folder / ("_".join([stamp] + [slug(p) for p in parts if p]) + suffix)


def find_ffmpeg(configured: str | None = None) -> str:
    exe = configured or shutil.which("ffmpeg")
    if not exe:
        raise VideoError("ffmpeg not found; install it and put it on PATH")
    return exe


def frame_files(frames_dir: Path) -> dict[int, Path]:
    return {int(m.group(1)): p for p in frames_dir.iterdir() if (m := FRAME_RE.search(p.name))}


def decode_hex_frame(text: str) -> tuple[int, int, bytes]:
    """(width, height, rgb24 bytes) from an engine image string."""
    # Header bytes: 08 08 08 00 (bits per channel + pad), width (2 bytes), height (2 bytes).
    if not text.startswith("08080800"):
        raise VideoError(f"unexpected image header {text[:16]!r}")
    w, h = int(text[8:12], 16), int(text[12:16], 16)
    body = text[16:16 + w * h * 6]
    if len(body) != w * h * 6:
        raise VideoError(f"truncated image ({len(body)} of {w * h * 6} hex chars)")
    return w, h, bytes.fromhex(body)


def sequence(frames: dict[int, Path]) -> list[Path]:
    """Frame path per output frame from the first to the last index, repeating across gaps."""
    out, last = [], None
    for k in range(min(frames), max(frames) + 1):
        last = frames.get(k, last)
        out.append(last)
    return out


def frames_to_mp4(frames_dir: Path, out: Path, fps: float, ffmpeg: str | None = None, crf: int = 20,
                  keep_frames: bool = False) -> Path:
    frames = frame_files(frames_dir)
    if not frames:
        raise VideoError(f"no frames in {frames_dir}")
    seq = sequence(frames)
    w, h, _ = decode_hex_frame(seq[0].read_text("ascii"))
    cmd = [find_ffmpeg(ffmpeg), "-y", "-loglevel", "error",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-r", str(fps), "-i", "-",
           "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2", "-c:v", "libx264", "-pix_fmt", "yuv420p",
           "-crf", str(crf), "-movflags", "+faststart", str(out)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    cache: tuple[Path, bytes] | None = None
    try:
        for p in seq:
            if cache is None or cache[0] != p:
                fw, fh, rgb = decode_hex_frame(p.read_text("ascii"))
                if (fw, fh) != (w, h):
                    raise VideoError(f"{p.name}: size {fw}x{fh} differs from {w}x{h}")
                cache = (p, rgb)
            proc.stdin.write(cache[1])
        proc.stdin.close()
    except BrokenPipeError:
        pass
    err = proc.stderr.read().decode(errors="replace")
    if proc.wait() != 0:
        raise VideoError(f"ffmpeg failed: {err.strip()[-500:]}")
    if not keep_frames:
        for p in frames.values():
            p.unlink()
    return out
