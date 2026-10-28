"""Challenges: the rules a model designs under, the session's rounds, its budget and submissions.

The rules are a TOML file, chosen by `challenge_rules` in bamzooki.toml [arena] ("short" or "full"
= challenges/<name>.toml, or a path):

    name = "short"                   # used in state file names
    title = "..."
    description = \"\"\"...\"\"\"          # the general rules, shown to the model by get_challenge
    music = "synth_funk"             # optional: the highlights reel's track (Music/tracks.toml; "none" = silent)
    hidden = false                   # true: models aren't told the rounds' contests (bamzooki.toml may override)
    qualifying = "first"             # short rules: qualifying games in the "first" round only, "all" rounds, "none"
    max_qualifiers = 8               # models in the knockout: the first this many to qualify ("" = no limit)
    [budget]
    runs = 20                        # test runs (one game each) the model may use (0 = unlimited)
    counts = ["run_contest"]         # tools that use a run
    trials = 5                       # run_trial games allowed besides the runs (0 = unlimited)
    test = 0                         # test_zook games (the Zook Kit's Test button) allowed (0 = unlimited)
    submissions = 1                  # submit_design calls per round
    reset_each_round = true          # short: a design phase per round, limits afresh (false: full, shared)
    [scoring]
    draw = 0.5                       # points for a draw (win = 1, loss = 0)
    [evaluation]                     # the qualifying games
    seed = ""                        # "" = random, decided when the session starts; never shown to models
    side = "green"                   # the submitted Zook's arena colour: green or red

The rounds come from bamzooki.toml [arena]: `contest` and `opponent` arrays with one element per
knockout round (the final last); "" = picked at random. A Session holds them, planned once for
everyone: visible contests and opponents are drawn when the session starts (no repeats while there
are others to draw), hidden contests when their knockout round starts.

    short (reset_each_round):  a design phase before each knockout round, the limits afresh each
                               time: every model designs round 1's Zook, then only the models still
                               in design the next round's (the arena opens each round: `current`).
                               Visible, qualifying games as `qualifying` says (a round 1 win gets
                               the model in; a later round's loss forfeits its match). Hidden, no
                               qualifying and the contest is announced once the round's Zooks are
                               in. The knockout's size fits the rounds (Session.fit): round 1 and
                               the final stay, middle rounds are dropped or added.
    full, visible:             one Zook per round designed up front, in any order, shared limits
    full, hidden:              a team of one Zook per round; in the knockout the model hears each
                               round's contest and picks a team Zook for it, each one only once
A model that hasn't put forward a Zook for a knockout round, or has no team Zook left to play
(full, hidden), forfeits that match.

Every run is a single game. Which games to play during testing (test, trial or contest, which
contest, which of its Zooks against which opponent) is up to the model. Models never see or
choose seeds: practice contests get a fresh random seed each, so games vary for them as the
real ones may.

Per-model progress lives in <zoo_dir>/<model>/challenge-<name>.json, so restarting the
server doesn't reset the budget. Reset it with: python tools/challenge.py reset --model X
"""

from __future__ import annotations

import json
import random
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .config import REPO_ROOT

CHALLENGES = REPO_ROOT / "challenges"
DEFAULT_RULES = "short"
DEFAULT_ROUNDS = (["Zook Sumo"], ["Spider"])  # contest, opponent: when bamzooki.toml gives none
COUNTERS = ("runs_used", "trials_used", "tests_used")
QUALIFYING = ("first", "all", "none")


def rules_path(rules: str | Path | None = None) -> Path:
    """challenge_rules -> its TOML: a name ("short", "full") from challenges/, or a path."""
    if not rules:
        rules = DEFAULT_RULES
    p = Path(rules)
    if p.suffix != ".toml":
        return CHALLENGES / f"{rules}.toml"
    return p if p.is_absolute() else REPO_ROOT / p


