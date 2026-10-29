"""Run a contest in the real engine (on screen) and print the verdict.

  python tools/run_contest.py "Zook Sumo" --green Ant --red Spider [--seed 1] [--record]
  python tools/run_contest.py --list

--green/--red take a .zook path, or the name of an example Zook in the install
(Teams/examples/Creatures/<Name>).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bamzooki import music  # noqa: E402
from bamzooki.runner import MONITOR_VIEW, Engine, Job, contests  # noqa: E402


def zook_path(engine: Engine, ref: str | None) -> Path | None:
    if not ref:
        return None
    p = Path(ref)
    if p.suffix.lower() == ".zook" and p.exists():
        return p
    d = engine.root / "Teams" / "examples" / "Creatures" / ref
    hits = sorted(d.glob("*.zook")) if d.is_dir() else []
    if not hits:
        raise SystemExit(f"no Zook found for {ref!r}")
    return hits[-1]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("contest", nargs="?")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--green")
    ap.add_argument("--red")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--duration", type=float, default=90)
    ap.add_argument("--no-stop", action="store_true", help="keep running after the finish")
    ap.add_argument("--record", action="store_true", help="save a .bvz replay")
    ap.add_argument("--video", action="store_true", help="capture frames and encode an MP4 into renders/<model>/")
    ap.add_argument("--model", help="model operating this run (render folder); default $BAMZOOKI_MODEL or 'manual'")
    ap.add_argument("--camera", choices=["split", "single"], default="split")
    ap.add_argument("--view", choices=["follow", "overview", "green", "red"], default="follow",
                    help="view for --camera single / --video")
    ap.add_argument("--window", help="window client size WxH (default: the engine's own window)")
    ap.add_argument("--position", choices=["right", "left", "center", "right-half", "left-half", "none"],
                    default="none")
    ap.add_argument("--monitor", action="store_true",
                    help="one screen, as the arena's --monitor: the window on the right half, a 4:3 picture in it")
    ap.add_argument("--full-screen", action="store_true", help="windowed full screen: a borderless window over the screen, the picture 4:3")
    ap.add_argument("--raw-overrides", action="store_true", help="disable the default behaviour fixes")
    ap.add_argument("--focus", choices=["launch", "idle", "always"], default="idle")
    ap.add_argument("--music", metavar="TRACK", help="loop a track from Music/tracks.toml under the game from Go")
    ap.add_argument("--countdown", action="store_true", help='the game\'s "3, 2, 1, Go!" over the game before it starts')
    a = ap.parse_args()

    engine = Engine()
    if a.list or not a.contest:
        for name, info in contests.catalog(engine.install).items():
            print(f"{name:30s} players={info.players} colours={','.join(info.colours)}"
                  f"{'' if info.in_pack else '  (not in pack)'}")
        return 0
    engine.prepare()
    info = contests.find(a.contest, engine.install)
    job = Job(info, zook_path(engine, a.green), zook_path(engine, a.red), seed=a.seed, duration=a.duration,
              stop_on_finish=not a.no_stop, record=a.record, video=a.video, camera=a.camera, view=a.view,
              window=tuple(int(x) for x in a.window.lower().split("x")) if a.window else None,
              position=a.position, model=a.model, countdown=a.countdown,
              music=music.load_tracks()[a.music] if a.music else None)
    if a.monitor:
        job.position, job.view_fit = "right-half", MONITOR_VIEW
    job.fullscreen = a.full_screen
    if a.raw_overrides:
        job.overrides = {}
    r = engine.run(job, focus=a.focus)
    fell = r.verdict.fell if r.verdict else {}
    print(json.dumps({
        "contest": r.contest, "status": r.status, "winner": r.winner, "scores": r.scores,
        "decided_by": r.verdict.decided_by if r.verdict else None, "fell": fell,
        "finish_after_go_s": r.finish_time, "sim_s": round(r.sim_time, 2), "wall_s": round(r.wall_time, 1),
        "replay": str(r.replay) if r.replay else None, "video": str(r.video) if r.video else None,
        "run_dir": str(r.run_dir),
        "notes": r.verdict.notes if r.verdict else [], "error": r.error,
    }, indent=2))
    return 0 if r.status in ("finished", "timeout") else 1


if __name__ == "__main__":
    sys.exit(main())
