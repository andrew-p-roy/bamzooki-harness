"""Challenge files, run budgets and opponent visibility."""

import pytest

from bamzooki.challenge import Challenge, ChallengeState
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
contest = "Zook Sumo"
opponent = "Spider"
seed = 2
side = "red"
"""


@pytest.fixture
def challenge(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text(TOML)
    return Challenge.load(p)


def test_default_challenge_loads():
    c = Challenge.load()
    assert c.runs == 20 and c.submissions == 1 and set(c.counts) == {"run_contest"}
    assert (c.evaluation.contest, c.evaluation.opponent, c.evaluation.seed) == ("Zook Sumo", "Spider", 7)
    assert c.time_limit == 120 and c.describe()["time_limit_s"] == 120
    shown = c.describe()["evaluation_game"]
    assert shown["side"] == "green" and "secret" in str(shown["seed"])  # models can't rehearse it


def test_evaluation_is_one_game(challenge, tmp_path):
    assert (challenge.evaluation.seed, challenge.evaluation.side) == (2, "red")
    bad = tmp_path / "bad.toml"
    bad.write_text('name = "b"\n[[evaluation]]\ncontest = "A"\nopponent = "B"\n'
                   '[[evaluation]]\ncontest = "C"\nopponent = "D"\n')
    with pytest.raises(ValueError, match="single game"):
        Challenge.load(bad)


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
    st.record_submission({"score": 1})
    assert st.can_submit() and st.budget()["submissions_left"] == 0
    st.reset()
    assert st.runs_used == 0 and st.can_submit() is None


def _studio(tmp_path, model="me") -> Studio:
    """A Studio without an engine: only what reference resolution needs."""
    ex = tmp_path / "examples"
    (ex / "Ant").mkdir(parents=True)
    (ex / "Ant" / "2.1.zook").write_bytes(b"")
    s = Studio.__new__(Studio)
    s.model = s.model_name = model
    s.zoo = Zoo(Install(root=tmp_path, config=Config(zoo_dir=tmp_path / "zoo")), examples_root=ex)
    s.challenge = s.state = None
    return s


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
    p = s._video_path("Test 02 - t - Zook Sumo - Bot@1 vs Ant - seed 7")
    assert p == tmp_path / "zoo" / "me" / "Videos" / "Test 02 - t - Zook Sumo - Bot@1 vs Ant - seed 7.mp4"
    p.parent.mkdir(parents=True)
    p.write_bytes(b"")
    assert s._video_path("Test 02 - t - Zook Sumo - Bot@1 vs Ant - seed 7").name.endswith(" (2).mp4")
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
    assert c.trials == 2 and c.describe()["budget"]["trials"] == 2
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
    assert Challenge.load().trials == 50
