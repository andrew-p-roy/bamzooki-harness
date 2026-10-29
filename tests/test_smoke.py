"""Smoke tests: whole arena sessions under every game mode, end to end, without the game or a model.

Each test plans a session from a bamzooki.toml [arena] table and runs the arena as tools/arena.py
does (Arena and its repl; --auto unless the test types at the keyboard), with:
  - scripted models: in each design phase a model puts its Zook(s) forward through a real Studio
    reading the arena's session.json (as its MCP server does), and the arena takes the result as it
    takes a real submit_design result. Knockout picks are answered by naming one of the options.
  - a stand-in engine: the stronger model's Zook wins every game (or every game is a draw, or the
    first game stalls), and every game it's asked to play is logged with its contest.
What's checked: who qualifies and who isn't called, the design phases between knockout rounds, the
knockout's rounds, contests, byes, forfeits and champion, and the session's files.
"""

from __future__ import annotations

import asyncio
import json
import random
import re
import shutil
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

import pytest

from bamzooki import arena as arena_module
from bamzooki import runner, showtime, studio, tournament
from bamzooki.arena import Arena, Entrant
from bamzooki.challenge import Challenge, Session, plan_from_config
from bamzooki.config import Config
from bamzooki.install import Install
from bamzooki.runner import contests
from bamzooki.sdk.store import Zoo, entrant_folder

CONTESTS = ["Dodgy Zook", "Merry Go Zook", "Weakest Zook", "Zookball Green To Score", "Zookball Red To Score",
            "Zook Marbles", "Zook Smash", "Zook Sumo", "Zook Hurdles"]  # the pack's two-player contests
EXAMPLES = ["Ant", "Leapsa", "Scrabber", "Spider", "Twigger", "Wormthing"]
SHORT_ROUNDS = {"contest": ["", "", "Zook Sumo"], "opponent": ["", "", "Spider"]}  # bamzooki.toml's


@dataclass
class Model:
    """How a scripted model behaves: its Zooks' strength (the stronger always wins a game), its
    qualifying results by round (default: a win), the rounds it puts nothing forward for (hidden
    full rules: how many team Zooks short it is), and the test runs it uses in each design phase."""
    strength: int
    qualifying: dict[int, str] = field(default_factory=dict)
    skip: set[int] = field(default_factory=set)
    runs: int = 0


class Script:
    """The operator at the keyboard: the lines are handed out one at a time as the arena asks (none
    are 'typed early', so a confirm's clearing of earlier lines doesn't lose any). None = input ends."""

    def __init__(self, lines=()):
        self.left, self.busy, self.asked = list(lines), False, []

    def get(self):
        line = self.left.pop(0) if self.left else None
        self.asked.append(line)
        return line

    def drain(self):
        return []

    def halting(self):
        return False


class Models:
    """The models' replies outside their tool loop: a knockout pick names one of the options."""

    def __init__(self, rng: random.Random):
        self.rng, self.asked = rng, []

    async def chat(self, model, messages, tools, on_delta=None, single_tool=False, tool_choice="auto"):
        prompt = messages[-1]["content"]
        prompt = prompt if isinstance(prompt, str) else prompt[0]["text"]
        self.asked.append((model, prompt))
        if m := re.search(r"Pick the Zook you'll play: (.+?)\.(?: Each| Answer)", prompt):
            reply = f"{self.rng.choice(m.group(1).split(', '))}, for this one"
        else:
            reply = f"{model} says hello"
        return {"choices": [{"message": {"content": reply}}], "usage": {"total_tokens": 10, "cost": 0.001}}