@dataclass
class Match:
    """One round: its contest and opponent ("" = not picked yet), and the qualifying game's seed and side."""
    contest: str
    opponent: str
    seed: int = 1
    side: str = "green"  # green | red

    def public(self) -> dict:
        """What models are told: the game, never its seed."""
        return {"contest": self.contest, "opponent": self.opponent, "side": self.side}


@dataclass
class Challenge:
    """The rules (a challenges/*.toml)."""
    name: str
    title: str
    description: str
    runs: int = 20
    counts: tuple[str, ...] = ("run_contest",)  # tools that use a run
    trials: int = 0  # run_trial games allowed besides the runs, when it isn't counted as one (0 = unlimited)
    tests: int = 0  # test_zook games (the Zook Kit's basic Test mode), config key "test" (0 = unlimited)
    submissions: int = 1  # per round
    reset_each_round: bool = True  # the limits start afresh each round (False: shared by all the Zooks)
    hidden: bool = False  # the rounds' contests are kept from the models
    qualifying: str = "first"  # qualifying games for the Zooks submitted: "first" round only, "all" rounds, "none"
    max_qualifiers: int | None = 8  # models in the knockout: the first this many to qualify (None = no limit)
    seed: int | None = None  # the qualifying games' seed (None = random per round)
    side: str = "green"  # the submitted Zook's side in the qualifying games
    draw_points: float = 0.0  # a draw (no winner, e.g. time limit) counts this much of a win
    time_limit: float = 120.0  # seconds of play after Go in every contest (tests and evaluation)
    music: str = ""  # highlights-reel track from Music/tracks.toml ("" = chosen by contest/default, "none" = silent)
    path: Path | None = None

    @classmethod
    def load(cls, path: str | Path | None = None) -> "Challenge":
        path = rules_path(path)
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
        budget = raw.get("budget", {})
        ev = raw.get("evaluation", {})
        if not isinstance(ev, dict):
            raise ValueError(f"{path}: [evaluation] is one table (seed, side)")
        side = ev.get("side", "green")
        if side not in ("green", "red"):
            raise ValueError(f"{path}: evaluation side must be green or red")
        seed = ev.get("seed", "")
        qualifying = raw.get("qualifying", "first")
        if isinstance(qualifying, bool):  # older rules files: true = every round
            qualifying = "all" if qualifying else "none"
        if qualifying not in QUALIFYING:
            raise ValueError(f"{path}: qualifying must be one of {', '.join(QUALIFYING)}")
        cap = raw.get("max_qualifiers", 8)
        return cls(name=raw.get("name", path.stem), title=raw.get("title", path.stem),
                   description=raw.get("description", "").strip(),
                   runs=int(budget.get("runs", 20)),
                   counts=tuple(budget.get("counts", ("run_contest",))),
                   trials=int(budget.get("trials", 0)),
                   tests=int(budget.get("test", 0)),
                   submissions=int(budget.get("submissions", 1)),
                   reset_each_round=bool(budget.get("reset_each_round", True)),
                   hidden=bool(raw.get("hidden", False)), qualifying=qualifying,
                   max_qualifiers=int(cap) if str(cap).strip() and int(cap) > 0 else None,
                   seed=int(seed) if str(seed).strip() else None, side=side,
                   draw_points=float(raw.get("scoring", {}).get("draw", 0.0)),
                   time_limit=float(raw.get("scoring", {}).get("time_limit", 120.0)),
                   music=str(raw.get("music", "")), path=path)

    def budget_info(self) -> dict:
        return {"runs": self.runs or "unlimited", "counted_tools": list(self.counts),
                "each_run_is": ("one game: a trial, or one contest between two Zooks you choose"
                                if "run_trial" in self.counts else "one contest between two Zooks you choose"),
                **({} if "run_trial" in self.counts else
                   {"trials": self.trials or "unlimited",
                    "trials_are": "run_trial games, separate from the runs: they don't use a run"}),
                **({} if "test_zook" in self.counts else
                   {"tests": self.tests or "unlimited",
                    "tests_are": "test_zook games (the Zook Kit's Test button), separate from the runs and the trials"})}


