"""Runner pieces that don't need the engine: judging, job serialisation, catalogue."""

import pytest

from bamzooki.codec import table
from bamzooki.runner import contests
from bamzooki.runner.contests import judge, trajectories


def _raw(reports, track=None, status="finished"):
    return {
        "status": status,
        "contestants": {
            1: {"colour": "green", "x": -4.2, "y": 2, "z": 0.0},
            2: {"colour": "red", "x": 4.2, "y": 2, "z": 0.0},
        },
        "reports": {i + 1: r for i, r in enumerate(reports)},
        "track": track or {},
    }


TRACK = {
    1: {"t": 0.0, "p": {}},  # before the Zooks exist
    2: {"t": 0.5, "p": {1: {"x": 4.5, "y": 0.7, "z": 0}, 2: {"x": -5.3, "y": 0.6, "z": 0}}},  # swapped order
    3: {"t": 9.0, "p": {1: {"x": 4.0, "y": 0.7, "z": 0}, 2: {"x": -20, "y": -24, "z": 0}}},
}


def test_finish_scoreboard_maps_contestants_to_colours():
    v = judge(_raw([{"name": "finish", "score": {1: 1, 2: 0}, "contestant": {1: 2, 2: 1}}]), players=2)
    assert v.scores == {"red": 1, "green": 0}
    assert v.winner == "red" and v.decided_by == "finish"


def test_tie_and_timeout_have_no_winner():
    v = judge(_raw([{"name": "finish", "score": {1: 1, 2: 1}, "contestant": {1: 1, 2: 2}}]), players=2)
    assert v.winner is None
    v = judge(_raw([], status="timeout"), players=2)
    assert v.winner is None and v.decided_by == "none" and v.notes


def test_single_player_scores():
    raw = _raw([{"name": "finish", "score": {1: 0}, "contestant": {1: 1}}])
    assert judge(raw, players=1).winner is None
    raw = _raw([{"name": "score", "score": {1: 3}, "contestant": {1: 1}}], status="timeout")
    v = judge(raw, players=1)
    assert v.scores == {"green": 3} and v.winner == "green" and v.decided_by == "score"


def test_one_sided_scoreboard_gets_complement():
    # Zookball: only the striker (red here) is scored; 0 = ball went out, so green wins.
    v = judge(_raw([{"name": "finish", "score": {1: 0}, "contestant": {1: 2}}]), players=2)
    assert v.scores == {"red": 0, "green": 1} and v.winner == "green"
    v = judge(_raw([{"name": "finish", "score": {1: 1}, "contestant": {1: 1}}]), players=2)
    assert v.winner == "green"


def test_trajectories_use_nearest_placeholder_and_detect_falls():
    raw = _raw([], TRACK)
    tr = trajectories(raw)
    assert tr["red"][0][1] == 4.5 and tr["green"][0][1] == -5.3
    assert judge(raw, players=2).fell == {"red": False, "green": True}


def test_job_values_serialise():
    spec = {"overrides": contests.DEFAULT_OVERRIDES, "classes": {1: "AIHarnessDriver"}, "camera_def": None}
    assert table.loads(table.dumps(spec)) == {"overrides": {"NoContact": {"timeout": "0.5"}},
                                               "classes": {1: "AIHarnessDriver"}}


def test_fill_gaps(tmp_path):
    from bamzooki.runner.video import fill_gaps

    for k, data in [(3, b"a"), (4, b"b"), (7, b"c")]:
        (tmp_path / f"frame_{k:06d}.jpg").write_bytes(data)
    assert fill_gaps(tmp_path) == 5
    assert [(tmp_path / f"frame_{k:06d}.jpg").read_bytes() for k in range(3, 8)] == [b"a", b"b", b"b", b"b", b"c"]


@pytest.mark.install
def test_catalog(install):
    cat = contests.catalog(install)
    assert len(cat) >= 12
    assert cat["Zook Sumo"].players == 2 and set(cat["Zook Sumo"].colours) == {"green", "red"}
    assert contests.find("cp_zook_sumo", install).name == "Zook Sumo"