class World:
    """The game install, the arena folder and everything the tests look at afterwards."""

    def __init__(self, tmp_path: Path, monkeypatch):
        self.mp = monkeypatch
        root = tmp_path / "BAMZOOKi"
        self.examples = root / "Teams" / "examples" / "Creatures"
        for name in EXAMPLES:
            (self.examples / name).mkdir(parents=True)
            (self.examples / name / "1.0.zook").write_bytes(b"")
        self.cfg = Config(arena_dir=tmp_path / "Arena")
        self.inst = Install(root=root, config=self.cfg)
        self.models: dict[str, Model] = {}  # by entrant folder
        self.games: list[SimpleNamespace] = []  # every knockout game: round index, contest, sides
        self.rounds: list[dict] = []  # every knockout round as it starts: name, field
        self.knockouts: list[tuple] = []  # what each tournament.knockout returned
        self.events: list[tuple] = []  # ("design", model, round) and ("contest", name), in order
        self.qualifiers: list[tuple] = []  # qualifying games: model, round, contest, opponent
        self.refused: list[dict] = []  # submit_design errors
        self.draws, self.stalls = False, 0

        catalog = {n: contests.ContestInfo(n, "CP_" + n.upper().replace(" ", "_"), 2, ["green", "red"])
                   for n in CONTESTS}
        catalog["CP_ZOOK_IN_A_CHINA_SHOP_GREEN"] = contests.ContestInfo(
            "CP_ZOOK_IN_A_CHINA_SHOP_GREEN", "CP_ZOOK_IN_A_CHINA_SHOP_GREEN", 1, ["green"], in_pack=False)
        real_find = contests.find

        def find(name, inst=None):
            info = real_find(name, inst)
            self.events.append(("contest", info.name))
            return info

        world = self

        class Engine:
            """The game: the stronger model's Zook wins (else a draw at the time limit, or a stall)."""

            def __init__(self, install=None):
                self.install, self.root = world.inst, world.inst.root

            def prepare(self):
                pass

            def selection(self, contest, zooks, cancel, **kw):
                cancel.wait(5)

            def showcase(self, zook, name, cancel, **kw):
                cancel.wait(5)

            def run(self, job, timeout=None, focus="idle", name=None):
                info, green, red = job.args
                g, r = green.parent.parent.name, red.parent.parent.name
                world.games.append(SimpleNamespace(round=len(world.rounds) - 1, contest=info.name, green=g, red=r,
                                                   green_zook=green.parent.name, red_zook=red.parent.name,
                                                   seed=job.seed))
                if world.stalls:
                    world.stalls -= 1
                    status, winner = "stalled", None
                elif world.draws:
                    status, winner = "timeout", None
                else:
                    status = "finished"
                    winner = "green" if world.models[g].strength > world.models[r].strength else "red"
                return SimpleNamespace(status=status, winner=winner, video=None, finish_time=None, video_lead=0.0,
                                       verdict=None, sim_time=60.0, scores={})

        real_knockout = tournament.knockout

        def knockout(seats, play, say, **kw):
            start = kw["start_round"]

            def start_round(index, total, name, field):
                self.rounds.append({"name": name, "field": [s.model for s in field]})
                start(index, total, name, field)
            out = real_knockout(seats, play, say, **{**kw, "start_round": start_round})
            self.knockouts.append(out)
            return out

        monkeypatch.setattr(contests, "catalog", lambda inst=None: dict(catalog))
        monkeypatch.setattr(contests, "find", find)
        monkeypatch.setattr(runner, "Engine", Engine)
        monkeypatch.setattr(runner, "Job", lambda *args, **kw: SimpleNamespace(args=args, **kw))
        monkeypatch.setattr(tournament, "knockout", knockout)
        monkeypatch.setattr(Install, "discover", classmethod(lambda cls, config=None: world.inst))
        monkeypatch.setattr(arena_module, "load_config", lambda: world.cfg)
        monkeypatch.setattr(arena_module, "load_api_key", lambda: "test-key")
        # a design is known by its name: different names are different Zooks, none is an example's copy
        monkeypatch.setattr(studio.Zook, "load", staticmethod(lambda path, key: path.parent.name))
        monkeypatch.setattr(studio, "too_like", lambda zook, others: None)

    # --- the session ---
    def plan(self, arena: dict, rules=None, seed=1) -> Session:
        self.cfg.arena = dict(arena)
        return plan_from_config(self.cfg, rules, install=self.inst, rng=random.Random(seed))

    def run(self, arena: dict, models: dict[str, Model], rules=None, auto=True, lines=(), show=False,
            seed=1) -> Arena:
        """A whole session: plan it, call every queued model in turn, play the knockout, sign off."""
        random.seed(seed)  # the arena's own draws (contests drawn as a hidden round starts)
        self.models = {entrant_folder(m): spec for m, spec in models.items()}
        session = self.plan(arena, rules, seed)
        for log in (self.games, self.rounds, self.knockouts, self.events, self.qualifiers, self.refused):
            log.clear()  # from here on: the session itself
        a = Arena(session, list(models), auto=auto, show=show, announcer=None, play_music=False)
        a.router = Models(random.Random(seed))
        shutil.rmtree(self.cfg.zoo_dir, ignore_errors=True)  # every model starts afresh (main() archives them)
        a.save_session()

        async def start(model):
            a.entrant = a.entrants[model] = Entrant(model, messages=[{"role": "system", "content": ""}])
            self.design_phase(a, a.entrant)

        async def design(e, slot, round_name):
            assert a.plan.current == slot  # the round open for design
            a.entrant, e.submitted = e, False
            self.design_phase(a, e)

        async def strictly(work):  # a failure fails the test (the arena would report it and carry on)
            await work

        async def nothing(*args, **kw):
            pass

        a.start, a.design, a._safely, a._settle, a._connect = start, design, strictly, nothing, nothing
        a._selection_stop = lambda shown=0.0: Arena._selection_stop(a, 0)  # no waiting for the screen
        self.keyboard = Script(lines)
        self.mp.setattr(arena_module, "Keyboard", lambda: self.keyboard)
        asyncio.run(a.repl())
        return a

    def studio(self, model: str, session: Session) -> studio.Studio:
        """The model's MCP server for one design phase: a Studio on the arena's session file."""
        s = studio.Studio.__new__(studio.Studio)
        s.engine, s.position, s.view_fit, s.fullscreen, s.live_sound, s.music = None, "none", None, False, False, False
        s.install = SimpleNamespace(zook_key=b"", config=self.cfg)
        s.zoo = Zoo(self.inst, examples_root=self.examples)
        s.lock = threading.Lock()
        s.session, s.challenge = session, session.rules
        s.model = s.model_name = s.state = None
        s._is_copy = lambda zook: False
        s._set_model(model)
        return s

    def design_phase(self, a: Arena, e: Entrant) -> None:
        """One model's design phase: the Zook(s) it owes put forward with submit_design, each result
        handed to the arena as the tool result."""
        session = Session.load(a.session_file)
        s = self.studio(e.model, session)
        spec = self.models[s.model]
        n = len(session.rounds)
        for _ in range(spec.runs):
            s.state.spend("run_contest", "practice")

        def qualifying_game(mine, match, round_no):
            self.qualifiers.append((e.model, round_no, match.contest, match.opponent))
            outcome = spec.qualifying.get(round_no, "win")
            return {"outcome": outcome, "score": {"win": 1.0, "draw": session.rules.draw_points, "loss": 0.0}[outcome],
                    "status": "finished", "winner": None}
        s._qualifying_game = qualifying_game

        if session.round_by_round:
            k = s.state.round + 1
            info = s.challenge_info()["current_round"]
            assert info["round"] == k == session.current + 1
            assert ("SECRET" in info["contest"]) == session.hidden
            todo = [] if k in spec.skip else [(f"Zook{k}", None)]
            self.events.append(("design", e.model, k))
        elif session.team:
            todo = [(f"Team{i}", None) for i in range(1, n + 1 - len(spec.skip))]
        else:
            todo = [(f"Zook{k}", k) for k in range(1, n + 1) if k not in spec.skip]
        for name, round_no in todo:
            _, path = s.zoo.new_version(s.model, name)
            path.write_bytes(b"")
            out = s.submit(name, round_no)
            if "error" in out:
                self.refused.append(out)
                continue
            a.entrant = e
            a._summarise("submit_design", json.dumps(out))

    # --- what happened ---
    @property
    def bracket(self) -> list[list[tournament.Bout]]:
        return self.knockouts[-1][0]

    @property
    def places(self) -> list[tuple[int, tournament.Seat]]:
        return self.knockouts[-1][1]

    def champion(self) -> str:
        return self.places[0][1].model

    def round_contests(self) -> list[set[str]]:
        """The contests each knockout round's games were played on."""
        return [{g.contest for g in self.games if g.round == i} for i in range(len(self.rounds))]

    def designed(self, k: int) -> list[str]:
        return [m for kind, *rest in self.events if kind == "design" for m, r in [rest] if r == k]