def _fill(picks: list[str], pool: list[str], rng: random.Random) -> list[str]:
    """Random picks for the "" entries: none repeats a name already in the list while the pool has others."""
    out = list(picks)
    for i, p in enumerate(out):
        if not p and pool:
            fresh = [x for x in pool if x not in out]
            out[i] = rng.choice(fresh or pool)
    return out


@dataclass
class Session:
    """This session's rounds under the rules, the same for every model."""
    rules: Challenge
    rounds: list[Match]
    hidden: bool = False
    current: int | None = None  # round by round: the round open for design (0-based), set by the arena

    @property
    def round_by_round(self) -> bool:
        """Short rules: a design phase for each knockout round, with the limits afresh each time. The
        arena opens each round (`current`) for the models still in; without the arena a model moves
        on by itself after each submission. Otherwise (full) every Zook is designed up front."""
        return self.rules.reset_each_round

    @property
    def shared_budget(self) -> bool:
        """One budget for all the Zooks (full rules), else each round's limits start afresh."""
        return not self.round_by_round

    @property
    def team(self) -> bool:
        """Full rules, hidden: the model puts a team forward, not a Zook for each known round."""
        return self.hidden and not self.round_by_round

    def qualifying_in(self, round_no: int) -> bool:
        """Does a Zook submitted for this round (1-based) play a qualifying game? Short rules with
        visible challenges only: in round 1 ("first") or in every round ("all")."""
        q = self.rules.qualifying
        return self.round_by_round and not self.hidden and (q == "all" or (q == "first" and round_no == 1))

    @property
    def qualifying(self) -> bool:
        """A qualifying win (round 1's) is needed to get into the knockout."""
        return self.qualifying_in(1)

    def fit(self, total: int, contest_pool=(), opponent_pool=(), rng: random.Random | None = None) -> list[str]:
        """Short rules: the rounds for a knockout of `total` rounds. Round 1 (everyone designed and
        qualified for it) and the final (the last round) stay; middle rounds are dropped, the
        earliest first, or random ones are added after round 1 (drawn now unless hidden). One
        knockout round plays round 1. Returns what changed, for the operator."""
        n = len(self.rounds)
        if not self.round_by_round or total < 1 or total == n:
            return []
        rng = rng or random.Random()
        first, middle, last = self.rounds[0], self.rounds[1:-1], self.rounds[-1]
        if total == 1:
            new, dropped, added = [first], self.rounds[1:], 0
        else:
            keep = middle[len(middle) - min(len(middle), total - 2):]
            dropped, added = middle[:len(middle) - len(keep)], total - 2 - len(keep)
            cs = [m.contest for m in self.rounds] + [""] * added
            ops = [m.opponent for m in self.rounds] + [""] * added
            if not self.hidden:
                cs, ops = _fill(cs, list(contest_pool), rng), _fill(ops, list(opponent_pool), rng)
            extra = [Match(c, o, self.rules.seed if self.rules.seed is not None else rng.randint(1, 999_999),
                           self.rules.side) for c, o in zip(cs[n:], ops[n:])]
            new = [first, *extra, *keep, last]
        out = []
        if dropped:
            out.append(f"dropped: {', '.join(m.contest or 'a secret contest' for m in dropped)}")
        if added:
            out.append(f"added after round 1: {added} random round(s)"
                       + ("" if self.hidden else f" ({', '.join(m.contest for m in new[1:1 + added])})"))
        self.rounds = new
        return out

    @classmethod
    def plan(cls, rules: Challenge, contests=DEFAULT_ROUNDS[0], opponents=DEFAULT_ROUNDS[1],
             hidden: bool | None = None, contest_pool=(), opponent_pool=(),
             rng: random.Random | None = None) -> "Session":
        """The rounds from bamzooki.toml's arrays. Visible: random contests and opponents are drawn now.
        Hidden: random contests stay "" until their knockout round starts."""
        rng = rng or random.Random()
        hidden = rules.hidden if hidden is None else bool(hidden)
        contests, opponents = list(contests or [""]), list(opponents or [""])
        n = max(len(contests), len(opponents))
        cs = [str(c or "").strip() for c in contests] + [""] * (n - len(contests))
        ops = [str(o or "").strip() for o in opponents] + [""] * (n - len(opponents))
        if not hidden:
            cs, ops = _fill(cs, list(contest_pool), rng), _fill(ops, list(opponent_pool), rng)
        seeds = [rules.seed if rules.seed is not None else rng.randint(1, 999_999) for _ in range(n)]
        return cls(rules, [Match(c, o, s, rules.side) for c, o, s in zip(cs, ops, seeds)], hidden)

    def knockout_round(self, index: int, total: int) -> Match | None:
        """The round a knockout round plays: the final is the last round, and with fewer knockout
        rounds the first ones are skipped. None for an extra early round (its contest is random)."""
        i = len(self.rounds) - total + index
        return self.rounds[i] if i >= 0 else None

    # --- saved for the MCP servers (one per model) ---
    def to_dict(self) -> dict:
        return {"rules": str(self.rules.path), "hidden": self.hidden, "current": self.current,
                "rounds": [{"contest": m.contest, "opponent": m.opponent, "seed": m.seed, "side": m.side}
                           for m in self.rounds]}

    @classmethod
    def from_dict(cls, d: dict) -> "Session":
        return cls(Challenge.load(d["rules"]), [Match(**r) for r in d["rounds"]], bool(d.get("hidden")),
                   d.get("current"))

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=1), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "Session":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))

    # --- what models are told ---
    def briefing(self) -> list[str]:
        """This session's rules for the models, one point each: the rules and visibility, how many
        Zooks to put forward and how, the limits, qualifying, the knockout and fair play. The arena
        adds them to every model's system prompt; get_challenge returns them too."""
        r, n = self.rules, len(self.rounds)
        zooks = f"{n} Zook{'s' if n != 1 else ''}"
        limits = [f"{r.runs or 'unlimited'} test runs (run_contest)"]
        if "run_trial" not in r.counts:
            limits.append(f"{r.trials or 'unlimited'} trials (run_trial)")
        if "test_zook" not in r.counts:
            limits.append(f"{r.tests or 'unlimited'} tests (test_zook)")
        rounds = f"are {n} challenge rounds" if n != 1 else "is 1 challenge round"
        vis = "HIDDEN" if self.hidden else "visible"
        if self.round_by_round:
            out = [f"Rules: {r.name}, with the challenges {vis}. The knockout against the other models has {n} "
                   f"round(s), and you design a NEW Zook for each round you reach: one design phase per round, "
                   f"round 1 first; after each knockout round only the models still in design the next round's Zook. "
                   f"Each design phase you MUST put forward one Zook for that round with submit_design(ref)"
                   + (", each a distinctly different design from your earlier ones." if n > 1 else "."),
                   f"If you don't, you FORFEIT: {self.forfeit_rule()}"]
            if self.hidden:
                out.append("The contests are SECRET: each knockout round's contest is announced only once every "
                           "model's Zook for it is in, so design Zooks that can handle different contests "
                           "(list_contests and get_contest show them all; you may test with any contest and opponent).")
            else:
                out.append("The planned challenges: " + "; ".join(
                    f"round {i}: {m.contest}, against {m.opponent}" for i, m in enumerate(self.rounds, 1))
                    + ". Round 1 and the final stay as planned; with more or fewer models in the knockout than these "
                      "rounds suit, rounds in between are added or dropped. Each design phase tells you its round's "
                      "challenge.")
            out.append(f"Your limits: {', '.join(limits)}, for EACH design phase: they start afresh every round.")
        else:
            out = [f"Rules: {r.name}, with the challenges {vis}. There {rounds}, so you MUST put forward {zooks}"
                   + (", each a distinctly different design." if n > 1 else "."),
                   f"If you don't, you FORFEIT: {self.forfeit_rule()}"]
            if self.team:
                out.append(f"The contests are SECRET: you won't know which contest each knockout round plays until "
                           f"it starts. Design a team of {zooks} that between them can handle different contests "
                           f"(list_contests and get_contest show them all; you may test with any contest and "
                           f"opponent), and put each forward with submit_design(ref).")
            else:
                out.append("The challenges: " + "; ".join(
                    f"round {i}: {m.contest}, against {m.opponent}" for i, m in enumerate(self.rounds, 1)) + ".")
                out.append(f"Design a separate Zook for each challenge, then put one forward for each with "
                           f"submit_design(ref, round), rounds 1-{n}.")
            out.append(f"Your limits: {', '.join(limits)}, SHARED by all your Zooks: plan how you spend them "
                       f"across all of them.")
        game = (f"plays that round's qualifying game against the round's opponent, on the side shown (win 1, draw "
                f"{r.draw_points:g}, loss 0)")
        if self.qualifying_in(2):
            out.append(f"Each Zook you submit {game}. Win round 1's to get into the knockout, and each later round's "
                       f"to play your knockout match in it: lose it and you forfeit that match.")
        elif self.qualifying:
            out.append(f"Your round 1 Zook {game}: win it to get into the knockout. Later rounds have no qualifying "
                       f"game.")
        else:
            out.append("There is no qualifying game: putting your " + ("round 1 Zook" if self.round_by_round else
                                                                       "Zooks") + " forward gets you into the knockout.")
        if r.max_qualifiers:
            out.append(f"At most {r.max_qualifiers} models get into the knockout: the first {r.max_qualifiers} to "
                       + ("qualify." if self.qualifying else "put their Zooks forward."))
        out.append(("In the knockout, each round's contest is announced as the round starts and you pick one of your "
                    "team Zooks for it. Each Zook can be played only once, so keep the right Zook for the right "
                    "contest." if self.team else
                    "In the knockout, each round plays its challenge's contest with the Zook you put forward for it.")
                   + " Knockout matches swap sides between games.")
        if n > 1:
            out.append("Your Zooks must be distinctly different designs: submit_design refuses a Zook that is too close "
                       "to one you've already put forward (a small change to another Zook doesn't make a new one), "
                       "so design each one as its own Zook from the start.")
        out.append("Don't copy an example Zook: save_zook and submit_design refuse a design that is too close to one.")
        out.append("Games aren't deterministic: the same two Zooks can get a different result each time, so aim to win "
                   "clearly and consistently.")
        return out

    def forfeit_rule(self) -> str:
        """What happens in the knockout to a model that hasn't put all its Zooks forward."""
        if self.team:
            return ("each knockout match needs a team Zook you haven't played yet, and when you have none left you "
                    "lose that match without a game.")
        if self.round_by_round:
            return "a knockout round you have no Zook for is a match you lose without a game."
        return "in a knockout round whose challenge you have no Zook for, you lose the match without a game."

    def describe(self, current: int | None = None) -> dict:
        """The task for get_challenge. current: the round being worked on (round by round only)."""
        r, n = self.rules, len(self.rounds)
        out = {"title": r.title, "rules_of_this_session": self.briefing(), "description": r.description, "rounds": n,
               "zooks_required": n if not self.round_by_round else "one for each round you reach",
               "missing_zook": "forfeit: " + self.forfeit_rule(),
               "scoring": {"win": 1, "draw": r.draw_points, "loss": 0}, "time_limit_s": r.time_limit,
               "budget": {**r.budget_info(),
                          "limits": ("shared by all your Zooks: plan how you spend them" if self.shared_budget
                                     else "start afresh for each round")}}
        if self.team:
            out["how_it_works"] = (
                f"The challenges are SECRET. The knockout against the other models has {n} round(s) and you don't "
                f"know which contest each will be. Design and test a team of {n} different Zooks, then submit them "
                f"one by one with submit_design(ref) ({n} in all). At the start of each knockout round its contest "
                "is announced and you pick one of your team Zooks for it; each Zook can be played only once, so "
                "plan a team that covers different contests (list_contests shows them all). You may test with any "
                "contest and opponent. There is no qualifying game.")
            return out
        if not self.hidden:
            out["challenges"] = [{"round": i + 1, "contest": m.contest, "opponent_to_beat": m.opponent}
                                 for i, m in enumerate(self.rounds)]
        if self.round_by_round:
            out["how_it_works"] = (
                f"{n} round(s), designed one at a time with fresh limits each round. Design a Zook for the round "
                f"that is open, then submit_design(ref)"
                + (": in a round with a qualifying game it plays it against the round's opponent (you play the side "
                   "shown)" if self.qualifying else "")
                + (". The next round opens for you only if you're still in the knockout"
                   if self.current is not None else ", which moves you on to the next round")
                + ". In the knockout each round plays its contest with the Zook you submitted for it.")
            if current is not None and current < n:
                m = self.rounds[current]
                out["current_round"] = {"round": current + 1, "of": n,
                                        **({"contest": "SECRET: announced once every model's Zook is in"}
                                           if self.hidden else m.public()),
                                        "qualifying_game": self.qualifying_in(current + 1)}
            elif current is not None:
                out["current_round"] = "all rounds done"
        else:
            out["how_it_works"] = (
                f"Design a separate Zook for each of the {n} challenge(s) (contest, and the example Zook to beat), "
                f"sharing your limits between them. When you're done, put one forward for each with "
                f"submit_design(ref, round) (rounds 1-{n}). There is no qualifying game: in the knockout against the "
                "other models each round plays its contest with the Zook you put forward for it.")
        return out


