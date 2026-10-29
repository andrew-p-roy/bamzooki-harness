"""Every combination of the rules and config settings: the session's plan, its random draws, what the
models are told, the budgets, and the knockout's shape for every field size."""

from __future__ import annotations

import itertools
import json
import random
import re
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pytest

from bamzooki import tournament
from bamzooki.arena import Arena, Entrant
from bamzooki.challenge import Challenge, ChallengeState, Session, _fill, plan_from_config
from bamzooki.config import Config
from bamzooki.runner import contests
from bamzooki.tournament import Seat, knockout, rounds_needed

POOL = ["Dodgy Zook", "Merry Go Zook", "Weakest Zook", "Zookball Green To Score", "Zookball Red To Score",
        "Zook Marbles", "Zook Smash", "Zook Sumo", "Zook Hurdles"]
EXAMPLES = ["Ant", "Leapsa", "Scrabber", "Spider", "Twigger", "Wormthing"]
QUALIFYING = ("first", "all", "none")


def rules(tmp_path: Path, base: str, **changes) -> Challenge:
    """challenges/<base>.toml with top-level settings changed."""
    text = Challenge.load(base).path.read_text(encoding="utf-8")
    for key, value in changes.items():
        text, n = re.subn(rf"(?m)^{key} = .*?(\s+#|$)", lambda m: f"{key} = {json.dumps(value)}{m.group(1)}", text)
        assert n == 1, key
    p = tmp_path / f"{base}-{'-'.join(f'{k}{v}' for k, v in changes.items())}.toml"
    p.write_text(text, encoding="utf-8")
    return Challenge.load(p)


@pytest.fixture
def install(monkeypatch, tmp_path):
    """A stand-in install: the pack's contests (and two that aren't drawn) and the example Zooks."""
    cat = {n: contests.ContestInfo(n, "CP_" + n.upper().replace(" ", "_"), 2, ["green", "red"]) for n in POOL}
    cat["CP_ZOOK_TAG"] = contests.ContestInfo("CP_ZOOK_TAG", "CP_ZOOK_TAG", 2, ["green", "red"], in_pack=False)
    cat["China Shop"] = contests.ContestInfo("China Shop", "CP_CHINA", 1, ["green"])
    monkeypatch.setattr(contests, "catalog", lambda inst=None: dict(cat))
    zooks = [tmp_path / "Teams" / "examples" / "Creatures" / n / "1.0.zook" for n in EXAMPLES]
    return SimpleNamespace(example_zooks=lambda: zooks + [tmp_path / "Zook Kit" / "Builder" / "1.0.zook"])


def config(**arena) -> Config:
    return Config(arena_dir=Path("unused"), arena=arena)


# --- the rules files ------------------------------------------------------------------------------

def test_every_rules_setting_loads(tmp_path):
    for base, q, hidden, cap in itertools.product(("short", "full"), QUALIFYING, (True, False), (8, 4, "", 0)):
        c = rules(tmp_path, base, qualifying=q, hidden=hidden, max_qualifiers=cap)
        assert (c.name, c.qualifying, c.hidden) == (base, q, hidden)
        assert c.max_qualifiers == (cap or None)  # "" and 0: no limit
        assert c.reset_each_round == (base == "short")
    for bad in ({"qualifying": "sometimes"}, {"max_qualifiers": "lots"}):
        with pytest.raises(ValueError):
            rules(tmp_path, "short", **bad)
    p = tmp_path / "side.toml"
    p.write_text('name = "s"\n[evaluation]\nside = "blue"\n')
    with pytest.raises(ValueError, match="green or red"):
        Challenge.load(p)
    with pytest.raises(FileNotFoundError):
        Challenge.load("no-such-rules")


@pytest.mark.parametrize("counts, runs, trials, tests", [
    (["run_contest"], 3, 5, 0), (["run_contest"], 0, 0, 0), (["run_contest", "run_trial"], 2, 9, 0),
    (["run_contest", "test_zook"], 2, 1, 9), (["run_contest"], 1, 1, 1)])