def strongest(models: list[str], w: World) -> str:
    return max(models, key=lambda m: w.models[entrant_folder(m)].strength)


def field_of(n: int, **specs) -> dict[str, Model]:
    """n models lab/m0 ... (m0 weakest), with any overrides: m3=Model(...)."""
    return {f"lab/m{i}": specs.get(f"m{i}", Model(i)) for i in range(n)}


@pytest.fixture
def w(tmp_path, monkeypatch):
    return World(tmp_path, monkeypatch)


def rules_file(tmp_path: Path, base: str, **changes) -> Path:
    """A copy of challenges/<base>.toml with top-level settings changed (as an operator's own rules)."""
    text = Challenge.load(base).path.read_text(encoding="utf-8")
    for key, value in changes.items():
        text, n = re.subn(rf"(?m)^{key} = .*?(\s+#|$)", lambda m: f"{key} = {json.dumps(value)}{m.group(1)}", text)
        assert n == 1, key
    p = tmp_path / f"{base}-{'-'.join(changes)}.toml"
    p.write_text(text, encoding="utf-8")
    return p


def no_seeds_told(w: World, a: Arena) -> None:
    """The models never hear a seed: not in their prompts, not in what they're asked in the knockout."""
    seeds = {str(m.seed) for m in a.plan.rounds} | {str(g.seed) for g in w.games}
    for _, prompt in a.router.asked:
        assert not any(re.search(rf"\b{s}\b", prompt) for s in seeds if len(s) > 2)
    told = json.dumps([e.messages for e in a.entrants.values()])
    assert "seed" not in told.lower()