def _as_list(v) -> list[str]:
    return [v] if isinstance(v, str) else list(v)


def plan_from_config(cfg, rules: str | Path | None = None, install=None,
                     rng: random.Random | None = None) -> Session:
    """The session bamzooki.toml [arena] describes: challenge_rules, contest, opponent, hidden. Random
    contests come from the contest pack's two-player contests, random opponents from the example Zooks."""
    from .install import Install
    from .runner import contests as catalog

    a = cfg.arena
    rules = Challenge.load(rules or a.get("challenge_rules") or a.get("challenge") or cfg.challenge)
    inst = install or Install.discover()
    pool = [n for n, c in catalog.catalog(inst).items() if c.players == 2 and c.in_pack]
    examples = sorted({p.parent.name for p in inst.example_zooks() if "Teams" in p.parts})
    picked = _as_list(a.get("contest", DEFAULT_ROUNDS[0]))
    against = _as_list(a.get("opponent", DEFAULT_ROUNDS[1]))
    for c in filter(None, picked):
        catalog.find(c, inst)  # a misspelt contest fails here, naming the real ones
    for o in filter(None, against):
        if o not in examples and "/" not in o:
            raise ValueError(f"[arena] opponent {o!r}: use an example Zook ({', '.join(examples)}) or \"\"")
    hidden = a.get("hidden")
    return Session.plan(rules, picked, against, None if hidden is None else bool(hidden), pool, examples, rng)


