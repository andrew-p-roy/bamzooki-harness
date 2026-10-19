"""Turn captured engine frames into an MP4 with ffmpeg.

The engine's HarnessCamera saves frame_<k>.jpg when the clock crosses frame boundary k;
when it renders slower than the target fps some indices are missing. We fill each gap
with the previous frame so the video's timing matches sim time exactly.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

FRAME_RE = re.compile(r"frame_(\d{6})\.jpg$", re.I)


class VideoError(RuntimeError):
    pass


def find_ffmpeg(configured: str | None = None) -> str:
    exe = configured or shutil.which("ffmpeg")
    if not exe:
        raise VideoError("ffmpeg not found; install it or set ffmpeg = \"...\" in bamzooki.toml")
    return exe


def fill_gaps(frames_dir: Path) -> int:
    """Copy the previous frame into every missing index. Returns the total frame count."""
    have = {int(m.group(1)): p for p in frames_dir.iterdir() if (m := FRAME_RE.search(p.name))}
    if not have:
        return 0
    last = None
    for k in range(min(have), max(have) + 1):
        if k in have:
            last = have[k]
        else:
            shutil.copyfile(last, frames_dir / f"frame_{k:06d}.jpg")
    return max(have) - min(have) + 1


def frames_to_mp4(frames_dir: Path, out: Path, fps: float, ffmpeg: str | None = None, crf: int = 20) -> Path:
    n = fill_gaps(frames_dir)
    if n == 0:
        raise VideoError(f"no frames in {frames_dir}")
    start = min(int(FRAME_RE.search(p.name).group(1)) for p in frames_dir.iterdir() if FRAME_RE.search(p.name))
    cmd = [find_ffmpeg(ffmpeg), "-y", "-loglevel", "error", "-framerate", str(fps), "-start_number", str(start),
           "-i", str(frames_dir / "frame_%06d.jpg"),
           # even dimensions for yuv420p; widely playable H.264
           "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", str(crf),
           "-movflags", "+faststart", str(out)]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise VideoError(f"ffmpeg failed: {res.stderr.strip()[-500:]}")
    return out
