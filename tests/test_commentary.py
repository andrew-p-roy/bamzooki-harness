"""Live commentary pieces that don't need the engine or the API: reading the driver log, the brief."""

from bamzooki.commentary import GameLog, latest_frame

LOG = """   0.003 contestant 1 = green (agent 2): a.zook
   0.003 contestant 2 = red (agent 3): b.zook
   2.008 camera fitted to width 944
   6.654 go at sim 4.0
   7.700 state 5.00 1:-4.20,2.00,0.00 2:4.20,2.00,0.00
   8.700 state 6.00 1:-1.00,2.00,0.00 2:1.00,2.00,0.50
   9.100 report contact at sim 6.4
"""

TIME_UP = "  130.0 time limit reached in real time at sim 124.2\n"


def test_game_log_reads_colours_go_states_and_reports(tmp_path):
    p = tmp_path / "driver_log.txt"
    p.write_text(LOG.splitlines(keepends=True)[0] + LOG.splitlines(keepends=True)[1], encoding="latin-1")
    log = GameLog(p)
    assert log.update() == [] and log.go is None
    p.write_text(LOG, encoding="latin-1")
    assert log.update() == [("contact", 6.4)]
    assert log.go == 4.0 and log.since_go(6.4) == 6.4 - 4.0
    assert log.colours == {"1": "green", "2": "red"}
    t, pos = log.states[-1]
    assert t == 6.0 and set(pos) == {"green", "red"}
    where = log.where()
    assert "GREEN 1.0 m from the centre (started 4.2)" in where and "apart" in where
    assert log.update() == []  # nothing new


def test_latest_frame_skips_the_one_being_written(tmp_path):
    assert latest_frame(tmp_path) is None
    for k in (1, 2, 3):
        (tmp_path / f"frame_{k:06d}.hex").write_text("x")
    assert latest_frame(tmp_path).name == "frame_000002.hex"


def test_game_log_reports_the_time_limit(tmp_path):
    p = tmp_path / "driver_log.txt"
    p.write_text(LOG + TIME_UP, encoding="latin-1")
    assert GameLog(p).update()[-1] == ("time_up", 124.2)
