"""Rules files, the session's rounds, run budgets, submissions and opponent visibility."""

import json
from types import SimpleNamespace

import pytest

from bamzooki.challenge import Challenge, ChallengeState, Session
from bamzooki.config import Config
from bamzooki.install import Install
from bamzooki.sdk.store import Zoo
from bamzooki.studio import Studio

TOML = """
name = "t"
title = "Test"
description = "do it"
[budget]
runs = 2
[evaluation]
seed = 2
side = "red"
"""


@pytest.fixture
def challenge(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text(TOML)
    return Challenge.load(p)


def test_rules_files():
    short, full = Challenge.load("short"), Challenge.load("full")
    assert Challenge.load().name == "short"  # the default rules
    assert (short.runs, short.trials, short.hidden, short.qualifying, short.reset_each_round) == (3, 5, False, "first", True)
    assert (full.runs, full.trials, full.hidden, full.qualifying, full.reset_each_round) == (20, 50, True, "none", False)
    assert short.max_qualifiers == 8 and full.max_qualifiers == 8
    assert short.seed is None and full.seed is None  # blank: random at the start of every challenge
    assert short.time_limit == 120 and set(short.counts) == {"run_contest"}


def test_rules_seed_and_side(challenge, tmp_path):
    assert (challenge.seed, challenge.side) == (2, "red")
    bad = tmp_path / "bad.toml"
    bad.write_text('name = "b"\n[[evaluation]]\nseed = 1\n[[evaluation]]\nseed = 2\n')
    with pytest.raises(ValueError, match="one table"):
        Challenge.load(bad)


def test_session_plan_fills_random_rounds_without_repeats():
    import random

    pool, examples = ["A", "B", "C"], ["Spider", "Ant", "Leapsa"]
    s = Session.plan(Challenge.load("short"), ["A", "", ""], ["", "", ""], contest_pool=pool,
                     opponent_pool=examples, rng=random.Random(1))
    assert s.rounds[0].contest == "A" and sorted(m.contest for m in s.rounds) == pool
    assert sorted(m.opponent for m in s.rounds) == sorted(examples) and len({m.seed for m in s.rounds}) == 3
    hidden = Session.plan(Challenge.load("full"), ["", "Zook Sumo"], [""], contest_pool=pool)
    assert [m.contest for m in hidden.rounds] == ["", "Zook Sumo"]  # picked when their knockout round starts
    assert hidden.team and hidden.shared_budget and not hidden.qualifying
    shown = Session.plan(Challenge.load("full"), hidden=False)
    assert not shown.team and shown.shared_budget and not shown.round_by_round
    blind = Session.plan(Challenge.load("short"), hidden=True)  # hidden short: still round by round, no qualifying
    assert blind.round_by_round and not blind.team and not blind.shared_budget and not blind.qualifying


def test_qualifying_settings(tmp_path):
    def rules(q):
        p = tmp_path / f"{q.strip(chr(34))}.toml"
        p.write_text(f'name = "r"\nqualifying = {q}\nmax_qualifiers = ""\n')
        return Session.plan(Challenge.load(p), ["A", "B", "C"], ["X", "Y", "Z"])

    assert [rules('"first"').qualifying_in(k) for k in (1, 2, 3)] == [True, False, False]
    assert [rules('"all"').qualifying_in(k) for k in (1, 2, 3)] == [True, True, True]
    assert not rules('"none"').qualifying and rules("true").qualifying_in(3)  # older files: true = every round
    assert rules('"none"').rules.max_qualifiers is None  # "" = no limit
    with pytest.raises(ValueError, match="qualifying"):
        rules('"sometimes"')


def test_short_rounds_fit_the_knockout():
    import random

    def fitted(total, hidden=False):
        s = Session.plan(Challenge.load("short"), ["A", "B", "C", "D"], ["W", "X", "Y", "Z"], hidden=hidden)
        changes = s.fit(total, ["A", "B", "C", "D", "E", "F"], ["W", "X", "Y", "Z"], random.Random(1))
        return [m.contest for m in s.rounds], changes

    assert fitted(4) == (["A", "B", "C", "D"], [])
    assert fitted(3)[0] == ["A", "C", "D"] and "dropped: B" in fitted(3)[1]  # round 1 and the final stay
    assert fitted(2)[0] == ["A", "D"] and fitted(1)[0] == ["A"]
    six, changes = fitted(6)
    assert six[0] == "A" and six[-3:] == ["B", "C", "D"] and set(six[1:3]) == {"E", "F"}  # random, after round 1
    assert "added after round 1: 2" in changes[0]
    assert fitted(5, hidden=True)[0] == ["A", "", "B", "C", "D"]  # hidden: drawn when the round starts
    full = Session.plan(Challenge.load("full"), ["A", "B"], ["X", "Y"])
    assert full.fit(1) == [] and len(full.rounds) == 2  # full rules: the rounds stay (early ones skipped)


def test_knockout_rounds_end_with_the_last_round():
    s = Session.plan(Challenge.load("short"), ["A", "B", "C"], ["X", "Y", "Z"])
    assert [s.knockout_round(i, 3).contest for i in range(3)] == ["A", "B", "C"]
    assert [s.knockout_round(i, 2).contest for i in range(2)] == ["B", "C"]  # fewer rounds: the first ones skipped
    assert s.knockout_round(0, 4) is None and s.knockout_round(1, 4).contest == "A"  # an extra early round: random


def test_session_saves_for_the_mcp_servers(tmp_path):
    s = Session.plan(Challenge.load("full"), ["Zook Sumo", ""], ["Spider", ""])
    s.save(tmp_path / "session.json")
    back = Session.load(tmp_path / "session.json")
    assert back.rounds == s.rounds and back.hidden and back.rules.name == "full"


def test_what_models_are_told():
    secret = Session.plan(Challenge.load("full"), ["Zook Sumo", "Zook Smash", ""], ["", "", ""])
    d = secret.describe()
    assert "SECRET" in d["how_it_works"] and "Zook Sumo" not in json.dumps(d) and d["rounds"] == 3
    assert "seed" not in json.dumps(d)
    short = Session.plan(Challenge.load("short"), ["Zook Sumo", "Zook Smash"], ["Spider", "Ant"])
    d = short.describe(current=1)
    assert d["current_round"] == {"round": 2, "of": 2, "contest": "Zook Smash", "opponent": "Ant", "side": "green",
                                  "qualifying_game": False}  # qualifying in the first round only
    assert "fresh limits each round" in d["how_it_works"] and "seed" not in json.dumps(d)
    blind = Session.plan(Challenge.load("short"), ["Zook Sumo", "Zook Smash"], ["Spider", "Ant"], hidden=True)
    d = blind.describe(current=0)
    assert "SECRET" in d["current_round"]["contest"] and "Zook Sumo" not in json.dumps(d)
    full = Session.plan(Challenge.load("full"), ["Zook Sumo", "Zook Smash"], ["Spider", "Ant"], hidden=False)
    assert "submit_design(ref, round)" in full.describe()["how_it_works"]


def test_budget_counts_and_persists(challenge, tmp_path):
    st = ChallengeState(challenge, tmp_path / "m")
    assert st.budget()["runs_left"] == 2 and st.check("run_contest") is None
    st.spend("run_contest", "a")
    st.spend("run_trial", "free: not counted")
    st.spend("save_zook", "not counted")
    assert ChallengeState(challenge, tmp_path / "m").runs_used == 1  # reloaded from disk
    st.spend("run_contest", "b")
    assert st.budget()["runs_left"] == 0 and "No test runs left" in st.check("run_contest")
    assert st.check("save_zook") is None  # uncounted tools still work
    assert "0 of 2" not in st.budget()["reminder"] and "submit_design" in st.budget()["reminder"]
    st.record_submission({"score": 1, "round": 1})
    assert len(st.submitted_for(1)) == 1 and st.submitted_for(2) == []
    st.advance()  # round by round: the next round's limits start afresh
    assert (st.round, st.runs_used, st.total_runs_used) == (1, 0, 2) and st.check("run_contest") is None
    st.reset()
    assert st.runs_used == 0 and st.submissions == [] and st.round == 0


def _studio(tmp_path, model="me") -> Studio:
    """A Studio without an engine: only what reference resolution needs."""
    ex = tmp_path / "examples"
    (ex / "Ant").mkdir(parents=True)
    (ex / "Ant" / "2.1.zook").write_bytes(b"")
    s = Studio.__new__(Studio)
    s.model = s.model_name = model
    s.zoo = Zoo(Install(root=tmp_path, config=Config(zoo_dir=tmp_path / "zoo")), examples_root=ex)
    s.challenge = s.state = s.session = None
    return s


def _submitting(tmp_path, monkeypatch, session) -> Studio:
    """A Studio that can take submissions without the game (no copy check, no qualifying games)."""
    from bamzooki import studio

    s = _studio(tmp_path)
    s.session, s.challenge = session, session.rules
    s.state = ChallengeState(session.rules, tmp_path / "zoo" / "me")
    s.install = SimpleNamespace(zook_key=b"")
    s._is_copy = lambda zook: False
    # a "Zook" is its design's name; "A" and "B" are too alike to count as different Zooks
    monkeypatch.setattr(studio.Zook, "load", staticmethod(lambda path, key: path.parent.name))
    monkeypatch.setattr(studio, "too_like", lambda zook, others: next(
        (n for n, z in others.items() if {zook, z} == {"A", "B"}), None))
    for name in ("A", "B", "C"):
        _, p = s.zoo.new_version("me", name)
        p.write_bytes(b"")
    return s


def test_hidden_challenges_take_a_team(tmp_path, monkeypatch):
    s = _submitting(tmp_path, monkeypatch, Session.plan(Challenge.load("full"), ["", ""], ["", ""]))
    assert s.submit("A")["team"] == ["me/A@1"]
    assert "already put forward" in s.submit("A")["error"]  # the same Zook twice
    assert "complete" in s.submit("C")["note"]
    assert "team is complete" in s.submit("B")["error"]
    assert "challenges" not in s.challenge_info()  # the contests stay secret


def test_visible_full_rules_take_a_zook_per_challenge(tmp_path, monkeypatch):
    s = _submitting(tmp_path, monkeypatch,
                    Session.plan(Challenge.load("full"), ["Zook Sumo", "Zook Smash"], ["Spider", "Ant"], hidden=False))
    assert "Say which challenge" in s.submit("A")["error"]
    out = s.submit("A", 2)
    assert out["game"] == {"contest": "Zook Smash"} and out["still_to_put_forward"] == [1] and "outcome" not in out
    assert "already put a Zook forward for round 2" in s.submit("B", 2)["error"]
    assert "too close" in s.submit("B", 1)["error"]  # B is a small change to A
    assert s.submit("C", 1)["still_to_put_forward"].startswith("none")


def test_short_rules_work_round_by_round_with_fresh_limits(tmp_path, monkeypatch):
    s = _submitting(tmp_path, monkeypatch,
                    Session.plan(Challenge.load("short"), ["Zook Sumo", "Zook Smash"], ["Spider", "Ant"]))
    s._qualifying_game = lambda mine, match, round_no: {"outcome": "loss", "score": 0.0}
    s.state.spend("run_contest", "x")
    assert "round 1" in s.submit("A", 2)["error"]  # in order
    out = s.submit("A")
    assert out["round"] == 1 and out["outcome"] == "loss" and "Round 2: Zook Smash against Ant" in out["next"]
    assert s.state.runs_used == 0 and s.challenge_info()["current_round"]["round"] == 2  # fresh limits
    assert s.submit("C")["next"] == "That was the last round." and "All rounds are done" in s.submit("B")["error"]


def test_the_arena_opens_each_round(tmp_path, monkeypatch):
    session = Session.plan(Challenge.load("short"), ["Zook Sumo", "Zook Smash", "Zookball"], ["Spider", "Ant", "Ant"])
    session.current = 0
    s = _submitting(tmp_path, monkeypatch, session)
    s._qualifying_game = lambda mine, match, round_no: {"outcome": "loss", "score": 0.0}
    out = s.submit("A")
    assert out["outcome"] == "loss" and "not in the knockout" in out["next"] and s.state.round == 0  # no moving on
    assert "already put a Zook forward for round 1" in s.submit("C")["error"]
    session.current = 1  # the arena opened round 2: a new MCP server syncs its state to it
    s.state.spend("run_contest", "x")
    while s.state.round < session.current:
        s.state.advance()
    assert s.state.runs_used == 0 and s.challenge_info()["current_round"]["round"] == 2
    out = s.submit("C")
    assert "outcome" not in out and "next round's design phase opens" in out["next"]  # no qualifying in round 2


def test_other_models_designs_are_hidden(tmp_path):
    s = _studio(tmp_path)
    for model in ("me", "rival"):
        _, p = s.zoo.new_version(model, "Bot")
        p.write_bytes(b"")
    assert s.zooks() == {"examples": ["Ant"], "mine": {"Bot": [1]}}
    assert s._ref("Bot").owner == "me" and s._ref("me/Bot@1").version == 1 and s._ref("Ant").owner == "examples"
    with pytest.raises(KeyError, match="not available"):
        s._ref("rival/Bot")
    with pytest.raises(KeyError):
        s._ref("rival/Bot@1")


def test_entrant_names(tmp_path):
    from bamzooki.sdk.store import entrant_folder

    assert entrant_folder("anthropic/claude-sonnet-4.5") == "anthropic_claude-sonnet-4.5"
    assert entrant_folder("claude-opus-5-5") == "claude-opus-5-5"
    for bad in ("", "examples", "x" * 121):
        with pytest.raises(ValueError):
            entrant_folder(bad)


def test_self_registration_only_for_new_names(tmp_path):
    s = _studio(tmp_path, model=None)
    s.challenge, s.state = None, None
    _, p = s.zoo.new_version("taken_model", "Bot")
    p.write_bytes(b"")
    with pytest.raises(RuntimeError, match="register_entrant"):
        s.zooks()
    assert s._ref("Ant").owner == "examples"  # examples are readable before registering
    assert "error" in s.register("taken/model")  # folder taken_model exists
    assert s.register("openai/gpt-5") == {"entrant": "openai/gpt-5", "folder": "openai_gpt-5"}
    assert s.zooks()["mine"] == {} and s.register("someone/else")["note"]


def test_videos_go_to_the_entrant_folder_with_test_numbers(tmp_path, challenge):
    from bamzooki.studio import _short

    s = _studio(tmp_path)
    s.challenge, s.state = challenge, ChallengeState(challenge, tmp_path / "zoo" / "me")
    assert s._test_label() == "Test 01 - t"
    s.state.spend("run_contest", "x")
    assert s._test_label() == "Test 02 - t"
    s.state.advance()
    assert s._test_label() == "Test 02 - t"  # numbered across rounds
    p = s._video_path("Test 02 - t - Zook Sumo - Bot@1 vs Ant")
    assert p == tmp_path / "zoo" / "me" / "Videos" / "Test 02 - t - Zook Sumo - Bot@1 vs Ant.mp4"
    p.parent.mkdir(parents=True)
    p.write_bytes(b"")
    assert s._video_path("Test 02 - t - Zook Sumo - Bot@1 vs Ant").name.endswith(" (2).mp4")
    assert s._video_path('bad: "name"?').name == "bad_ _name__.mp4"
    _, z = s.zoo.new_version("me", "Bot")
    z.write_bytes(b"")
    assert _short(s._ref("Bot")) == "Bot@1" and _short(s._ref("Ant")) == "Ant"
    s.challenge = s.state = None
    assert s._test_label() == "Run"


def test_trials_have_their_own_allowance(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text(TOML.replace("runs = 2", "runs = 2\ntrials = 2"))
    c = Challenge.load(p)
    assert c.trials == 2 and c.budget_info()["trials"] == 2
    st = ChallengeState(c, tmp_path / "m")
    st.spend("run_trial", "sprint")
    assert st.runs_left == 2 and st.trials_left == 1  # a trial doesn't use a run
    assert st.budget()["reminder"] == "2 of 2 test runs left. 1 of 2 trials left."
    st.spend("run_trial", "block_push")
    assert "No trials left" in st.check("run_trial") and st.check("run_contest") is None
    assert ChallengeState(c, tmp_path / "m").trials_used == 2  # reloaded from disk


def test_old_progress_files_have_no_trial_count(tmp_path, challenge):
    (tmp_path / "m").mkdir()
    (tmp_path / "m" / "challenge-t.json").write_text('{"challenge": "t", "runs_used": 1, "log": [], "submissions": []}')
    st = ChallengeState(challenge, tmp_path / "m")
    assert st.trials_used == 0 and st.trials_left is None and st.budget()["trials_left"] == "unlimited"


def test_standard_challenges_allow_5_and_50_trials():
    from bamzooki.config import REPO_ROOT

    assert Challenge.load(REPO_ROOT / "challenges" / "short.toml").trials == 5
    assert Challenge.load("full").trials == 50


def test_standard_challenges_have_unlimited_tests():
    for c in (Challenge.load("short"), Challenge.load("full")):
        assert c.tests == 0 and c.budget_info()["tests"] == "unlimited"


def test_tests_have_their_own_allowance(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text(TOML.replace("runs = 2", "runs = 2\ntrials = 1\ntest = 2"))
    c = Challenge.load(p)
    st = ChallengeState(c, tmp_path / "m")
    st.spend("test_zook", "free:BoxEnv: me/Z@1")
    assert st.runs_left == 2 and st.trials_left == 1 and st.tests_left == 1  # a test uses neither
    assert st.budget()["reminder"] == "2 of 2 test runs left. 1 of 1 trials left. 1 of 2 tests left."
    st.spend("test_zook", "free:BoxEnv: me/Z@1")
    assert "No tests left" in st.check("test_zook") and st.check("run_trial") is None
    assert ChallengeState(c, tmp_path / "m").tests_used == 2  # reloaded from disk
    p.write_text(TOML.replace("runs = 2", "runs = 2\ntest = 0"))
    unlimited = ChallengeState(Challenge.load(p), tmp_path / "n")
    unlimited.spend("test_zook", "x")
    assert unlimited.check("test_zook") is None and unlimited.budget()["tests_left"] == "unlimited"


def test_briefing_names_the_rules_and_how_many_zooks():
    hidden = "\n".join(Session.plan(Challenge.load("full"), ["", "", ""], ["", "", ""]).briefing())
    assert "full, with the challenges HIDDEN" in hidden and "MUST put forward 3 Zooks, each a distinctly" in hidden
    assert "SHARED by all your Zooks" in hidden and "no qualifying" in hidden and "only once" in hidden
    assert "FORFEIT" in hidden and "team Zook you haven't played yet" in hidden
    short = "\n".join(Session.plan(Challenge.load("short"), ["Zook Sumo", "Zook Smash"], ["Spider", "Ant"]).briefing())
    assert "round 2: Zook Smash, against Ant" in short and "for EACH design phase" in short
    assert "only the models still in design the next round's Zook" in short and "MUST put forward one Zook" in short
    assert "Your round 1 Zook plays that round's qualifying game" in short and "Later rounds have no qualifying" in short
    assert "FORFEIT: a knockout round you have no Zook for" in short and "At most 8 models" in short
    one = Session.plan(Challenge.load("full"), hidden=False).briefing()
    assert "There is 1 challenge round, so you MUST put forward 1 Zook." in one[0]
    assert not any("distinctly different designs" in line for line in one)  # nothing to tell apart
    told = Session.plan(Challenge.load("full"), hidden=False).describe()
    assert told["rules_of_this_session"] == one and told["zooks_required"] == 1 and "forfeit" in told["missing_zook"]
    blind = "\n".join(Session.plan(Challenge.load("short"), ["Zook Sumo", "Zook Smash"], ["", ""], hidden=True).briefing())
    assert "SECRET" in blind and "Zook Sumo" not in blind and "There is no qualifying game" in blind


def test_submissions_must_be_distinct_zooks(tmp_path, monkeypatch):
    from bamzooki.studio import DISTINCT_REFUSAL

    s = _submitting(tmp_path, monkeypatch, Session.plan(Challenge.load("full"), ["", "", ""], ["", "", ""]))
    assert s.submit("A")["team"] == ["me/A@1"]
    assert s.submit("A") == {"error": DISTINCT_REFUSAL}  # the same Zook twice
    assert s.submit("B") == {"error": DISTINCT_REFUSAL} and "Zook" in DISTINCT_REFUSAL
    assert s.submit("C")["team"] == ["me/A@1", "me/C@1"]