# --- short rules ---------------------------------------------------------------------------------

def test_short_rules_eight_models_design_each_round_and_the_strongest_wins(w):
    a = w.run(SHORT_ROUNDS, field_of(8))
    assert [r["name"] for r in w.rounds] == ["Quarter-finals", "Semi-finals", "Final"]
    # round 1: everyone designed and played round 1's qualifying game against round 1's opponent
    assert sorted(w.designed(1)) == sorted(a.entrants) and len(w.qualifiers) == 8
    assert {(c, o) for _, k, c, o in w.qualifiers} == {(a.plan.rounds[0].contest, a.plan.rounds[0].opponent)}
    # later rounds: only the models still in designed, each before its round (no qualifying game)
    assert sorted(w.designed(2)) == sorted(w.rounds[1]["field"]) and len(w.designed(2)) == 4
    assert sorted(w.designed(3)) == sorted(w.rounds[2]["field"]) and len(w.designed(3)) == 2
    # the rounds: random contests and opponents drawn at the start, none twice; the final as set
    contests_, opponents = [m.contest for m in a.plan.rounds], [m.opponent for m in a.plan.rounds]
    assert contests_[-1] == "Zook Sumo" and opponents[-1] == "Spider"
    assert len(set(contests_)) == 3 and len(set(opponents)) == 3 and set(contests_) <= set(CONTESTS)
    assert w.round_contests() == [{c} for c in contests_]  # every game of a round on its contest
    # each round's games are played with the Zook designed for that round
    assert all(g.green_zook == g.red_zook == f"Zook{g.round + 1}" for g in w.games)
    assert w.champion() == "lab/m7" and len([b for r in w.bracket for b in r if b.games]) == 7
    hall = showtime.hall_of_fame(w.cfg.arena_dir)
    assert [h["champion"] for h in hall] == ["lab/m7"] and hall[0]["field"] == 8 and hall[0]["zook_name"] == "Zook3"
    assert a.plan.current == 0 and Session.load(a.session_file).current == 0  # new challengers design round 1
    assert list((w.cfg.arena_dir / "Sessions").glob("*.txt")) and not w.refused
    no_seeds_told(w, a)


def test_short_rules_a_smaller_field_drops_a_middle_round_and_losers_stay_out(w, capsys):
    models = field_of(6, m0=Model(0, qualifying={1: "loss"}), m1=Model(1, qualifying={1: "draw"}))
    a = w.run(SHORT_ROUNDS, models)
    assert {m for m, e in a.entrants.items() if e.qualified} == {"lab/m2", "lab/m3", "lab/m4", "lab/m5"}
    assert [r["name"] for r in w.rounds] == ["Semi-finals", "Final"]
    assert len(a.plan.rounds) == 2 and a.plan.rounds[-1].contest == "Zook Sumo"  # round 1 and the final stay
    assert "dropped:" in capsys.readouterr().out
    assert sorted(w.designed(2)) == sorted(w.rounds[1]["field"])
    assert w.champion() == "lab/m5" and "lab/m0" not in a.knockout_places


def test_short_rules_byes_go_to_the_best_qualifiers(w):
    # seven qualify with the same score: the one that used the fewest test runs gets the only bye
    models = {f"lab/m{i}": Model(i, runs=0 if i == 3 else 2) for i in range(7)}
    w.run(SHORT_ROUNDS, models)
    first = w.bracket[0]
    assert [b.a.model for b in first if b.how == "bye"] == ["lab/m3"]
    assert all(b.how != "bye" for r in w.bracket[1:] for b in r)


