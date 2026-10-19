"""Run every contest once (on screen) and tabulate the verdicts.

  python tools/spike/all_contests.py [--green Ant] [--red Spider] [--seed 1] [--only NAME ...]

Writes <cache>/survey/all_contests.json and prints one line per contest.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from bamzooki.runner import Engine, Job, contests  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--green", default="Ant")
    ap.add_argument("--red", default="Spider")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--duration", type=float, default=90)
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--raw", action="store_true", help="no behaviour overrides")
    ap.add_argument("--tag", default="")
    a = ap.parse_args()

    engine = Engine()
    engine.prepare()
    ex = engine.root / "Teams" / "examples" / "Creatures"
    green = sorted((ex / a.green).glob("*.zook"))[-1]
    red = sorted((ex / a.red).glob("*.zook"))[-1]
    rows = []
    for name, info in contests.catalog(engine.install).items():
        if a.only and name not in a.only and info.contest_file not in a.only:
            continue
        job = Job(info, green, red, seed=a.seed, duration=a.duration)
        if a.raw:
            job.overrides = {}
        r = engine.run(job, name=f"{info.contest_file}{a.tag}")
        v = r.verdict
        row = {
            "contest": name, "file": info.contest_file, "players": info.players, "status": r.status,
            "winner": r.winner, "scores": r.scores, "decided_by": v.decided_by if v else None,
            "finish_after_go": None if r.finish_time is None else round(r.finish_time, 2),
            "fell": v.fell if v else {}, "reports": [x.get("name") for x in r.raw.get("reports", {}).values()],
            "sim": round(r.sim_time, 1), "wall": round(r.wall_time, 1), "error": (r.error or "")[:300],
            "run_dir": str(r.run_dir),
        }
        rows.append(row)
        print(f"{name:30s} {r.status:12s} winner={str(r.winner):6s} scores={r.scores} by={row['decided_by']} "
              f"finish={row['finish_after_go']} fell={row['fell']} reports={row['reports'][:6]} "
              f"sim={row['sim']} wall={row['wall']}{'  ERROR ' + row['error'][:120] if row['error'] else ''}",
              flush=True)
    out = engine.cache / "survey" / f"all_contests{a.tag}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rows, indent=2))
    print(f"saved {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
