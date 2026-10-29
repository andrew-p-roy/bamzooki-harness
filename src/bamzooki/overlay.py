"""The arena's broadcast overlay: a transparent 1920x1080 page for an OBS Browser Source.

    Overlay(port)          serves the page on http://127.0.0.1:<port>/ while the arena runs
    overlay.update(...)    merges into the show's state; the page redraws at once
    python -m bamzooki.overlay --demo [--port 8765]
                           steps a made-up session through every phase, to see the look in a
                           browser or in OBS without the engine or any model calls

Routes: / (the page, web/overlay.html), /state (the state as JSON) and /events (Server-Sent
Events: the whole state on connecting and after every update, so a reloaded page loses nothing).
The page takes ?preview (a dark stand-in for the game behind it, to look at in a browser) and
?once (the state read once, no live updates: for a headless screenshot).

The state (no seeds, ever: the evaluation seed stays secret even from the audience):
    phase       "idle", "design", "showcase", "game", "talk" or "crown"
    challenge   {title, rounds_line}
    challenger  {model, maker, country, tokens, cost, runs_used, runs_left, trials_used,
                 trials_left, last, submitted: [Zook names]}
    bracket     {event, total_rounds, rounds: [{name, contest, matches: [{a: {model, zook},
                 b: {...} or None (a bye), winner: model or None, how, games: [{winner_zook, status}]}]}],
                 current: [round, match] or None, champion: {model, zook} or None}
    match       {round, contest, green: {model, zook}, red: {...}, game}: the score bug

Standard library only.
"""

from __future__ import annotations

import argparse
import copy
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PAGE = Path(__file__).with_name("web") / "overlay.html"
DEFAULT_PORT = 8765
PHASES = ("idle", "design", "showcase", "game", "talk", "crown")


class Overlay:
    def __init__(self, port: int = DEFAULT_PORT, host: str = "127.0.0.1"):
        self.state: dict = {"phase": "idle", "version": 0, "challenge": None, "challenger": None,
                            "bracket": None, "match": None}
        self._changed = threading.Condition()
        self._closed = False
        overlay = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # not over the audience's terminal
                pass

            def do_GET(self):
                path = self.path.split("?", 1)[0]
                if path in ("/", "/index.html"):
                    self._send(PAGE.read_bytes(), "text/html; charset=utf-8")
                elif path == "/state":
                    self._send(json.dumps(overlay.snapshot()).encode(), "application/json")
                elif path == "/events":
                    self._events()
                else:
                    self.send_error(404)

            def _send(self, body: bytes, kind: str) -> None:
                self.send_response(200)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def _events(self) -> None:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                seen = -1
                try:
                    while not overlay._closed:
                        with overlay._changed:
                            overlay._changed.wait_for(lambda: overlay.state["version"] != seen or overlay._closed,
                                                      timeout=15)
                            state = copy.deepcopy(overlay.state)
                        if overlay._closed:
                            return
                        if state["version"] == seen:
                            self.wfile.write(b": still here\n\n")  # keeps the connection open
                        else:
                            seen = state["version"]
                            self.wfile.write(f"data: {json.dumps(state)}\n\n".encode())
                        self.wfile.flush()
                except (ConnectionError, OSError):
                    return  # the page went away

        self._server = ThreadingHTTPServer((host, port), Handler)
        self._server.daemon_threads = True
        self.url = f"http://{host}:{self._server.server_address[1]}/"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def snapshot(self) -> dict:
        with self._changed:
            return copy.deepcopy(self.state)

    def update(self, **parts) -> None:
        """Set top-level parts of the state (a dict part is merged into the one there)."""
        with self._changed:
            for key, value in parts.items():
                old = self.state.get(key)
                self.state[key] = {**old, **value} if isinstance(old, dict) and isinstance(value, dict) else value
            self.state["version"] += 1
            self._changed.notify_all()

    def edit(self, fn) -> None:
        """Change the state in place under the lock (fn(state)), e.g. a bracket's match."""
        with self._changed:
            fn(self.state)
            self.state["version"] += 1
            self._changed.notify_all()

    def close(self) -> None:
        with self._changed:
            self._closed = True
            self._changed.notify_all()
        self._server.shutdown()
        self._server.server_close()


# --- the bracket, built up from the knockout's callbacks ---------------------------------------

def side(seat) -> dict | None:
    return None if seat is None else {"model": seat.model, "zook": seat.zook_name}


def new_bracket(event: str, total_rounds: int) -> dict:
    return {"event": event, "total_rounds": total_rounds, "rounds": [], "current": None, "champion": None}


def add_round(state: dict, name: str, draw: list) -> None:
    b = state.get("bracket")
    if b is not None:
        b["current"] = None
        b["rounds"].append({"name": name, "contest": "", "matches": [
            {"a": side(a), "b": side(o), "winner": a.model if o is None else None, "how": "bye" if o is None else "",
             "games": []} for a, o in draw]})


def find_match(state: dict, a_model: str, b_model: str | None) -> tuple[int, int] | None:
    b = state.get("bracket")
    if not b or not b["rounds"]:
        return None
    r = len(b["rounds"]) - 1
    for i, m in enumerate(b["rounds"][r]["matches"]):
        if {m["a"] and m["a"]["model"], m["b"] and m["b"]["model"]} == {a_model, b_model}:
            return r, i
    return None


def set_contest(state: dict, contest: str) -> None:
    """The latest round's contest, once it's drawn."""
    b = state.get("bracket")
    if b and b["rounds"]:
        b["rounds"][-1]["contest"] = contest