def test_short_rules_qualifying_in_every_round_a_lost_one_forfeits_the_match(w, tmp_path):
    rules = rules_file(tmp_path, "short", qualifying="all")
    w.run(SHORT_ROUNDS, field_of(4, m3=Model(3, qualifying={2: "loss"})), rules=rules)
    assert sorted({k for _, k, _, _ in w.qualifiers}) == [1, 2]  # a qualifying game in the final's design phase too
    final = w.bracket[-1][0]
    assert final.how == "forfeit" and not final.games and final.loser.model == "lab/m3"
    assert w.champion() == final.winner.model != "lab/m3"


def test_short_rules_without_qualifying_everyone_who_submits_is_in_and_byes_are_random(w, tmp_path):
    rules = rules_file(tmp_path, "short", qualifying="none")
    byes = set()
    for seed in range(8):
        a = w.run(SHORT_ROUNDS, field_of(3), rules=rules, seed=seed)
        assert not w.qualifiers and all(e.qualified for e in a.entrants.values())
        assert all("outcome" not in x for e in a.entrants.values() for x in e.entries)
        byes |= {b.a.model for b in w.bracket[0] if b.how == "bye"}
        assert w.champion() == "lab/m2"
    assert len(byes) > 1  # not always the same model


def test_short_rules_hidden_contests_are_drawn_after_the_round_is_designed(w):
    a = w.run({**SHORT_ROUNDS, "contest": ["", ""], "hidden": True}, field_of(4))
    assert a.plan.hidden and not w.qualifiers  # hidden: no qualifying
    assert [m.contest for m in a.plan.rounds] == ["", ""]  # the plan keeps them secret
    played = [c for kind, c, *_ in w.events if kind == "contest"]
    assert len(played) == 2 and len(set(played)) == 2 and w.round_contests() == [{played[0]}, {played[1]}]
    # round 2's contest was drawn only once both finalists had designed their Zooks for it
    assert w.events.index(("contest", played[1])) > max(i for i, e in enumerate(w.events) if e[0] == "design")
    assert w.champion() == "lab/m3"


def test_short_rules_the_knockout_cap_stops_calling_models(w, tmp_path, capsys):
    rules = rules_file(tmp_path, "short", max_qualifiers=4)
    a = w.run(SHORT_ROUNDS, field_of(6), rules=rules)
    assert list(a.entrants) == ["lab/m0", "lab/m1", "lab/m2", "lab/m3"]
    assert "not called: lab/m4, lab/m5" in capsys.readouterr().out
    assert len(w.rounds) == 2 and w.champion() == "lab/m3"


def test_short_rules_with_no_limit_a_big_field_gets_an_extra_random_round(w, tmp_path):
    rules = rules_file(tmp_path, "short", max_qualifiers="")
    a = w.run(SHORT_ROUNDS, field_of(9), rules=rules)
    assert [r["name"] for r in w.rounds] == ["Round 1", "Quarter-finals", "Semi-finals", "Final"]
    assert len(a.plan.rounds) == 4 and a.plan.rounds[-1].contest == "Zook Sumo"
    assert len({m.contest for m in a.plan.rounds}) == 4  # the added round isn't one already planned
    assert sum(b.how == "bye" for b in w.bracket[0]) == 7 and w.champion() == "lab/m8"


def test_short_rules_two_models_play_only_round_1(w):
    a = w.run(SHORT_ROUNDS, field_of(2))
    assert [r["name"] for r in w.rounds] == ["Final"] and len(a.plan.rounds) == 1
    assert w.designed(2) == [] and w.round_contests() == [{a.plan.rounds[0].contest}]
    assert all(g.green_zook == "Zook1" for g in w.games)


def test_a_model_that_puts_nothing_forward_for_a_round_forfeits_it(w):
    w.run(SHORT_ROUNDS, field_of(4, m3=Model(3, skip={2})))
    final = w.bracket[-1][0]
    assert final.how == "forfeit" and final.loser.model == "lab/m3"


def test_nobody_qualifies_no_knockout_but_the_session_table_is_saved(w, capsys):
    a = w.run(SHORT_ROUNDS, {f"lab/m{i}": Model(i, qualifying={1: "loss"}) for i in range(3)})
    assert not w.knockouts and not any(e.qualified for e in a.entrants.values())
    assert "0 model(s) qualified" in capsys.readouterr().out
    assert list((w.cfg.arena_dir / "Sessions").glob("*.txt"))
    assert not (w.cfg.arena_dir / showtime.HALL_OF_FAME).exists()


