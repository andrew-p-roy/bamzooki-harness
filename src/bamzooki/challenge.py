"""Challenges: the task given to a model, its run budget and the final evaluation.

A challenge is a TOML file (see challenges/default.toml):

    name = "default"                 # used in state file names
    title = "..."
    description = \"\"\"...\"\"\"          # shown to the model by get_challenge
    [budget]
    runs = 20                        # engine test runs the model may use (0 = unlimited)
    counts = ["run_contest", "run_trial"]
    submissions = 1                  # how many times submit_design may be called
    [[evaluation]]                   # official matches run by submit_design (not counted)
    contest = "Zook Sumo"
    opponent = "Spider"              # an example Zook
    seeds = [1, 2, 3]
    side = "green"                   # the submitted Zook's colour: green, red or both
    [scoring]
    draw = 0.0                       # points for a draw (win = 1, loss = 0)

Per-model progress lives in <zoo_dir>/<model>/challenge-<name>.json, so restarting the
server doesn't reset the budget. Reset it with: python tools/challenge.py reset --model X
"""

from __future__ import annotations

import json
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .config import REPO_ROOT

DEFAULT_CHALLENGE = REPO_ROOT / "challenges" / "default.toml"


@dataclass
class Match:
    contest: str
    opponent: str
    seeds: list[int] = field(default_factory=lambda: [1])
    side: str = "green"  # green | red | both


@dataclass
class Challenge:
    name: str
    title: str
    description: str
    runs: int = 20
    counts: tuple[str, ...] = ("run_contest", "run_trial")
    submissions: int = 1
    evaluation: list[Match] = field(default_factory=list)
    draw_points: float = 0.0  # a draw (no winner, e.g. time limit) counts this much of a win
    path: Path | None = None

    @classmethod
    def load(cls, path: str | Path | None = None) -> "Challenge":
        path = Path(path) if path else DEFAULT_CHALLENGE
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
        budget = raw.get("budget", {})
        matches = []
        for m in raw.get("evaluation", []):
            side = m.get("side", "green")
            if side not in ("green", "red", "both"):
                raise ValueError(f"{path}: evaluation side must be green, red or both")
            matches.append(Match(m["contest"], m["opponent"], list(m.get("seeds", [1])), side))
        return cls(name=raw.get("name", path.stem), title=raw.get("title", path.stem),
                   description=raw.get("description", "").strip(), runs=int(budget.get("runs", 20)),
                   counts=tuple(budget.get("counts", ("run_contest", "run_trial"))),
                   submissions=int(budget.get("submissions", 1)), evaluation=matches,
                   draw_points=float(raw.get("scoring", {}).get("draw", 0.0)), path=path)

    def matches(self) -> list[dict]:
        """The evaluation expanded into individual games."""
        games = []
        for m in self.evaluation:
            for side in (("green", "red") if m.side == "both" else (m.side,)):
                for seed in m.seeds:
                    games.append({"contest": m.contest, "opponent": m.opponent, "side": side, "seed": seed})
        return games

    def describe(self) -> dict:
        return {"title": self.title, "description": self.description,
                "evaluation": [vars(m) for m in self.evaluation], "games_in_evaluation": len(self.matches()),
                "scoring": {"win": 1, "draw": self.draw_points, "loss": 0,
                            "score": "points / games (a draw is a game without a winner)"},
                "budget": {"runs": self.runs or "unlimited", "counted_tools": list(self.counts),
                           "submissions": self.submissions}}


class ChallengeState:
    """Budget and submissions for one model in one challenge (a small JSON file)."""

    def __init__(self, challenge: Challenge, model_dir: Path):
        self.challenge = challenge
        self.path = model_dir / f"challenge-{challenge.name}.json"
        self.data = {"challenge": challenge.name, "runs_used": 0, "log": [], "submissions": []}
        if self.path.exists():
            self.data.update(json.loads(self.path.read_text(encoding="utf-8")))

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=1), encoding="utf-8")

    @property
    def runs_used(self) -> int:
        return self.data["runs_used"]

    @property
    def runs_left(self) -> int | None:
        return None if not self.challenge.runs else max(0, self.challenge.runs - self.runs_used)

    def counts(self, tool: str) -> bool:
        return tool in self.challenge.counts

    def check(self, tool: str) -> str | None:
        """Why this run may not happen, or None."""
        if self.counts(tool) and self.runs_left == 0:
            return (f"No test runs left: all {self.challenge.runs} are used. "
                    "Submit your best design with submit_design.")
        return None

    def spend(self, tool: str, detail: str) -> None:
        if self.counts(tool):
            self.data["runs_used"] += 1
        self.data["log"].append({"tool": tool, "detail": detail, "time": time.strftime("%Y-%m-%d %H:%M:%S")})
        self.save()

    def budget(self) -> dict:
        left = self.runs_left
        out = {"runs_used": self.runs_used, "runs_left": "unlimited" if left is None else left,
               "runs_total": self.challenge.runs or "unlimited",
               "submissions_left": max(0, self.challenge.submissions - len(self.data["submissions"]))}
        if left is not None:
            out["reminder"] = (f"{left} of {self.challenge.runs} test runs left." if left else
                               "No test runs left. Submit your best design with submit_design.")
        return out

    def can_submit(self) -> str | None:
        if len(self.data["submissions"]) >= self.challenge.submissions:
            return f"Already submitted {len(self.data['submissions'])} time(s); the challenge allows {self.challenge.submissions}."
        return None

    def record_submission(self, entry: dict) -> None:
        self.data["submissions"].append(entry)
        self.save()

    def reset(self) -> None:
        self.data = {"challenge": self.challenge.name, "runs_used": 0, "log": [], "submissions": []}
        self.save()