def _slot(state: dict, *seats) -> dict | None:
    """The latest round's match between these seats, showing the Zooks they play in it."""
    at = find_match(state, seats[0].model, seats[1].model if seats[1] else None)
    if at is None:
        return None
    m = state["bracket"]["rounds"][at[0]]["matches"][at[1]]
    played = {s.model: s.zook_name for s in seats if s}  # the Zooks picked for this round
    for s in (m["a"], m["b"]):
        if s and s["model"] in played:
            s["zook"] = played[s["model"]]
    return m


def now_playing(state: dict, a, b) -> None:
    """The match about to be played: highlighted, with the Zooks each side picked."""
    if _slot(state, a, b) is not None:
        state["bracket"]["current"] = list(find_match(state, a.model, b.model))


def match_result(state: dict, bout) -> None:
    """A decided bout into its bracket slot: the Zooks it played, the winner, how, the games."""
    m = _slot(state, bout.a, bout.b)
    if m is None:
        return
    m["winner"] = bout.winner.model if bout.winner else None
    m["how"] = bout.how
    m["games"] = [{"winner_zook": g.winner.zook_name if g.winner else None, "status": g.status} for g in bout.games]


# --- the demo ----------------------------------------------------------------------------------

def demo(overlay: Overlay, step: float = 4.0, loop: bool = True) -> None:
    """A made-up session through every phase (no engine, no model calls)."""
    from .tournament import Bout, Game, Seat

    def wait():
        if step:
            time.sleep(step)

    a, b, c, d, e = (Seat(m, f"{m.replace('/', '_')}/{z}@1") for m, z in (
        ("anthropic/claude-opus-5.5", "Bulldozer"), ("openai/gpt-6-luna-pro", "Crusher"),
        ("google/gemini-3.5-pro", "Pusher"), ("qwen/qwen3.8-max", "Scuttler"), ("x-ai/grok-5", "Lancer")))
    TITLE = "BAMZOOKi Tournament: Short Rules"  # as the arena names the show (Arena._show_title)
    while True:
        overlay.update(phase="design", challenge={"title": TITLE, "rounds_line": "1. Zook Sumo v Spider  "
                                                  "2. Zook Marbles v Ant  3. Weakest Zook v Scrabber"},
                       challenger={"model": a.model, "maker": "Anthropic", "country": "USA", "tokens": 0, "cost": 0.0,
                                   "runs_used": 0, "runs_left": 3, "trials_used": 0, "trials_left": 5,
                                   "last": "", "submitted": []}, bracket=None, match=None)
        wait()
        for n, last in enumerate(("saved Bulldozer@1: 4.1 kg, 23 parts", "Sprint: 41.2 cm (drift 3 deg, fell False)",
                                  "Zook Sumo: Bulldozer v Spider -> winner Bulldozer"), 1):
            overlay.update(challenger={"tokens": 18_000 * n, "cost": 0.031 * n, "last": last,
                                       "trials_used": min(n, 2), "trials_left": 5 - min(n, 2),
                                       "runs_used": n // 3, "runs_left": 3 - n // 3})
            wait()
        overlay.update(phase="game", match={"round": "Qualifying", "contest": "Zook Sumo", "green": side(a),
                                            "red": {"model": "examples", "zook": "Spider"}, "game": 1})
        wait()
        overlay.update(phase="showcase", challenger={"submitted": ["Bulldozer"], "last": "SUBMITTED Bulldozer: WIN"})
        wait()
        overlay.update(phase="talk", bracket=new_bracket(TITLE, 3), match=None)
        overlay.edit(lambda s: add_round(s, "Quarter-finals", [(a, None), (b, c), (d, None), (e, None)]))
        overlay.edit(lambda s: set_contest(s, "Zook Sumo"))
        wait()
        overlay.edit(lambda s: now_playing(s, b, c))
        overlay.update(phase="game", match={"round": "Quarter-final", "contest": "Zook Sumo", "green": side(b),
                                            "red": side(c), "game": 1})
        wait()
        overlay.edit(lambda s: match_result(s, Bout(b, c, [Game(b, c, 0, "finished", b)], b, "won")))
        overlay.update(phase="talk", match=None)
        wait()
        overlay.edit(lambda s: add_round(s, "Semi-finals", [(a, b), (d, e)]))
        overlay.edit(lambda s: set_contest(s, "Zook Marbles"))
        overlay.edit(lambda s: match_result(s, Bout(a, b, [Game(a, b, 0, "timeout", None),
                                                           Game(b, a, 0, "finished", a)], a, "won")))
        overlay.edit(lambda s: match_result(s, Bout(d, e, [], e, "forfeit")))
        wait()
        overlay.edit(lambda s: add_round(s, "Final", [(a, e)]))
        overlay.edit(lambda s: set_contest(s, "Weakest Zook"))
        overlay.edit(lambda s: now_playing(s, a, e))
        overlay.update(phase="game", match={"round": "Final", "contest": "Weakest Zook", "green": side(a),
                                            "red": side(e), "game": 2})
        wait()
        overlay.edit(lambda s: match_result(s, Bout(a, e, [Game(a, e, 0, "finished", a)], a, "won")))
        overlay.edit(lambda s: s["bracket"].update(champion=side(a), current=None))
        overlay.update(phase="crown", match=None)
        wait()
        if not loop:
            return


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="The arena's broadcast overlay page.")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--demo", action="store_true", help="step a made-up session through every phase")
    ap.add_argument("--step", type=float, default=4.0, help="--demo: seconds per step")
    a = ap.parse_args(argv)
    overlay = Overlay(a.port)
    print(f"overlay: {overlay.url}  (OBS: add it as a Browser Source, 1920x1080; in a browser: {overlay.url}?preview; "
          "Ctrl+C stops)")
    try:
        if a.demo:
            demo(overlay, a.step)
        else:
            threading.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        overlay.close()


if __name__ == "__main__":
    main()