def test_draws_are_replayed_then_settled_by_a_coin_toss_and_a_stalled_game_is_replayed(w):
    w.draws, w.stalls = True, 1
    a = w.run(SHORT_ROUNDS, field_of(4))
    played = [b for r in w.bracket for b in r if b.games]
    assert len(played) == 3 and all(b.how == "coin toss" and len(b.games) == 3 for b in played)
    assert len(w.games) == 9 + 1  # the stalled game was played again
    for b in played:  # sides swap between games, each a new seed
        assert [g.green.model for g in b.games] == [b.a.model, b.b.model, b.a.model]
        assert len({g.seed for g in b.games}) == 3
    assert a.knockout_places[w.champion()] == 1


# --- full rules ----------------------------------------------------------------------------------

FULL_VISIBLE = {"challenge_rules": "full", "hidden": False, "contest": ["Zook Sumo", "Zook Smash", "Weakest Zook"],
                "opponent": ["Spider", "Ant", "Leapsa"]}


def test_full_rules_visible_one_zook_per_challenge_and_a_missing_one_forfeits(w):
    a = w.run(FULL_VISIBLE, field_of(8, m7=Model(7, skip={2})))
    assert not a.plan.round_by_round and not w.qualifiers and not w.designed(2)  # designed up front
    assert w.round_contests()[0] == {"Zook Sumo"} and w.round_contests()[2] == {"Weakest Zook"}
    semi = next(b for b in w.bracket[1] if "lab/m7" in (b.a.model, b.b.model))
    assert semi.how == "forfeit" and semi.loser.model == "lab/m7"  # no Zook for Zook Smash
    assert all(g.green_zook == g.red_zook == f"Zook{g.round + 1}" for g in w.games)
    assert w.champion() == strongest(w.rounds[2]["field"], w)


def test_full_rules_hidden_team_zooks_are_each_played_once(w):
    a = w.run({"challenge_rules": "full", **SHORT_ROUNDS}, field_of(8, m7=Model(7, skip={1})))
    assert a.plan.team and [m.contest for m in a.plan.rounds] == ["", "", "Zook Sumo"]
    for m in a.entrants:  # no team Zook played twice
        mine = [g.green_zook if g.green == entrant_folder(m) else g.red_zook for g in w.games
                if entrant_folder(m) in (g.green, g.red)]
        assert len(mine) == len(set(mine))
    final = w.bracket[-1][0]
    assert final.how == "forfeit" and final.loser.model == "lab/m7"  # a team of two: none left for the final
    drawn = [c for kind, c, *_ in w.events if kind == "contest"]  # announced as each round starts
    assert len(drawn) == len(set(drawn)) == 3 and drawn[-1] == "Zook Sumo"
    assert w.round_contests()[:2] == [{drawn[0]}, {drawn[1]}]
    picks = [p for _, p in a.router.asked if "Pick the Zook" in p]
    assert picks and all("only once" in p for p in picks)
    no_seeds_told(w, a)


def test_full_rules_with_fewer_models_the_early_rounds_are_skipped(w):
    w.run(FULL_VISIBLE, field_of(2))
    assert w.round_contests() == [{"Weakest Zook"}]  # only the final, the last round
    assert all(g.green_zook == "Zook3" for g in w.games)


def test_full_rules_with_more_models_an_extra_random_round_comes_first(w):
    a = w.run({**FULL_VISIBLE, "contest": ["Zook Sumo", "Zook Smash"], "opponent": ["Spider", "Ant"]}, field_of(5))
    assert len(w.rounds) == 3 and len(a.plan.rounds) == 2  # full rules don't change the plan
    extra = w.round_contests()[0]
    assert len(extra) == 1 and not extra & {"Zook Sumo", "Zook Smash"}  # drawn, not a planned one
    assert w.round_contests()[1:] == [{"Zook Sumo"}, {"Zook Smash"}]
    assert w.champion() == "lab/m4"


def test_random_contests_never_repeat_in_a_knockout(w):
    seen = set()
    for seed in range(6):
        w.run({"challenge_rules": "full", "contest": ["", "", ""], "opponent": ["", "", ""]}, field_of(8), seed=seed)
        drawn = [next(iter(c)) for c in w.round_contests()]
        assert len(drawn) == len(set(drawn)) == 3
        seen |= set(drawn)
    assert len(seen) >= 5  # the draws vary between sessions


# --- the show and the keyboard -------------------------------------------------------------------

