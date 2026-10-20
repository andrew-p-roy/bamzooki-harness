"""Match music for the highlights reel (Music/tracks.toml).

  python tools/music.py list                         the tracks, and which one each challenge gets
  python tools/music.py analyse Music/new_track.mp3  tempo, fade and loop points: a [tracks.<name>]
                                                     entry to paste into Music/tracks.toml
  python tools/music.py loop synth_funk out.mp3 [--times 3]
                                                     a track's loop played several times, to listen
                                                     to the seam

No model calls: everything is ffmpeg and plain Python.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bamzooki import music  # noqa: E402
from bamzooki.challenge import Challenge  # noqa: E402
from bamzooki.config import REPO_ROOT  # noqa: E402
from bamzooki.runner.video import find_ffmpeg  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    an = sub.add_parser("analyse")
    an.add_argument("files", nargs="+", type=Path)
    lp = sub.add_parser("loop")
    lp.add_argument("track")
    lp.add_argument("out", type=Path)
    lp.add_argument("--times", type=int, default=3)
    a = ap.parse_args()

    if a.cmd == "list":
        tracks = music.load_tracks()
        for t in tracks.values():
            loop = f"{t.loop_start:g}-{t.loop_end:g} s" if t.loop_end else f"from {t.loop_start:g} s"
            print(f"{t.name:22s} {t.bpm or '?':>7} BPM  loop {loop:16s} {'DEFAULT ' if t.default else ''}"
                  f"tags: {', '.join(t.tags)}")
        print()
        for path in sorted((REPO_ROOT / "challenges").glob("*.toml")):
            c = Challenge.load(path)
            t = music.pick_track(tracks, c)
            print(f"{path.name:18s} -> {t.name if t else 'silence'}")
    elif a.cmd == "analyse":
        for f in a.files:
            print(music.toml_entry(f.stem, f.name, music.analyse(f)))
    else:
        t = music.load_tracks().get(a.track)
        if not t:
            raise SystemExit(f"no track {a.track!r} in {music.TRACKS_FILE}")
        end = f":end={t.loop_end}" if t.loop_end else ""
        subprocess.run([find_ffmpeg(), "-y", "-loglevel", "error", "-i", str(t.file), "-af",
                        f"atrim=start={t.loop_start}{end},asetpts=PTS-STARTPTS,aloop=loop={a.times - 1}:"
                        "size=2147483647", "-b:a", "192k", str(a.out)], check=True)
        print(a.out)


if __name__ == "__main__":
    main()