def test_every_budget_combination_counts_its_own_allowance(tmp_path, counts, runs, trials, tests):
    p = tmp_path / "b.toml"
    p.write_text(f'name = "b"\n[budget]\nruns = {runs}\ncounts = {json.dumps(counts)}\ntrials = {trials}\n'
                 f'test = {tests}\n')
    c = Challenge.load(p)
    st = ChallengeState(c, tmp_path / "m")
    plays = {"run_contest": 0, "run_trial": 0, "test_zook": 0}
    for _ in range(12):
        for tool in plays:
            if st.check(tool) is None:
                st.spend(tool, "x")
                plays[tool] += 1
    used_as_runs = sum(plays[t] for t in counts)
    assert used_as_runs == (runs or 12 * len(counts))  # the runs, shared by every counted tool
    if "run_trial" not in counts:
        assert plays["run_trial"] == (trials or 12)
    if "test_zook" not in counts:
        assert plays["test_zook"] == (tests or 12)
    info = c.budget_info()
    assert ("trials" in info) == ("run_trial" not in counts) and ("tests" in info) == ("test_zook" not in counts)
    st.advance()  # a new round's limits start afresh (short rules)
    assert all(st.check(t) is None for t in plays) and st.total_runs_used == st.data["past_rounds"][0]["runs_used"]


# --- the plan: modes and what models are told ------------------------------------------------------

def test_every_mode_plans_and_briefs_consistently(tmp_path):
    for base, q, hidden, n in itertools.product(("short", "full"), QUALIFYING, (None, True, False), (1, 2, 3, 4)):
        r = rules(tmp_path, base, qualifying=q)
        cs = ["Zook Sumo", "Zook Smash", "Weakest Zook", "Zook Marbles"][:n]
        s = Session.plan(r, cs, EXAMPLES[:n], hidden, POOL, EXAMPLES, random.Random(n))
        secret = r.hidden if hidden is None else hidden
        short = base == "short"
        assert (s.hidden, s.round_by_round, s.shared_budget, s.team) == (secret, short, not short, secret and not short)
        for k in range(1, n + 1):
            assert s.qualifying_in(k) == (short and not secret and (q == "all" or (q == "first" and k == 1)))
        told = "\n".join(s.briefing())
        d = s.describe(0 if short else None)
        both = told + json.dumps(d)
        assert "seed" not in both.lower() and all(str(m.seed) not in both for m in s.rounds if m.seed > 99)
        assert ("SECRET" in told) == secret and ("HIDDEN" in told) == secret
        assert all((c in both) != secret for c in cs)  # visible: every contest named; hidden: none
        assert ("for EACH design phase" in told) == short and ("SHARED by all your Zooks" in told) == (not short)
        assert ("There is no qualifying game" in told) == (not s.qualifying)
        assert ("each later round's" in told) == s.qualifying_in(2) and ("FORFEIT" in told)
        assert ("At most 8 models" in told) and ("challenges" in d) == (not secret)
        if short:
            assert d["current_round"]["round"] == 1 and d["current_round"]["qualifying_game"] == s.qualifying
        back = Session.from_dict(json.loads(json.dumps(s.to_dict())))
        assert (back.rounds, back.hidden, back.rules.qualifying) == (s.rounds, s.hidden, q)


def test_the_qualifying_game_is_the_only_place_a_round_shows_its_side():
    s = Session.plan(Challenge.load("short"), ["Zook Sumo"], ["Spider"])
    assert s.rounds[0].public() == {"contest": "Zook Sumo", "opponent": "Spider", "side": "green"}


def test_seeds_are_random_per_round_unless_the_rules_fix_one(tmp_path):
    s = Session.plan(Challenge.load("short"), ["", "", ""], ["", "", ""], contest_pool=POOL, opponent_pool=EXAMPLES)
    assert all(1 <= m.seed <= 999_999 for m in s.rounds) and len({m.seed for m in s.rounds}) == 3
    p = tmp_path / "fixed.toml"
    p.write_text('name = "f"\n[evaluation]\nseed = 42\n')
    fixed = Session.plan(Challenge.load(p), ["A", "B"], ["X", "Y"])
    assert [m.seed for m in fixed.rounds] == [42, 42]
    fixed.fit(4, POOL, EXAMPLES)
    assert [m.seed for m in fixed.rounds] == [42] * 4  # added rounds too


# --- random selection ---------------------------------------------------------------------------

