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


def hex_to_png(hex_file: Path, png: Path, ffmpeg: str | None = None) -> Path:
    """Convert one engine image string (Camera.GetImageAsString) to PNG."""
    w, h, rgb = decode_hex_frame(Path(hex_file).read_text("ascii"))
    cmd = [find_ffmpeg(ffmpeg), "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
           "-s", f"{w}x{h}", "-i", "-", "-frames:v", "1", "-update", "1", str(png)]
    res = subprocess.run(cmd, input=rgb, capture_output=True)
    if res.returncode != 0:
        raise VideoError(f"ffmpeg failed: {res.stderr.decode(errors='replace')[-300:]}")
    return png


def video_duration(mp4: Path, ffmpeg: str | None = None) -> float:
    probe = Path(find_ffmpeg(ffmpeg)).with_name("ffprobe" + Path(find_ffmpeg(ffmpeg)).suffix)
    res = subprocess.run([str(probe if probe.exists() else "ffprobe"), "-v", "error", "-show_entries",
                          "format=duration", "-of", "csv=p=0", str(mp4)], capture_output=True, text=True)
    return float(res.stdout.strip() or 0)


def contact_sheet(mp4: Path, times: list[float], out: Path, width: int = 1024, ffmpeg: str | None = None) -> Path:
    """One PNG with up to four stills from a video in a 2x2 grid (left-right, top-bottom)."""
    exe = find_ffmpeg(ffmpeg)
    dur = video_duration(mp4, ffmpeg)
    times = [min(max(0.0, t), max(0.0, dur - 0.05)) for t in times][:4]
    while len(times) < 4:
        times.append(times[-1] if times else 0.0)
    cmd = [exe, "-y", "-loglevel", "error"]
    for t in times:
        cmd += ["-ss", f"{t:.2f}", "-i", str(mp4)]
    half = width // 2
    scale = "".join(f"[{i}:v]trim=end_frame=1,scale={half}:-2[v{i}];" for i in range(4))
    cmd += ["-filter_complex", scale + "[v0][v1][v2][v3]xstack=inputs=4:layout=0_0|w0_0|0_h0|w0_h0[out]",
            "-map", "[out]", "-frames:v", "1", "-update", "1", str(out)]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise VideoError(f"ffmpeg failed: {res.stderr.strip()[-300:]}")
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
