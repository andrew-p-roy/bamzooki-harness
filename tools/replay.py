"""Watch or export a recorded contest (.bvz).

  python tools/replay.py play   RUN_DIR_OR_BVZ     # open in the game's Motion Player (play/pause/seek UI)
  python tools/replay.py render RUN_DIR_OR_BVZ [--view follow|overview|green|red] [--fps 25]
                                                   # render to renders/<model>/<timestamp>_..._replay-<view>.mp4

Contests are recorded with: python tools/run_contest.py ... --record
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bamzooki.runner import VIEWS, Engine  # noqa: E402


def bvz(p: str) -> Path:
    path = Path(p)
    if path.is_dir():
        path = path / "replay.bvz"
    if not path.is_file():
        raise SystemExit(f"no replay at {path}")
    return path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["play", "render"])
    ap.add_argument("replay")
    ap.add_argument("--view", choices=sorted(VIEWS), default="follow")
    ap.add_argument("--fps", type=float, default=25)
    ap.add_argument("--model", help="render folder under renders/ (default $BAMZOOKI_MODEL or 'manual')")
    a = ap.parse_args()
    engine = Engine()
    if a.action == "play":
        engine.play_replay(bvz(a.replay))
        print("Motion Player started.")
        return 0
    res = engine.render_replay(bvz(a.replay), view=a.view, fps=a.fps, model=a.model)
    print(f"{res['status']}: {res.get('frames')} frames, video {res['video']}, {res['wall_time']:.1f}s"
          f"{'  ERROR ' + res['error'] if res.get('error') else ''}")
    return 0 if res.get("video") else 1


if __name__ == "__main__":
    sys.exit(main())
