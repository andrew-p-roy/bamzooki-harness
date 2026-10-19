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
    assert c.runs == 20 and c.submissions == 1 and set(c.counts) == {"run_contest", "run_trial"}
    assert (c.evaluation.contest, c.evaluation.opponent, c.evaluation.seed) == ("Zook Sumo", "Spider", 1)
    assert c.describe()["evaluation_game"]["side"] == "green"


def test_evaluation_is_one_game(challenge, tmp_path):
    assert (challenge.evaluation.seed, challenge.evaluation.side) == (2, "red")
    bad = tmp_path / "bad.toml"
    bad.write_text('name = "b"\n[[evaluation]]\ncontest = "A"\nopponent = "B"\n'
                   '[[evaluation]]\ncontest = "C"\nopponent = "D"\n')
    with pytest.raises(ValueError, match="single game"):
        Challenge.load(bad)


def test_budget_counts_and_persists(challenge, tmp_path):
    st = ChallengeState(challenge, tmp_path / "m")
    assert st.budget()["runs_left"] == 2 and st.check("run_trial") is None
    st.spend("run_trial", "a")
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
    s.model = model
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