class ChallengeState:
    """Budget and submissions for one model in one challenge (a small JSON file). Round by round,
    advance() starts the next round's limits afresh."""

    def __init__(self, challenge: Challenge, model_dir: Path):
        self.challenge = challenge
        self.path = model_dir / f"challenge-{challenge.name}.json"
        self.data = self._fresh()
        if self.path.exists():
            self.data.update(json.loads(self.path.read_text(encoding="utf-8")))

    def _fresh(self) -> dict:
        return {"challenge": self.challenge.name, "round": 0, **{k: 0 for k in COUNTERS}, "past_rounds": [],
                "log": [], "submissions": []}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=1), encoding="utf-8")

    @property
    def round(self) -> int:
        """The round being worked on (0-based; round by round only)."""
        return self.data.get("round", 0)

    @property
    def runs_used(self) -> int:
        return self.data["runs_used"]

    @property
    def total_runs_used(self) -> int:
        """Runs used in all rounds so far."""
        return self.runs_used + sum(r.get("runs_used", 0) for r in self.data.get("past_rounds", []))

    @property
    def runs_left(self) -> int | None:
        return None if not self.challenge.runs else max(0, self.challenge.runs - self.runs_used)

    @property
    def trials_used(self) -> int:
        return self.data.get("trials_used", 0)

    @property
    def trials_left(self) -> int | None:
        return None if not self.challenge.trials else max(0, self.challenge.trials - self.trials_used)

    @property
    def tests_used(self) -> int:
        return self.data.get("tests_used", 0)

    @property
    def tests_left(self) -> int | None:
        return None if not self.challenge.tests else max(0, self.challenge.tests - self.tests_used)

    def counts(self, tool: str) -> bool:
        return tool in self.challenge.counts

    def is_trial(self, tool: str) -> bool:
        """A trial on its own allowance (not counted as a run)."""
        return tool == "run_trial" and not self.counts(tool)

    def is_test(self, tool: str) -> bool:
        """A basic Zook Kit test on its own allowance (not counted as a run)."""
        return tool == "test_zook" and not self.counts(tool)

    def check(self, tool: str) -> str | None:
        """Why this run may not happen, or None."""
        if self.counts(tool) and self.runs_left == 0:
            return (f"No test runs left: all {self.challenge.runs} are used. "
                    "Submit your best design with submit_design.")
        if self.is_trial(tool) and self.trials_left == 0:
            return (f"No trials left: all {self.challenge.trials} are used. Test with run_contest (it uses a "
                    "test run) or submit your best design with submit_design.")
        if self.is_test(tool) and self.tests_left == 0:
            return f"No tests left: all {self.challenge.tests} are used. Use run_trial or run_contest instead."
        return None

    def spend(self, tool: str, detail: str) -> None:
        if self.counts(tool):
            self.data["runs_used"] += 1
        elif self.is_trial(tool):
            self.data["trials_used"] = self.trials_used + 1
        elif self.is_test(tool):
            self.data["tests_used"] = self.tests_used + 1
        self.data["log"].append({"tool": tool, "detail": detail, "time": time.strftime("%Y-%m-%d %H:%M:%S")})
        self.save()

    def advance(self) -> None:
        """Round by round: on to the next round, whose limits start afresh."""
        self.data.setdefault("past_rounds", []).append({k: self.data.get(k, 0) for k in COUNTERS})
        self.data.update({k: 0 for k in COUNTERS}, round=self.round + 1)
        self.save()

    def budget(self) -> dict:
        left = self.runs_left
        out = {"runs_used": self.runs_used, "runs_left": "unlimited" if left is None else left,
               "runs_total": self.challenge.runs or "unlimited"}
        reminders = []
        if left is not None:
            reminders.append(f"{left} of {self.challenge.runs} test runs left." if left else
                             "No test runs left. Submit your best design with submit_design.")
        if "run_trial" not in self.challenge.counts:
            trials = self.trials_left
            out["trials_used"], out["trials_left"] = self.trials_used, "unlimited" if trials is None else trials
            if trials is not None:
                reminders.append(f"{trials} of {self.challenge.trials} trials left." if trials else "No trials left.")
        if "test_zook" not in self.challenge.counts:
            tests = self.tests_left
            out["tests_used"], out["tests_left"] = self.tests_used, "unlimited" if tests is None else tests
            if tests is not None:
                reminders.append(f"{tests} of {self.challenge.tests} tests left." if tests else "No tests left.")
        if reminders:
            out["reminder"] = " ".join(reminders)
        return out

    @property
    def submissions(self) -> list[dict]:
        return self.data["submissions"]

    def submitted_for(self, round_no: int | None) -> list[dict]:
        """Submissions for a round (1-based), or the team's (None)."""
        return [s for s in self.submissions if s.get("round") == round_no]

    def record_submission(self, entry: dict) -> None:
        self.data["submissions"].append(entry)
        self.save()

    def reset(self) -> None:
        self.data = self._fresh()
        self.save()