def test_an_interactive_session_with_picks_an_exhibition_and_the_end_table(w, capsys):
    lines = ["y",  # 2 models queued for 3 rounds: carry on (only round 1 is played)
             "/list", "/next", "/next",
             "y",  # the queue is empty: run the knockout now
             "/pick m1", "",  # before the final: the audience's pick, then Enter plays
             "",  # after it
             "n",  # not into the Hall of Fame
             "/leaderboard", "/hall", "/bogus", "/exhibition m0 m1", "", "",
             "/end", ""]  # the table, then Enter quits
    a = w.run(SHORT_ROUNDS, field_of(2), auto=False, lines=lines)
    out = capsys.readouterr().out
    assert w.keyboard.left == [] and w.champion() == "lab/m1"
    assert "Your pick: lab/m1" in out and "called it!" in out and a.picks == [1, 1]
    assert "not added to the Hall of Fame" in out and "unknown command" in out
    assert len(w.knockouts) == 2 and w.rounds[-1]["name"] == "Exhibition match"
    assert a.knockout_places == {"lab/m1": 1, "lab/m0": 2}  # the exhibition doesn't change the ranking
    assert "lab/m1 wins the exhibition" in out and list((w.cfg.arena_dir / "Sessions").glob("*.txt"))


def test_showtime_trash_talk_and_interviews_go_to_both_models(w):
    a = w.run(SHORT_ROUNDS, field_of(2), show=True)
    asked = [(m, p) for m, p in a.router.asked]
    assert sum("Say something to the audience before the game" in p for _, p in asked) == 2
    assert sum("Post-match interview" in p for _, p in asked) == 2
    assert {m for m, _ in asked} == {"lab/m0", "lab/m1"}


# --- the config, as tools/arena.py reads it -----------------------------------------------------

def test_arena_main_plans_the_session_the_config_describes(w, monkeypatch, tmp_path):
    seen = []

    async def repl(self):
        seen.append(self)
    monkeypatch.setattr(Arena, "repl", repl)

    def main(arena_cfg, *argv):
        w.cfg.arena = dict(arena_cfg)
        seen.clear()
        arena_module.main(list(argv))
        return seen[0]

    a = main({"challengers": ["lab/x", "lab/y"], **SHORT_ROUNDS})
    assert a.queue == ["lab/x", "lab/y"] and a.plan.rules.name == "short" and not a.plan.hidden
    assert len(a.plan.rounds) == 3 and a.plan.current == 0
    assert Session.load(w.cfg.zoo_dir / "session.json").rounds == a.plan.rounds  # for the MCP servers
    a = main({**SHORT_ROUNDS, "hidden": True})
    assert a.plan.hidden and [m.contest for m in a.plan.rounds] == ["", "", "Zook Sumo"]
    a = main({**SHORT_ROUNDS, "challenge_rules": "full"})
    assert a.plan.team and a.plan.current is None  # full rules: hidden by default, no rounds opened
    a = main({**SHORT_ROUNDS, "challenge_rules": "full", "hidden": False})
    assert not a.plan.team and all(m.contest for m in a.plan.rounds)
    a = main(SHORT_ROUNDS, "--challenge", "full", "--challenger", "lab/z")
    assert a.plan.rules.name == "full" and a.queue == ["lab/z"]  # the command line wins
    a = main({"challenge_rules": str(rules_file(tmp_path, "short", qualifying="all")), "contest": "Zook Smash",
              "opponent": "Ant"})
    assert [(m.contest, m.opponent) for m in a.plan.rounds] == [("Zook Smash", "Ant")]
    assert a.plan.qualifying_in(1) and a.plan.rules.qualifying == "all"
    saved = a.plan.rounds
    a = main({**SHORT_ROUNDS, "challenge_rules": "full"}, "--keep-entries")
    assert a.plan.rounds == saved  # --keep-entries carries on with the saved plan
    # the show's settings: [arena] defaults, the command line over them
    a = main({**SHORT_ROUNDS, "monitor": True, "music": False})
    assert (a.position, a.fullscreen, a.play_music, a.show, a.announcer) == ("right-half", False, False, False, None)
    a = main({**SHORT_ROUNDS, "monitor": True, "music": False}, "--full-screen")
    assert (a.position, a.fullscreen) == ("none", True)  # --full-screen wins over [arena] monitor
    a = main({**SHORT_ROUNDS, "showtime": True, "music": False}, "--no-announcer", "--max-turns", "7")
    assert a.show and a.announcer is None and a.design_music is None and a.max_turns == 7
    a = main({**SHORT_ROUNDS, "showtime": True, "announcer": "lab/host"}, "--nomusic")
    assert a.announcer.model == "lab/host" and not a.play_music


