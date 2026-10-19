"""Contest catalogue (read from the user's install) and result judging."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

from ..codec import table
from ..install import Install

PACK_DIR = "ContestPacks"
CONTEST_DIR = "Contests/Contest Pack"

# Behaviour parameter fixes applied to every contest at load time (in memory only).
# NoContact with timeout 0 fires on the first update tick after Go (Sumo, Merry Go Zook),
# ending the contest instantly; a short grace period restores the intended "lost contact" rule.
DEFAULT_OVERRIDES: dict[str, dict] = {"NoContact": {"timeout": "0.5"}}

# Per-contest fixes for data bugs, by behaviour id (in memory only).
CONTEST_PATCHES: dict[str, dict[int, dict]] = {
    # The "green" ListenContact on the pit sensor has no target, so a green Zook falling in
    # is never reported (the contest then runs until the time limit). Point it at the green
    # placeholder (agent 3), mirroring the red one (#4 -> agent 2).
    "CP_WEAKEST_ZOOK": {5: {"target": 3}},
}


# Plain-language rules for models, written from each contest's behaviour graph (our wording).
# Green and red are the arena's colours; contests start 4 s after the world is created ("Go").
RULES: dict[str, str] = {
    "CP_ZOOK_SUMO": "Sumo on a raised round table. Both Zooks are steered at each other; a Zook that "
                    "wanders too far from the centre walks back. The first Zook to lose contact with the "
                    "table (pushed or fallen off) loses. Wins come from pushing power, grip and stability.",
    "CP_MERRY_GO_ZOOK": "Both Zooks start on a turntable and walk towards targets. The first Zook to lose "
                        "contact with the turntable loses. Stability and grip on a moving floor matter most.",
    "CP_WEAKEST_ZOOK": "Head-to-head on a track with a pit/sensor zone. Each Zook walks towards the far end; "
                       "the first Zook to touch the sensor (pushed or fallen in) loses. Pushing power and "
                       "staying upright matter. (Rule inferred from the contest's sensors.)",
    "CP_ZOOK_BALL_GREEN_STRIKER": "Zookball. Green is the striker: it walks to the ball and must push it into "
                                  "the goal. Red is the goalkeeper and stays frozen until the ball is first "
                                  "touched. Goal = green wins; ball out of bounds = green loses (red wins).",
    "CP_ZOOK_BALL_RED_STRIKER": "Zookball. Red is the striker: it walks to the ball and must push it into the "
                                "goal. Green is the goalkeeper and stays frozen until the ball is first "
                                "touched. Goal = red wins; ball out of bounds = red loses (green wins).",
    "CP_ZOOK_MARBLES": "Race along a course to a finish sensor; the first Zook to touch it wins. Speed and "
                       "the ability to keep a straight line matter.",
    "CP_ZOOK_SMASH": "Race through a course of waypoints (with obstacles to smash through) to a finish "
                     "sensor; the first Zook to touch it wins.",
    "CP_ZOOK_SUPER_HURDLES": "Hurdles race: walk over a course of obstacles (bars, troughs, ramps) to the "
                             "finish sensor; the first Zook to touch it wins. Leg clearance and climbing matter.",
    "CP_DODGY_ZOOK": "Obstacle race: each Zook follows a chain of targets (touching one sends it to the next) "
                     "to a final target; the first Zook to touch the final target wins.",
    "CP_ZOOK_IN_A_CHINA_SHOP_GREEN": "Single Zook (green) chases a roving target around a china shop for 25 s, "
                                     "then freezes. Score = number of items it knocks over. (Not in the "
                                     "game's contest pack; whether a high score is good is not confirmed.)",
    "CP_ZOOK_IN_A_CHINA_SHOP_RED": "As China Shop (green) but for a red Zook.",
    "CP_ZOOK_TAG": "Tag: both Zooks chase a target that jumps to a random spot whenever a Zook touches it. "
                   "Each touch scores a point; after 45 s the higher score wins. (Not in the game's pack.)",
}


@dataclass
class ContestInfo:
    name: str  # pack name ("Zook Sumo") or file stem for contests outside the pack
    contest_file: str  # e.g. "CP_ZOOK_SUMO"
    players: int  # number of Zook placeholders
    colours: list[str]  # placeholder colours in contestant order ("green"/"red")
    camera_def: dict | None = None
    in_pack: bool = True
    behaviours: list[str] = field(default_factory=list)


def _colour(flags) -> str:
    return {"1": "green", "5": "red"}.get(str(flags).strip(), "unknown")


def load_contest_file(inst: Install, contest_file: str) -> dict:
    return table.loads(inst.path(f"{CONTEST_DIR}/{contest_file}.contest").read_text("latin-1"))


def _info(inst: Install, name: str, contest_file: str, camera_def, in_pack: bool) -> ContestInfo:
    data = load_contest_file(inst, contest_file)
    agents = data.get("agents", {})
    holders = [agents[i] for i in sorted(k for k in agents if isinstance(k, int))
               if agents[i].get("agent") == "ZookPlaceHolder"]
    behs = sorted({b.get("name", "") for b in data.get("behaviours", {}).values()})
    return ContestInfo(name, contest_file, len(holders), [_colour(h.get("params", {}).get("flags")) for h in holders],
                       camera_def, in_pack, behs)


def catalog(inst: Install | None = None) -> dict[str, ContestInfo]:
    """All contests: the pack's named contests plus any .contest file the pack doesn't use."""
    inst = inst or Install.discover()
    out: dict[str, ContestInfo] = {}
    used = set()
    for pack in sorted(inst.path(PACK_DIR).glob("*.dat")):
        for name, d in table.loads(pack.read_text("latin-1")).items():
            rounds = d.get("rounds", {})
            if not rounds:
                continue
            cf = rounds[min(rounds)]["contest"]
            used.add(cf)
            out[name] = _info(inst, name, cf, d.get("camera"), True)
    for f in sorted(inst.path(CONTEST_DIR).glob("*.contest")):
        if f.stem not in used:
            out[f.stem] = _info(inst, f.stem, f.stem, None, False)
    return out


def find(name: str, inst: Install | None = None) -> ContestInfo:
    cat = catalog(inst)
    key = name.strip().lower()
    for info in cat.values():
        if key in (info.name.lower(), info.contest_file.lower()):
            return info
    raise KeyError(f"unknown contest {name!r}; known: {', '.join(cat)}")


# --- judging -------------------------------------------------------------------------

@dataclass
class Verdict:
    winner: str | None  # "green" / "red" / None (draw, no result, or single-player failure)
    scores: dict[str, float]  # colour -> score from the contest's own scoreboard
    decided_by: str  # "finish", "score", or "none"
    fell: dict[str, bool]  # colour -> dropped well below its start height
    notes: list[str] = field(default_factory=list)


def _evo_order(raw: dict) -> list[str]:
    """Colour of each Evo in GetAgentsOfClass order, verified by nearest placeholder at the start."""
    contestants = [raw["contestants"][k] for k in sorted(raw.get("contestants", {}))]
    colours = [c["colour"] for c in contestants]
    track = raw.get("track", {})
    samples = [track[k]["p"] for k in sorted(track) if track[k].get("p")]
    if not samples or not contestants:
        return colours
    first = samples[0]  # the earliest samples are taken before the Zooks exist
    order = []
    for k in sorted(first):
        p = first[k]
        best = min(contestants, key=lambda c: math.dist((p["x"], p["z"]), (c["x"], c["z"])))
        order.append(best["colour"])
    return order if len(set(order)) == len(order) else colours


def trajectories(raw: dict) -> dict[str, list[tuple[float, float, float, float]]]:
    """colour -> [(t, x, y, z), ...] from the result's track samples."""
    order = _evo_order(raw)
    out: dict[str, list] = {c: [] for c in order}
    for k in sorted(raw.get("track", {})):
        s = raw["track"][k]
        for i in sorted(s.get("p", {})):
            if i - 1 < len(order):
                p = s["p"][i]
                out[order[i - 1]].append((s["t"], p["x"], p["y"], p["z"]))
    return out


