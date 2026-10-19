"""M0 headless/speed experiments: run job-driven contests and summarise timing and outcome.

  python tools/spike/experiments.py NAME [--repeat N] [--show minimized]
      [--window 0|1] [--camera 0|1] [--sim 30] [--finish 0|1] [--set key=value ...]

Each run writes .cache/bamzooki/experiments/<name>-<i>/{job.dat,result.dat,*.txt}
and prints: status, sim time, wall time, sim/real ratio, finish reports, final positions.
Requires `engine.py sandbox` and `engine.py put tools/spike/lua/loader.lua loader` first.
"""

from __future__ import annotations

import argparse
import io
import sys
from contextlib import redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "src"))
sys.path.insert(0, str(HERE))

import engine  # noqa: E402
from bamzooki.codec import table  # noqa: E402
from bamzooki.install import Install  # noqa: E402


def parse_value(v: str):
    try:
        return int(v)
    except ValueError:
        try:
            return float(v)
        except ValueError:
            return v


def run_once(inst: Install, name: str, i: int, a) -> dict | None:
    sandbox = engine.sandbox_dir(inst)
    out = inst.config.cache_dir / "experiments" / f"{name}-{i}"
    out.mkdir(parents=True, exist_ok=True)
    for f in out.iterdir():
        f.unlink()
    job = {
        "contest": a.contest,
        "green": (sandbox / a.green).as_posix(),
        "red": (sandbox / a.red).as_posix(),
        "out": (out / "result.dat").as_posix(),
        "log": (out / "driver_log.txt").as_posix(),
        "boot_log": (out / "boot_log.txt").as_posix(),
        "max_sim_time": a.sim,
        "stop_on_finish": a.finish,
        "go_time": 4,
        "window": a.window,
        "camera": a.camera,
        "update_interval": 0.1,
        "seed": a.seed,
        "config": {k: parse_value(v) for k, v in (s.split("=", 1) for s in a.set)},
    }
    (out / "job.dat").write_text(table.dumps(job), encoding="latin-1")
    args = ["-init", "loader", "-videocfg", "aiharness.vcfg",
            "-script", (HERE / "lua" / "harness_boot.lua").as_posix(), "-job", (out / "job.dat").as_posix()] + a.engine_arg
    buf = io.StringIO()
    with redirect_stdout(buf):
        engine.cmd_run(inst, args, a.timeout, trace=(out / "trace.txt") if a.trace else None, show=a.show,
                       keep=a.keepalive)
    lines = buf.getvalue().splitlines()
    status_line = next((l for l in lines if l.startswith(("exit", "TIMEOUT"))), "?")
    status_line += "; " + next((l for l in lines if l.startswith("visible windows")), "")
    res_path = out / "result.dat"
    if not res_path.exists():
        print(f"[{name}-{i}] {status_line}; NO RESULT (see {out})")
        return None
    r = table.loads(res_path.read_text("latin-1"))
    ratio = r["sim_time"] / r["real_time"] if r.get("real_time") else 0
    finishes = [f'{rep["name"]}@{rep["sim_time"]:.2f}' for rep in r.get("reports", {}).values()]
    pos = "; ".join(f'({p["x"]:.3f},{p["y"]:.3f},{p["z"]:.3f})' for p in r.get("positions", {}).values())
    print(f"[{name}-{i}] {status_line}; {r['status']}: sim {r['sim_time']:.2f}s in {r['real_time']:.2f}s "
          f"(x{ratio:.2f}), updates {r['updates']}, reports {finishes}, positions {pos}")
    return r


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("name")
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--contest", default="Zook Sumo")
    ap.add_argument("--green", default="Teams/examples/Creatures/Ant/2.1.zook")
    ap.add_argument("--red", default="Teams/examples/Creatures/Spider/2.1.zook")
    ap.add_argument("--sim", type=float, default=30)
    ap.add_argument("--finish", type=int, default=0)
    ap.add_argument("--window", type=int, default=1)
    ap.add_argument("--camera", type=int, default=1)
    ap.add_argument("--show", choices=["normal", "minimized", "desktop"], default="normal")
    ap.add_argument("--timeout", type=float, default=150)
    ap.add_argument("--engine-arg", action="append", default=[], help="extra Bonsai.exe argument")
    ap.add_argument("--trace", action="store_true", help="capture OutputDebugString (may slow the engine)")
    ap.add_argument("--keepalive", choices=["none", "activate", "pump", "foreground"], default="none")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--set", action="append", default=[], help="config override key=value (repeatable)")
    a = ap.parse_args()
    inst = Install.discover()
    for i in range(a.repeat):
        run_once(inst, a.name, i, a)
    return 0


if __name__ == "__main__":
    sys.exit(main())