# --- the real MCP server, as a model sees it -----------------------------------------------------

def _leg(around, length=1.0, **extra):
    return {"attach": {"around": around}, "size": {"length": length}, **extra}


DESIGNS = {  # two clearly different Zooks of a model's own
    "Quad": {"name": "Quad", "body": {"mesh": "Blob", "children": [_leg(45, mirror=True), _leg(135, mirror=True)]}},
    "Strider": {"name": "Strider", "body": {"mesh": "Blob", "children": [
        _leg(90, 3.0, mirror=True, children=[_leg(30, 2.5, children=[_leg(10, 2.0)])])]}},
}


@pytest.mark.install
@pytest.mark.parametrize("rules, hidden, qualifying", [
    ("full", True, None), ("full", False, None), ("short", False, "none"), ("short", True, None)])
def test_the_mcp_server_under_each_mode(install, tmp_path, rules, hidden, qualifying):
    """The bamzooki MCP server a model talks to, started as the arena starts it (on the arena's
    session file), through its engine-free tools: what get_challenge tells it, and submit_design
    (a copy of an example refused, its own two Zooks taken). No game is played."""
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, get_default_environment, stdio_client

    from bamzooki.sdk.zook import Zook

    arena, zoo = tmp_path / "Arena", tmp_path / "Arena" / "Zook Entries"
    challenge = Challenge.load(rules_file(tmp_path, rules, qualifying=qualifying) if qualifying else rules)
    session = Session.plan(challenge, ["Zook Sumo", "Zook Smash"], ["Spider", "Ant"], hidden=hidden)
    session.current = 0 if session.round_by_round else None  # as the arena opens round 1
    session.save(zoo / "session.json")
    for name, spec in DESIGNS.items():
        p = zoo / "lab_probe" / name / "1.0.zook"
        p.parent.mkdir(parents=True)
        Zook.from_spec(spec).save(p, install.zook_key)
    (zoo / "lab_probe" / "Copycat").mkdir()
    spider = next(p for p in install.example_zooks() if p.parent.name == "Spider")
    (zoo / "lab_probe" / "Copycat" / "1.0.zook").write_bytes(spider.read_bytes())

    async def server(steps):
        params = StdioServerParameters(
            command=sys.executable, args=["-m", "bamzooki.mcp_server", "--model", "lab/probe",
                                          "--session", str(zoo / "session.json")],
            cwd=str(arena), env={**get_default_environment(), "BAMZOOKI_ARENA_DIR": str(arena)})
        async with stdio_client(params) as (r, w_), ClientSession(r, w_) as s:
            await s.initialize()

            async def call(name, **args):
                return json.loads((await s.call_tool(name, args)).content[0].text)
            return await steps(s, call)

    def by_round(k):
        return {} if session.round_by_round or session.team else {"round": k}

    async def first_phase(s, call):
        tools = {t.name: t.description for t in (await s.list_tools()).tools}
        info = await call("get_challenge")
        told = json.dumps(info)
        assert "seed" not in told.lower() and all(str(m.seed) not in told for m in session.rounds)
        assert ("Zook Smash" in told) != hidden  # hidden: the contests stay secret
        if session.round_by_round:
            assert info["current_round"]["round"] == 1 and not info["current_round"]["qualifying_game"]
        assert "too close to one of the example Zooks" in (await call("submit_design", ref="Copycat",
                                                                      **by_round(1)))["error"]
        quad = await call("submit_design", ref="Quad", **by_round(1))
        assert "error" not in quad and "outcome" not in quad  # no qualifying game here
        if session.round_by_round:
            assert "already put a Zook forward for round 1" in (await call("submit_design", ref="Strider"))["error"]
        else:
            strider = await call("submit_design", ref="Strider", **by_round(2))
            assert "error" not in strider
            assert "complete" in strider["note"] if session.team else strider["still_to_put_forward"].startswith("none")
        return tools

    tools = asyncio.run(server(first_phase))
    assert {"get_challenge", "submit_design", "save_zook", "run_contest", "run_trial", "test_zook"} <= set(tools)
    assert ("team" in tools["submit_design"]) == session.team
    if session.round_by_round:  # the arena opens round 2: a new server starts the model on it, limits afresh
        session.current = 1
        session.save(zoo / "session.json")

        async def second_phase(s, call):
            info = await call("get_challenge")
            assert info["current_round"]["round"] == 2 and info["budget"]["runs_used"] == 0
            assert "error" not in await call("submit_design", ref="Strider")
        asyncio.run(server(second_phase))