def test_random_picks_never_repeat_while_the_pool_has_others():
    rng = random.Random(0)
    for _ in range(500):
        pool = rng.sample(POOL, rng.randint(1, len(POOL)))
        picks = [rng.choice(["", "", "", rng.choice(POOL)]) for _ in range(rng.randint(1, 12))]
        fixed = {i: p for i, p in enumerate(picks) if p}
        out = _fill(picks, pool, rng)
        assert all(out[i] == p for i, p in fixed.items())  # the set rounds stay as set
        drawn = [out[i] for i in range(len(out)) if i not in fixed]
        assert set(drawn) <= set(pool)
        fresh = [p for p in pool if p not in fixed.values()]
        if len(drawn) <= len(fresh):
            assert len(set(drawn)) == len(drawn) and not set(drawn) & set(fixed.values())
        else:
            assert set(fresh) <= set(drawn)  # every fresh name is used before any repeats
    assert _fill(["", ""], [], rng) == ["", ""]  # nothing to draw from: left blank


def test_random_rounds_cover_the_pool_evenly(install):
    contest, opponent = Counter(), Counter()
    trials = 3000
    for seed in range(trials):
        s = plan_from_config(config(contest=["", "Zook Sumo"], opponent=["", "Spider"]), install=install,
                             rng=random.Random(seed))
        contest[s.rounds[0].contest] += 1
        opponent[s.rounds[0].opponent] += 1
        assert s.rounds[1].contest == "Zook Sumo" and s.rounds[1].opponent == "Spider"
    assert set(contest) == set(POOL) - {"Zook Sumo"}  # only the pack's two-player contests, never the set one
    assert set(opponent) == set(EXAMPLES) - {"Spider"}  # only the example Zooks
    for counts in (contest, opponent):
        expected = trials / len(counts)
        assert all(0.8 * expected < c < 1.2 * expected for c in counts.values()), counts


def test_the_same_seed_gives_the_same_plan_and_others_differ(install):
    cfg = config(contest=["", "", ""], opponent=["", "", ""])
    plans = [[(m.contest, m.opponent, m.seed) for m in plan_from_config(cfg, install=install,
                                                                         rng=random.Random(seed)).rounds]
             for seed in (1, 1, 2, 3, 4)]
    assert plans[0] == plans[1] and len({tuple(p) for p in plans}) == 4


def test_the_config_settings_plan_the_session(install):
    s = plan_from_config(config(contest="Zook Smash", opponent="Ant"), install=install)
    assert [(m.contest, m.opponent) for m in s.rounds] == [("Zook Smash", "Ant")]  # one round, as a string
    s = plan_from_config(config(contest=["zook sumo", "", ""], opponent=["Spider"]), install=install,
                         rng=random.Random(1))
    assert len(s.rounds) == 3 and all(m.opponent for m in s.rounds)  # the shorter array is padded, drawn
    for seed in range(200):  # a contest set by another spelling is still never drawn again
        s = plan_from_config(config(contest=["zook sumo", "", "CP_ZOOK_SMASH"]), install=install,
                             rng=random.Random(seed))
        assert s.rounds[0].contest == "Zook Sumo" and s.rounds[2].contest == "Zook Smash"
        assert len({m.contest for m in s.rounds}) == 3
    s = plan_from_config(config(), install=install)
    assert [(m.contest, m.opponent) for m in s.rounds] == [("Zook Sumo", "Spider")]  # nothing set: the default round
    s = plan_from_config(config(opponent=["lab_x/Crusher@2"]), install=install)
    assert s.rounds[0].opponent == "lab_x/Crusher@2"  # a saved design may be the Zook to beat
    s = plan_from_config(config(challenge_rules="full", contest=["", "Zook Sumo"]), install=install)
    assert s.team and [m.contest for m in s.rounds] == ["", "Zook Sumo"]  # hidden: drawn when the round starts
    s = plan_from_config(config(challenge_rules="full", contest=["", ""], hidden=False), install=install)
    assert not s.team and all(m.contest in POOL for m in s.rounds)
    s = plan_from_config(config(challenge_rules="short", hidden=True), install=install)
    assert s.hidden and s.round_by_round and not s.qualifying
    s = plan_from_config(config(challenge_rules="short"), "full", install=install)  # --challenge wins
    assert s.rules.name == "full"
    with pytest.raises(KeyError, match="known: "):
        plan_from_config(config(contest=["Zook Sumoo"]), install=install)
    with pytest.raises(ValueError, match="example Zook"):
        plan_from_config(config(opponent=["Spyder"]), install=install)


