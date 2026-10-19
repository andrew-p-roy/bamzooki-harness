"""Summarise every contest in the install: arena agents and the behaviour graph.

  python tools/spike/contest_survey.py [CONTEST_FILE_STEM ...]

Reads the user's install at runtime; prints a compact summary (keep any saved
copies in the gitignored cache).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from bamzooki.codec import table  # noqa: E402
from bamzooki.install import Install  # noqa: E402

SPECIAL = {-1: "ALL", -2: "SYSTEM", -3: "IMMEDIATE", -4: "TRIGGER_ON", -5: "TRIGGER_OFF", -6: "DESTROYED"}


def fmt_events(evs: dict, behaviours: dict) -> str:
    out = []
    for e in (evs or {}).values():
        b, i = e.get("behaviour"), e.get("id")
        src = SPECIAL.get(b, f"#{b}" if b is not None else "?")
        if isinstance(b, (int, float)) and b in behaviours:
            src = f"#{b}{behaviours[b].get('name', '')}"
        out.append(f"{src}:{SPECIAL.get(i, i)}")
    return ",".join(out)


def agent_label(agents: dict, idx) -> str:
    a = agents.get(idx)
    if not a:
        return f"agent{idx}"
    desc = a.get("params", {}).get("description", "")
    return f"{idx}:{a.get('agent')}{'(' + desc + ')' if desc else ''}"


def summarise(path: Path) -> str:
    t = table.loads(path.read_text("latin-1"))
    agents, behs = t.get("agents", {}), t.get("behaviours", {})
    lines = [f"== {path.stem}"]
    for idx, a in sorted(agents.items()):
        p = a.get("params", {})
        extra = ""
        if a.get("agent") == "ZookPlaceHolder":
            extra = f" pos={tuple(round(c, 2) for c in p.get('position', ()))} group={p.get('group')!r} flags={p.get('flags')!r} movable={p.get('movable')}"
        lines.append(f"  agent {agent_label(agents, idx)}{extra}")
    for bid, b in sorted(behs.items()):
        params = {k: v for k, v in (b.get("params") or {}).items()}
        for k, v in list(params.items()):
            if isinstance(v, (int, float)) and k in ("owner", "target", "winner", "loser", "follow"):
                params[k] = agent_label(agents, v)
        lines.append(
            f"  beh #{bid} {b.get('name')} owner={agent_label(agents, b.get('owner'))}"
            f" on=[{fmt_events(b.get('triggerOnEvents'), behs)}]"
            f" off=[{fmt_events(b.get('triggerOffEvents'), behs)}]"
            f" destroy=[{fmt_events(b.get('destroyOnEvents'), behs)}]"
            f" params={params} desc={b.get('description', '')!r}"
        )
    return "\n".join(lines)


def main() -> int:
    inst = Install.discover()
    files = sorted(inst.path("Contests/Contest Pack").glob("*.contest"))
    wanted = set(sys.argv[1:])
    for f in files:
        if not wanted or f.stem in wanted:
            print(summarise(f))
    return 0


if __name__ == "__main__":
    sys.exit(main())
