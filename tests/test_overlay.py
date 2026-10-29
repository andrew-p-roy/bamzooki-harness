"""The broadcast overlay (bamzooki.overlay) and OBS control (bamzooki.obs), without OBS or a browser."""

import json
import random
import socket
import threading
import urllib.request

import pytest

from bamzooki import overlay
from bamzooki.obs import Obs, obs_password
from bamzooki.tournament import Seat, knockout


@pytest.fixture
def ov():
    o = overlay.Overlay(port=0)
    yield o
    o.close()


def get(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=5) as r:
        return r.read()


def test_serves_the_page_and_the_state(ov):
    assert b"EventSource" in get(ov.url)
    ov.update(phase="design", challenger={"model": "lab/a", "cost": 0.01})
    ov.update(challenger={"cost": 0.02})  # a part is merged into the one there
    state = json.loads(get(ov.url + "state"))
    assert state["phase"] == "design" and state["challenger"] == {"model": "lab/a", "cost": 0.02}
    assert state["version"] == 2


def test_events_send_the_state_now_and_after_each_update(ov):
    host, port = ov.url.split("//")[1].rstrip("/").split(":")
    sock = socket.create_connection((host, int(port)), timeout=5)
    sock.sendall(b"GET /events HTTP/1.1\r\nHost: x\r\n\r\n")
    buf = b""

    def events():
        return [json.loads(line[6:]) for line in buf.decode().split("\n") if line.startswith("data: ")]

    def read_until(n):
        nonlocal buf
        while len(events()) < n:
            buf += sock.recv(65536)

    read_until(1)
    assert events()[0]["phase"] == "idle"  # the state on connecting
    threading.Timer(0.1, lambda: ov.update(phase="game", match={"game": 1})).start()
    read_until(2)
    assert events()[1]["phase"] == "game" and events()[1]["match"] == {"game": 1}
    sock.close()


def test_the_bracket_follows_the_knockout_and_has_no_seeds(ov):
    field = [Seat(f"lab/model-{i}", f"lab_model-{i}/Zook{i}@1") for i in range(5)]

    def play(green, red, seed):
        g, r = int(green.model[-1]), int(red.model[-1])
        return "finished", ("green" if g > r else "red"), ""

    def before(a, b, name):
        ov.edit(lambda s: overlay.now_playing(s, a, b))
        assert ov.snapshot()["bracket"]["current"] is not None

    ov.update(phase="talk", bracket=overlay.new_bracket("Sumo: the knockout", 3))
    rounds, places = knockout(field, play, lambda *_: None, random.Random(1), seeded=True, before=before,
                              on_draw=lambda i, name, draw: ov.edit(lambda s: overlay.add_round(s, name, draw)),
                              start_round=lambda i, t, name, f: ov.edit(lambda s: overlay.set_contest(s, "Zook Sumo")),
                              after=lambda bout: ov.edit(lambda s: overlay.match_result(s, bout)))
    b = ov.snapshot()["bracket"]
    assert [r["name"] for r in b["rounds"]] == ["Quarter-finals", "Semi-finals", "Final"]
    assert all(r["contest"] == "Zook Sumo" for r in b["rounds"])
    first = b["rounds"][0]["matches"]
    assert sum(m["how"] == "bye" for m in first) == 3 and all(m["winner"] for m in first)
    final = b["rounds"][-1]["matches"][0]
    assert final["winner"] == places[0][1].model == "lab/model-4" and final["games"][0]["winner_zook"] == "Zook4"
    assert "seed" not in json.dumps(ov.snapshot())


def test_the_demo_runs_through_every_phase(ov):
    seen = []
    real = ov.update

    def spy(**parts):
        if "phase" in parts:
            seen.append(parts["phase"])
        real(**parts)
    ov.update = spy
    overlay.demo(ov, step=0, loop=False)
    assert set(seen) == {"design", "game", "showcase", "talk", "crown"}
    b = ov.snapshot()["bracket"]
    assert b["champion"]["zook"] == "Bulldozer" and b["rounds"][1]["matches"][1]["how"] == "forfeit"
    assert "seed" not in json.dumps(ov.snapshot())


class FakeOBS:
    def __init__(self, fail_on=None, missing=()):
        self.calls, self.fail_on, self.missing = [], fail_on, set(missing)

    def __getattr__(self, what):
        def call(*args):
            if what == self.fail_on:
                raise ConnectionError("gone")
            if what == "set_current_program_scene" and args[0] in self.missing:
                raise type("OBSSDKRequestError", (Exception,), {})("no such scene")
            if what != "disconnect":
                self.calls.append((what, *args))
        return call


def test_obs_switches_scenes_by_phase_once_each():
    lines = []
    client = FakeOBS(missing={"Showcase"})
    obs = Obs({"scenes": {"talk": "Bracket", "crown": ""}, "record": True}, lines.append, client=client)
    for phase in ("design", "design", "showcase", "showcase", "talk", "crown", "game"):
        obs.scene(phase)
    obs.record(True)
    obs.record(True)
    obs.close()
    assert client.calls == [("set_current_program_scene", "Design"), ("set_current_program_scene", "Bracket"),
                            ("set_current_program_scene", "Game"), ("start_record",), ("stop_record",)]
    assert len(lines) == 1 and "Showcase" in lines[0]  # the missing scene is mentioned once, and OBS stays on


def test_obs_turns_itself_off_when_the_connection_goes():
    lines = []
    obs = Obs({}, lines.append, client=FakeOBS(fail_on="set_current_program_scene"))
    obs.scene("design")  # doesn't raise
    assert not obs.on and "off" in lines[0] and "gone" not in lines[0]
    obs.scene("game")
    assert len(lines) == 1


def test_obs_password_from_the_environment_or_obs_settings(tmp_path, monkeypatch):
    monkeypatch.delenv("OBS_WEBSOCKET_PASSWORD", raising=False)
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert obs_password() is None
    folder = tmp_path / "obs-studio" / "plugin_config" / "obs-websocket"
    folder.mkdir(parents=True)
    (folder / "config.json").write_text(json.dumps({"auth_required": True, "server_password": "pw1"}))
    assert obs_password() == "pw1"
    monkeypatch.setenv("OBS_WEBSOCKET_PASSWORD", "pw2")
    assert obs_password() == "pw2"