def test_fitting_short_rounds_to_every_field(tmp_path):
    names = ["A", "B", "C", "D", "E"]
    for n, total, hidden in itertools.product(range(1, 6), range(1, 7), (False, True)):
        s = Session.plan(Challenge.load("short"), names[:n], [f"O{i}" for i in range(n)], hidden=hidden)
        before = list(s.rounds)
        changes = s.fit(total, POOL, EXAMPLES, random.Random(n * 10 + total))
        after = s.rounds
        assert len(after) == total and after[0] is before[0]  # round 1 stays
        kept = [m for m in after if any(m is b for b in before)]
        added = [m for m in after if not any(m is b for b in before)]
        if total >= 2 and n >= 2:
            assert after[-1] is before[-1]  # the final stays
        elif total >= 2:  # one planned round: it's round 1, and the rest are random (not it again)
            assert len(added) == total - 1
        assert kept == [before[0]] + (before[len(before) - len(kept) + 1:] if total > 1 else [])  # the latest stay
        assert after[1:1 + len(added)] == added  # random rounds come straight after round 1
        if hidden:
            assert all(m.contest == "" for m in added)  # drawn when the round starts
        else:
            assert len({m.contest for m in after}) == total  # no contest twice
            assert all(m.contest in POOL and m.opponent in EXAMPLES for m in added)
        assert bool(changes) == (total != n)
        a = Arena.__new__(Arena)  # the operator's warning names the same rounds
        a.plan, a.queue = Session.plan(Challenge.load("short"), names[:n], [""] * n, hidden=hidden), ["m"] * (2 ** total)
        if total < n and not hidden and 2 ** total <= 8:  # (a bigger queue is capped at max_qualifiers)
            gone = {m.contest for m in before} - {m.contest for m in after}
            line = next(x for x in a.rounds_check()[1] if "skipped" in x)
            assert {c for c in names[:n] if f"({c})" in line} == gone


# --- the knockout ---------------------------------------------------------------------------------

def test_every_field_size_makes_a_proper_bracket():
    for n, seeded, seed in itertools.product(range(2, 18), (False, True), range(6)):
        rng = random.Random(seed * 100 + n)
        seats = [Seat(f"lab/m{i}", f"lab_m{i}/Z@1") for i in range(n)]
        out = {"before": [], "after": [], "rounds": []}

        def play(green, red, s):
            return rng.choice([("finished", "green", ""), ("finished", "red", ""), ("timeout", None, ""),
                               ("stalled", None, "")])

        def pick(a, b, name):
            return tuple(None if rng.random() < 0.1 else s.zook for s in (a, b))

        rounds, places = knockout(seats, play, lambda *_: None, rng, seeded=seeded, pick=pick,
                                  before=lambda a, b, r: out["before"].append((a.model, b.model)),
                                  after=lambda b: out["after"].append(b),
                                  start_round=lambda i, t, name, f: out["rounds"].append((i, t, name, len(f))))
        k = rounds_needed(n)
        byes = [b for b in rounds[0] if b.how == "bye"]
        assert len(rounds) == k and len(byes) == 2 ** k - n
        assert sorted(s.model for b in rounds[0] for s in (b.a, b.b) if s) == sorted(s.model for s in seats)
        assert [f for *_, f in out["rounds"]] == [n] + [2 ** (k - i) for i in range(1, k)]  # even after round 1
        assert all(b.b is not None for r in rounds[1:] for b in r)  # byes in the first round only
        decided = [b for r in rounds for b in r if b.how != "bye"]
        assert len(decided) == n - 1 == len(out["after"])  # every match decided once
        assert len(out["before"]) == sum(bool(b.games) for b in decided)  # only played matches are introduced
        for b in decided:
            assert b.winner in (b.a, b.b) and b.how in ("won", "coin toss", "forfeit", "both forfeit")
            assert bool(b.games) == (b.how in ("won", "coin toss")) and len(b.games) <= 3
            assert b.how != "coin toss" or all(g.winner is None for g in b.games)
        if seeded:
            assert {b.a.model for b in byes} == {s.model for s in seats[:len(byes)]}  # the best seeds
        champion = rounds[-1][0].winner.model
        assert places[0] == (1, next(s for s in seats if s.model == champion))
        assert sorted(s.model for _, s in places) == sorted(s.model for s in seats)
        out_round = {b.loser.model: r for r, bouts in enumerate(rounds) for b in bouts if b.loser}
        for place, s in places[1:]:  # knocked out later = placed higher; the same round = the same place
            assert place == 1 + sum(1 for m, r in out_round.items() if r > out_round[s.model]) + 1