def judge(raw: dict, players: int) -> Verdict:
    contestants = raw.get("contestants", {})
    colour_of = {k: c["colour"] for k, c in contestants.items()}
    reports = [raw["reports"][k] for k in sorted(raw.get("reports", {}))]
    notes = []

    scores: dict[str, float] = {}
    decided_by = "none"
    finish = next((r for r in reports if r.get("name") == "finish"), None)
    if finish and finish.get("score"):
        decided_by = "finish"
        for i, s in finish["score"].items():
            cid = (finish.get("contestant") or {}).get(i)
            scores[colour_of.get(cid, f"contestant{cid}")] = s
    else:
        if finish:
            # e.g. China Shop: a timer ends the contest; the result is in earlier "score" reports.
            notes.append("finish reported without a scoreboard; using the latest score reports")
        for r in reports:
            if r.get("name") == "score" and r.get("score"):
                decided_by = "score"
                for i, s in r["score"].items():
                    cid = (r.get("contestant") or {}).get(i)
                    scores[colour_of.get(cid, f"contestant{cid}")] = s
        if raw.get("status") == "timeout":
            notes.append("time limit reached without a finish")

    winner = None
    colours = [c["colour"] for c in contestants.values()]
    if scores and players == 2 and len(scores) == 1 and decided_by == "finish":
        # One-sided scoreboard (Zookball: only the striker is scored; 1 = goal, 0 = ball out):
        # the other contestant gets the complement.
        (c, s), = scores.items()
        other = next((o for o in colours if o != c), None)
        if other:
            scores[other] = 0 if s > 0 else 1
            notes.append(f"{c} scored {s}; {other} assigned {scores[other]} (one-sided scoreboard)")
    if scores:
        best = max(scores.values())
        leaders = [c for c, s in scores.items() if s == best]
        if players == 1:
            winner = leaders[0] if best > 0 else None
        elif len(leaders) == 1 and best > min(scores.values()):
            winner = leaders[0]

    fell = {}
    for colour, pts in trajectories(raw).items():
        if pts:
            fell[colour] = min(p[2] for p in pts) < pts[0][2] - 5.0
    return Verdict(winner, scores, decided_by, fell, notes)


def contest_path(inst: Install, info: ContestInfo) -> Path:
    return inst.path(f"{CONTEST_DIR}/{info.contest_file}.contest")
