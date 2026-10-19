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


def test_frame_sequence_repeats_across_gaps(tmp_path):
    from bamzooki.runner.video import frame_files, sequence

    for k in (3, 4, 7):
        (tmp_path / f"frame_{k:06d}.hex").write_text("x")
    seq = sequence(frame_files(tmp_path))
    assert [p.name for p in seq] == ["frame_000003.hex"] + ["frame_000004.hex"] * 3 + ["frame_000007.hex"]


def test_render_path_is_per_model_and_timestamped(tmp_path):
    from datetime import datetime

    from bamzooki.runner.video import render_path, slug

    p = render_path(tmp_path, "claude-opus-5-5", ["Zook Sumo", "Ant-vs-Spider", "seed1"],
                    datetime(2026, 10, 19, 18, 47, 10))
    assert p == tmp_path / "claude-opus-5-5" / "2026-10-19_18-47-10_Zook-Sumo_Ant-vs-Spider_seed1.mp4"
    assert p.parent.is_dir()
    assert slug("a/b\\c: d?") == "a-b-c-d" and slug("") == "unnamed"


def test_renders_folder_is_gitignored():
    import subprocess

    from bamzooki.config import REPO_ROOT

    r = subprocess.run(["git", "check-ignore", "-q", "renders/manual/x.mp4"], cwd=REPO_ROOT)
    assert r.returncode == 0


def test_decode_hex_frame():
    from bamzooki.runner.video import VideoError, decode_hex_frame

    w, h, rgb = decode_hex_frame("08080800" + "0002" + "0001" + "ff0000" + "00ff00" + "00")
    assert (w, h, rgb) == (2, 1, b"\xff\x00\x00\x00\xff\x00")
    with pytest.raises(VideoError):
        decode_hex_frame("08080800" + "0002" + "0002" + "ff")


def test_frames_to_mp4(tmp_path):
    import shutil

    from bamzooki.runner.video import frames_to_mp4

    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    for k, colour in [(0, "ff0000"), (1, "00ff00"), (3, "0000ff")]:
        (tmp_path / f"frame_{k:06d}.hex").write_text("08080800" + "0010" + "0010" + colour * 256)
    out = frames_to_mp4(tmp_path, tmp_path / "t.mp4", fps=25)
    assert out.stat().st_size > 0 and not list(tmp_path.glob("*.hex"))


@pytest.mark.install
def test_catalog(install):
    cat = contests.catalog(install)
    assert len(cat) >= 12
    assert cat["Zook Sumo"].players == 2 and set(cat["Zook Sumo"].colours) == {"green", "red"}
    assert contests.find("cp_zook_sumo", install).name == "Zook Sumo"


def test_track_stats_forward_is_minus_z():
    from bamzooki.runner import _track_stats

    track = [(0.0, 0, 1, 0), (3.0, 0, 1, 0), (5.0, 0, 1, -10), (7.0, 1, 1, -20)]
    m = _track_stats(track, go_time=3.0)["moved"]
    assert m["forward_units"] == 20 and m["forward_cm_per_s"] == 20.0  # 20 units * 4 cm / 4 s
    assert not m["fell"] and abs(m["drift_deg"]) < 5
    assert _track_stats([(0, 0, 0, 0)], 3.0) == {"moved": None}


def test_contact_sheet(tmp_path):
    import shutil

    from bamzooki.runner.video import contact_sheet, frames_to_mp4

    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    for k in range(50):
        (tmp_path / f"frame_{k:06d}.hex").write_text("08080800" + "0020" + "0020" + f"{k * 5:02x}0000" * 1024)
    mp4 = frames_to_mp4(tmp_path, tmp_path / "t.mp4", fps=25)
    sheet = contact_sheet(mp4, [0, 0.5, 1.0, 99], tmp_path / "s.png", width=256)
    assert sheet.stat().st_size > 0


def test_trials_table():
    from bamzooki.runner import TRIALS

    assert TRIALS["sprint"] == ("SprintEnv", 20.0) and set(TRIALS) == {"sprint", "hurdles", "lap", "block_push",
                                                                       "high_jump"}


def test_manual_text_cleanup():
    from bamzooki.manual import clean

    # headings repeat each letter three times; some paragraphs are emitted twice
    text = "MMMaaakkkiiinnnggg   yyyooouuurrr\nSome text\n\nSame para\n\nSame para\n\nEnd"
    assert clean(text) == "Making your Some text\n\nSame para\n\nEnd"


def test_arena_prunes_old_images():
    from bamzooki.arena import _prune_images

    img = lambda: {"role": "user", "content": [{"type": "text", "text": "x"}, {"type": "image_url", "image_url": {"url": "d"}}]}
    msgs = [img(), {"role": "assistant", "content": "a"}, img(), img()]
    _prune_images(msgs, keep=2)
    assert "omitted" in msgs[0]["content"][0]["text"] and msgs[2]["content"][1]["type"] == "image_url"


def test_guard_blocks_pdfs(tmp_path):
    from bamzooki.guard import check_files

    (tmp_path / "manual.pdf").write_bytes(b"%PDF")
    assert check_files([tmp_path / "manual.pdf"])


def test_stalled_runs_are_not_game_timeouts():
    from bamzooki.runner import _stalled, _wall_limit
    from bamzooki.studio import PLAYED

    assert _wall_limit(124) == 492 and "stalled" not in PLAYED and "engine_error" not in PLAYED
    assert "does not count" in _stalled(492, None) and _stalled(492, "x").endswith("\nx")


def test_arena_sees_a_submission():
    import json

    from bamzooki.arena import Arena, Entrant

    a = Arena.__new__(Arena)
    a.entrant = Entrant("some/model", False)
    a._summarise("submit_design", json.dumps({"design": "x/Bot@1", "outcome": "draw", "score": 0.5}))
    assert a.entrant.submitted