def test_byes_sit_beside_a_match_and_random_ones_go_to_anyone():
    got_a_bye = Counter()
    for n in (3, 5, 6, 7, 9):
        for seed in range(40):
            seats = [Seat(f"lab/m{i}", f"lab_m{i}/Z@1") for i in range(n)]
            rounds, _ = knockout(seats, lambda g, r, s: ("finished", "green", ""), lambda *_: None,
                                 random.Random(seed))
            byes = {b.a.model for b in rounds[0] if b.how == "bye"}
            winners = {b.winner.model for b in rounds[0] if b.how != "bye"}
            mixed = [b for b in rounds[1] if len({b.a.model, b.b.model} & byes) == 1]
            assert len(mixed) == min(len(byes), len(winners))  # each bye meets a match winner where it can
            if n == 5:
                got_a_bye.update(byes)
    assert len(got_a_bye) == 5 and min(got_a_bye.values()) > 10  # unseeded byes: any entrant, at random


def test_coin_tosses_are_fair():
    a, b = Seat("lab/a", "lab_a/A@1"), Seat("lab/b", "lab_b/B@1")
    rng = random.Random(7)
    wins = Counter(tournament.bout(a, b, lambda g, r, s: ("timeout", None, ""), rng, lambda *_: None).winner.model
                   for _ in range(4000))
    assert 0.45 < wins["lab/a"] / 4000 < 0.55


def test_the_knockout_round_for_every_field_and_plan():
    for n, total in itertools.product(range(1, 6), range(1, 7)):
        s = Session.plan(Challenge.load("full"), [f"C{i}" for i in range(n)], [""] * n, hidden=False)
        got = [s.knockout_round(i, total) for i in range(total)]
        assert got[-1].contest == f"C{n - 1}"  # the final plays the last round
        assert [m.contest for m in got if m] == [f"C{i}" for i in range(max(0, n - total), n)]
        assert got.count(None) == max(0, total - n)  # extra early rounds: random contests


# --- the arena's bookkeeping in every mode --------------------------------------------------------

def test_who_qualifies_in_every_mode(tmp_path):
    for base, q, hidden in itertools.product(("short", "full"), QUALIFYING, (True, False)):
        s = Session.plan(rules(tmp_path, base, qualifying=q), ["Zook Sumo", "Zook Smash"], ["Spider", "Ant"], hidden)
        a = Arena.__new__(Arena)
        a.plan = s
        for outcome, rnd in itertools.product(("win", "draw", "loss"), (1, 2)):
            e = Entrant("lab/m")
            entry = {"design": "lab_m/Z@1", "round": None if s.team else rnd}
            if s.qualifying_in(rnd):
                entry["outcome"] = outcome
            a._took(e, entry)
            if s.round_by_round and rnd == 2:
                assert not e.qualified  # only round 1 gets a model in
            else:
                assert e.qualified == (outcome == "win" if s.qualifying else True), (base, q, hidden, outcome, rnd)


@pytest.mark.install
def test_the_repo_config_plans_under_every_rules_and_visibility(monkeypatch):
    from bamzooki.config import load_config
    from bamzooki.install import Install

    cfg = load_config()
    inst = Install.discover()
    real = {n for n, c in contests.catalog(inst).items() if c.players == 2 and c.in_pack}
    for name, hidden in itertools.product(("short", "full"), (None, True, False)):
        arena = dict(cfg.arena)
        arena.pop("hidden", None)
        if hidden is not None:
            arena["hidden"] = hidden
        s = plan_from_config(Config(arena_dir=cfg.arena_dir, arena=arena), name, install=inst, rng=random.Random(1))
        assert len(s.rounds) == max(len(arena.get("contest", [""])), len(arena.get("opponent", [""])))
        if not s.hidden:
            assert all(m.contest in real for m in s.rounds) and len({m.contest for m in s.rounds}) == len(s.rounds)
            assert all(m.opponent for m in s.rounds)
