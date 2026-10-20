"""Challenges: the task given to a model, its run budget and the final evaluation game.

A challenge is a TOML file (see challenges/default.toml):

    name = "default"                 # used in state file names
    title = "..."
    description = \"\"\"...\"\"\"          # shown to the model by get_challenge
    music = "synth_funk"             # optional: the highlights reel's track (Music/tracks.toml; "none" = silent)
    [budget]
    runs = 20                        # test runs (one game each) the model may use (0 = unlimited)
    counts = ["run_contest", "run_trial"]
    submissions = 1                  # how many times submit_design may be called
    [scoring]
    draw = 0.5                       # points for a draw (win = 1, loss = 0)
    [evaluation]                     # the ONE official game played by submit_design (not counted)
    contest = "Zook Sumo"
    opponent = "Spider"              # an example Zook ("Spider") or a saved design ("model/Name@n")
    seed = 1
    side = "green"                   # the submitted Zook's arena colour: green or red
    reveal_seed = false              # hide the seed so models can't rehearse the exact game

Every run is a single game. Which games to play during testing (trial or contest, which
contest, which of its Zooks against which opponent, which seed) is up to the model.

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
    seed: int = 1
    side: str = "green"  # green | red
    reveal_seed: bool = True  # False: models are told the game but not its seed

    def public(self) -> dict:
        d = {"contest": self.contest, "opponent": self.opponent, "side": self.side}
        d["seed"] = self.seed if self.reveal_seed else "secret (the same for every entrant)"
        return d


@dataclass
class Challenge:
    name: str
    title: str
    description: str
    evaluation: Match
    runs: int = 20
    counts: tuple[str, ...] = ("run_contest", "run_trial")
    submissions: int = 1
    draw_points: float = 0.0  # a draw (no winner, e.g. time limit) counts this much of a win
    time_limit: float = 120.0  # seconds of play after Go in every contest (tests and evaluation)
    music: str = ""  # highlights-reel track from Music/tracks.toml ("" = chosen by contest/default, "none" = silent)
    path: Path | None = None

    @classmethod
    def load(cls, path: str | Path | None = None) -> "Challenge":
        path = Path(path) if path else DEFAULT_CHALLENGE
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
        budget = raw.get("budget", {})
        ev = raw.get("evaluation")
        if isinstance(ev, list):
            if len(ev) != 1:
                raise ValueError(f"{path}: [evaluation] is a single game; use one [evaluation] table")
            ev = ev[0]
        if not isinstance(ev, dict) or "contest" not in ev or "opponent" not in ev:
            raise ValueError(f"{path}: needs an [evaluation] table with contest and opponent")
        side = ev.get("side", "green")
        if side not in ("green", "red"):
            raise ValueError(f"{path}: evaluation side must be green or red")
        match = Match(ev["contest"], ev["opponent"], int(ev.get("seed", 1)), side,
                      bool(ev.get("reveal_seed", True)))
        return cls(name=raw.get("name", path.stem), title=raw.get("title", path.stem),
                   description=raw.get("description", "").strip(), evaluation=match,
                   runs=int(budget.get("runs", 20)),
                   counts=tuple(budget.get("counts", ("run_contest", "run_trial"))),
                   submissions=int(budget.get("submissions", 1)),
                   draw_points=float(raw.get("scoring", {}).get("draw", 0.0)),
                   time_limit=float(raw.get("scoring", {}).get("time_limit", 120.0)),
                   music=str(raw.get("music", "")), path=path)

    def describe(self) -> dict:
        return {"title": self.title, "description": self.description,
                "evaluation_game": self.evaluation.public(),
                "scoring": {"win": 1, "draw": self.draw_points, "loss": 0},
                "time_limit_s": self.time_limit,
                "budget": {"runs": self.runs or "unlimited", "counted_tools": list(self.counts),
                           "each_run_is": "one game: a trial, or one contest between two Zooks you choose",
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
