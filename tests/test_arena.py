"""The arena's agent loop: messages typed while a model works, /stop, verbose output."""

import asyncio
import json
import queue

from bamzooki.arena import Arena, Entrant, Keyboard
from bamzooki.challenge import Challenge, Session


def plan(rules="short", **kw):
    """The default session: one round of Zook Sumo against Spider."""
    return Session.plan(Challenge.load(rules), **kw)


def keyboard(*lines):
    kb = Keyboard.__new__(Keyboard)  # no stdin thread
    kb.lines, kb.busy = queue.Queue(), False
    for line in lines:
        kb.lines.put(line)
    return kb


class Router:
    """Replies with one tool call per turn until `calls` run out, then a plain answer."""

    def __init__(self, calls=1, on_call=None):
        self.calls, self.on_call, self.seen = calls, on_call, []

    async def chat(self, model, messages, tools, on_delta=None, single_tool=False):
        self.seen.append([dict(m) for m in messages])
        if self.calls:
            self.calls -= 1
            msg = {"content": "", "tool_calls": [{"id": "1", "function": {"name": "list_zooks", "arguments": "{}"}}]}
        else:
            msg = {"content": "done"}
        return {"choices": [{"message": msg}], "usage": {}}


def arena(router, kb, verbose=False):
    a = Arena.__new__(Arena)
    a.router, a.keyboard, a.verbose, a.max_turns, a.tools, a.skip = router, kb, verbose, 10, [], False
    a.ending, a.knockout_places, a.announcer, a.show = False, {}, None, False
    a.plan = plan()
    a.challenge = a.plan.rules
    a.entrant = Entrant("lab/model", messages=[{"role": "user", "content": "go"}], submitted=True)

    async def run_tool(call):
        a.entrant.messages.append({"role": "tool", "tool_call_id": "1", "content": "{}"})
        kb.lines.put("use more legs")  # typed while the tool ran
        return []

    a._run_tool = run_tool
    return a


def test_message_typed_while_working_reaches_the_model_after_its_step():
    r = Router(calls=1)
    a = arena(r, keyboard())
    asyncio.run(a.converse())
    second = r.seen[1]
    assert second[-2]["role"] == "tool"  # tool results stay right after the call
    assert second[-1]["role"] == "user" and "use more legs" in second[-1]["content"]
    assert a.keyboard.busy is False and a.keyboard.drain() == []


def test_stop_halts_after_the_current_step():
    r = Router(calls=5)
    kb = keyboard()
    a = arena(r, kb)

    async def run_tool(call):
        a.entrant.messages.append({"role": "tool", "tool_call_id": "1", "content": "{}"})
        kb.lines.put("/stop")
        return []

    a._run_tool = run_tool
    asyncio.run(a.converse())
    assert len(r.seen) == 1 and a.entrant.messages[-1]["role"] == "tool"


def test_commands_wait_and_end_of_input_is_kept():
    kb = keyboard("/leaderboard", "hi", None)
    assert kb.drain() == ["/leaderboard", "hi"] and kb.get() is None


def test_stream_is_put_back_together():
    from bamzooki.arena import _read_stream

    chunks = [
        ": OPENROUTER PROCESSING", "",
        {"choices": [{"delta": {"reasoning": "Let me "}}]},
        {"choices": [{"delta": {"reasoning": "think."}}]},
        {"choices": [{"delta": {"content": "Saving "}}]},
        {"choices": [{"delta": {"content": "it.", "tool_calls": [
            {"index": 0, "id": "c1", "function": {"name": "save_zook", "arguments": '{"spec": '}}]}}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": '{"name": "A"}}'}}]}}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 1, "id": "c2", "function": {"name": "list_zooks"}}]}}]},
        {"choices": [], "usage": {"total_tokens": 42, "cost": 0.001}},
        "[DONE]",
    ]

    async def lines():
        for c in chunks:
            if isinstance(c, dict):
                yield f"data: {json.dumps(c)}"
            else:
                yield c if c != "[DONE]" else f"data: {c}"

    seen = []
    data = asyncio.run(_read_stream(lines(), lambda kind, piece: seen.append((kind, piece))))
    msg = data["choices"][0]["message"]
    assert msg["reasoning"] == "Let me think." and msg["content"] == "Saving it."
    assert [c["function"]["name"] for c in msg["tool_calls"]] == ["save_zook", "list_zooks"]
    assert msg["tool_calls"][0]["id"] == "c1"
    assert msg["tool_calls"][0]["function"]["arguments"] == '{"spec": {"name": "A"}}'
    assert data["usage"]["total_tokens"] == 42
    assert seen[0] == ("reasoning", "Let me ") and ("content", "it.") in seen


def test_stream_error_mid_way():
    from bamzooki.arena import _read_stream

    async def lines():
        yield 'data: {"error": {"message": "provider died"}}'

    try:
        asyncio.run(_read_stream(lines(), lambda *_: None))
    except RuntimeError as e:
        assert "provider died" in str(e)
    else:
        raise AssertionError("no error raised")


def test_verbose_streams_and_doesnt_print_twice(capsys):
    class Streamer(Router):
        async def chat(self, model, messages, tools, on_delta=None, single_tool=False):
            assert on_delta is not None
            for piece in ("hel", "lo"):
                on_delta("content", piece)
            return {"choices": [{"message": {"content": "hello"}}], "usage": {}}

    a = arena(Streamer(), keyboard(), verbose=True)
    asyncio.run(a.converse())
    assert capsys.readouterr().out.count("hel") == 1


class Typist(Keyboard):
    """Types `answer` once the question has been asked (asking clears earlier typing)."""

    def __init__(self, answer):
        self.lines, self.busy, self.answer = queue.Queue(), False, answer
        self.lines.put("typed earlier")

    def drain(self):
        out = super().drain()
        self.lines.put(self.answer)
        return out


def offer(answer, qualified=2, queue_left=()):
    a = arena(Router(), Typist(answer))
    a.queue = list(queue_left)
    a.entrants = {f"m{i}": Entrant(f"m{i}", design=f"m{i}/Z@1", qualified=i < qualified) for i in range(3)}
    played = []

    async def contest():
        played.append(1)

    a.contest = contest
    asyncio.run(a.offer_contest())
    return played, a.keyboard


def test_empty_queue_offers_the_contest():
    assert offer("y")[0] == [1] and offer("Yes")[0] == [1]
    assert offer("n")[0] == [] and offer("")[0] == []


def test_no_offer_without_two_qualified_or_with_models_waiting():
    played, kb = offer("y", qualified=1)
    assert played == [] and kb.get() == "typed earlier"  # not asked: typing left alone
    played, kb = offer("y", queue_left=["next/model"])
    assert played == [] and kb.get() == "typed earlier"


def test_tool_args_null_and_bad():
    from bamzooki.arena import _tool_args

    assert _tool_args("null") == ({}, "") and _tool_args(None) == ({}, "") and _tool_args("") == ({}, "")
    assert _tool_args('{"ref": "Ant"}') == ({"ref": "Ant"}, "") and _tool_args({"a": 1}) == ({"a": 1}, "")
    assert _tool_args("[1]")[0] is None and "JSON object" in _tool_args("[1]")[1]
    assert _tool_args("{oops")[0] is None and "valid JSON" in _tool_args("{oops")[1]


def test_next_while_working_moves_on_to_the_next_challenger():
    a = arena(Router(), keyboard())
    a.queue, a.entrants, started = ["second/model", "third/model"], {}, []

    async def start(model):
        started.append(model)
        a.entrant = Entrant(model, messages=[])
        if model == "first/model":
            a.keyboard.lines.put("/next")  # typed while it was (not) playing
            await a.converse()

    a.start = start
    asyncio.run(a.call("first/model"))
    assert started == ["first/model", "second/model"] and a.queue == ["third/model"] and not a.skip


def test_a_model_that_crashes_doesnt_end_the_session():
    a = arena(Router(), keyboard())
    a.queue, a.entrants = [], {}

    async def start(model):
        raise UnboundLocalError("boom")

    a.start = start
    asyncio.run(a.call("broken/model"))  # reported, not raised
    assert a.keyboard.busy is False


def test_parallel_calls_rewritten_one_per_turn():
    from bamzooki.arena import _one_call_per_turn

    call = lambda i: {"id": f"c{i}", "function": {"name": "list_zooks", "arguments": "null"}}  # noqa: E731
    msgs = [{"role": "user", "content": "go"},
            {"role": "assistant", "content": "looking", "tool_calls": [call(1), call(2)]},
            {"role": "tool", "tool_call_id": "c1", "content": "r1"},
            {"role": "tool", "tool_call_id": "c2", "content": "r2"},
            {"role": "user", "content": "images"}]
    _one_call_per_turn(msgs)
    assert [m["role"] for m in msgs] == ["user", "assistant", "tool", "assistant", "tool", "user"]
    assert [m["content"] for m in msgs[2::2][:2]] == ["r1", "r2"] and msgs[1]["content"] == "looking"
    assert all(len(m["tool_calls"]) == 1 for m in msgs if m["role"] == "assistant")


def test_single_tool_provider_error_is_handled():
    class Picky(Router):
        """Refuses history with parallel calls; returns several calls when allowed to."""

        async def chat(self, model, messages, tools, on_delta=None, single_tool=False):
            self.seen.append(single_tool)
            if any(len(m.get("tool_calls") or []) > 1 for m in messages):
                raise RuntimeError('OpenRouter 200: {"message": "This model only supports single tool-calls at once!"}')
            if len(self.seen) in (1, 3):  # 2 = the refused request, 4 = done
                calls = [{"id": f"c{len(self.seen)}{i}", "function": {"name": "list_zooks", "arguments": "null"}}
                         for i in range(3)]
                return {"choices": [{"message": {"content": "", "tool_calls": calls}}], "usage": {}}
            return {"choices": [{"message": {"content": "done"}}], "usage": {}}

    r = Picky()
    a = arena(r, keyboard())
    ran = []

    async def run_tool(call):
        ran.append(call["id"])
        a.entrant.messages.append({"role": "tool", "tool_call_id": call["id"], "content": "{}"})
        return []

    a._run_tool = run_tool
    asyncio.run(a.converse())
    assert a.entrant.single_tool and r.seen == [False, False, True, True]
    assert ran == ["c10", "c11", "c12", "c30"]  # after the switch only the first of several calls runs
    assert a.entrant.messages[-1]["content"] == "done"


def test_stop_skips_the_rest_of_a_batch_of_calls():
    class Batch(Router):
        async def chat(self, model, messages, tools, on_delta=None, single_tool=False):
            self.seen.append(1)
            calls = [{"id": f"c{i}", "function": {"name": "list_zooks", "arguments": "{}"}} for i in range(4)]
            return {"choices": [{"message": {"content": "", "tool_calls": calls}}], "usage": {}}

    r, kb = Batch(), keyboard()
    a = arena(r, kb)
    ran = []

    async def run_tool(call):
        ran.append(call["id"])
        a.entrant.messages.append({"role": "tool", "tool_call_id": call["id"], "content": "{}"})
        kb.lines.put("/next")  # typed during the first call
        return []

    a._run_tool = run_tool
    asyncio.run(a.converse())
    assert ran == ["c0"] and len(r.seen) == 1 and a.skip
    tools = [m for m in a.entrant.messages if m["role"] == "tool"]
    assert [t["tool_call_id"] for t in tools] == ["c0", "c1", "c2", "c3"]  # every call still answered
    assert "not run" in tools[-1]["content"]


def test_pre_match_comment_is_one_line():
    from bamzooki.arena import _one_line

    assert _one_line("\n**Crusher will flatten you!**\nSecond line.\n- a list") == "Crusher will flatten you!"
    long = _one_line(" ".join(["boast"] * 100), limit=40)
    assert long.endswith("...") and len(long) <= 43 and "\n" not in long


def test_find_seat_by_model_or_zook_name():
    from bamzooki.arena import _find_seat
    from bamzooki.tournament import Seat

    a, b = Seat("openai/gpt-5", "openai_gpt-5/Crusher@3"), Seat("anthropic/claude-sonnet-4.5", "x/Stomper@1")
    seats = [a, b]
    assert _find_seat(seats, "gpt-5") is a and _find_seat(seats, "openai/gpt-5") is a
    assert _find_seat(seats, "STOMPER") is b and _find_seat(seats, "claude") is b and _find_seat(seats, "cru") is a
    assert _find_seat(seats, "sonnet") is b  # any part of a name
    assert _find_seat(seats, "nobody") is None and _find_seat(seats, "") is None
    assert _find_seat(seats, "t") is None  # starts neither, inside both: ambiguous


class Talker(Router):
    """Answers every prompt with two lines naming the model."""

    async def chat(self, model, messages, tools, on_delta=None, single_tool=False, tool_choice="auto"):
        self.seen.append((model, tool_choice, messages[-1]["content"]))
        return {"choices": [{"message": {"content": f"{model} line one\nline two"}}], "usage": {}}


def contest_arena(*typed, show=True):
    from bamzooki.tournament import Seat

    r = Talker()
    a = arena(r, keyboard(*typed))
    a.show = show
    seats = [Seat("lab/alpha", "lab_alpha/Crusher@1"), Seat("lab/beta", "lab_beta/Stomper@2")]
    a.entrants = {s.model: Entrant(s.model, messages=[]) for s in seats}
    return a, r, seats


def test_match_talk_routes_messages_and_enter_goes_on():
    a, r, seats = contest_arena("/stomper nice legs", "good luck both", "/nobody hi", None)  # None: input ends
    asyncio.run(a.match_talk(seats, "about to play", "to start the game"))
    assert [m for m, *_ in r.seen] == ["lab/beta", "lab/alpha", "lab/beta"]  # /stomper, then both
    assert all(choice == "none" for _, choice, _ in r.seen)
    assert "nice legs" in r.seen[0][2] and "about to play" in r.seen[0][2]
    reply = a.entrants["lab/beta"].messages[1]["content"]
    assert reply == "lab/beta line one\nline two"  # replies to the audience aren't cut


def test_remark_one_line_for_the_pre_match_comment():
    a, r, seats = contest_arena()
    assert asyncio.run(a.remark(seats[0], "boast", one_line=True)) == "lab/alpha line one"


def test_previous_entries_are_archived(tmp_path):
    from types import SimpleNamespace

    zoo = tmp_path / "Zook Entries"
    (zoo / "openai_gpt-5" / "Crusher").mkdir(parents=True)
    (zoo / "openai_gpt-5" / "challenge-short.json").write_text("{}")
    a = Arena.__new__(Arena)
    a.cfg, a.arena_dir = SimpleNamespace(zoo_dir=zoo), tmp_path
    dest = a.archive_entries()
    assert dest.parent.parent == tmp_path / "Archive" and dest.name == "Zook Entries"
    assert (dest / "openai_gpt-5" / "challenge-short.json").exists() and not any(zoo.iterdir())
    assert a.archive_entries() is None  # nothing left to archive


def test_presentation_saved_once_with_a_header(tmp_path):
    from types import SimpleNamespace

    a = arena(Talker(), keyboard())
    a.cfg = SimpleNamespace(zoo_dir=tmp_path)
    a.entrant = e = Entrant("lab/alpha", messages=[{"role": "user", "content": "present"}], tokens=1234, cost=0.0123)
    a._took(e, {"design": "lab_alpha/Crusher@3", "round": 1, "outcome": "win", "score": 1.0, "status": "finished",
                "game": {"contest": "Zook Sumo", "opponent": "Spider"}, "video": "C:/v/Qualifying.mp4"})
    assert e.submitted and e.qualified
    asyncio.run(a.converse())  # the model's answer: "lab/alpha line one\nline two"
    path = tmp_path / "lab_alpha" / f"Presentation - {a.challenge.name}.txt"
    text = path.read_text(encoding="utf-8")
    assert "Put forward:     round 1 (Zook Sumo): Crusher@3, WIN" in text and "Knockout:        qualified" in text
    assert "1,234 / $0.0123" in text and ".mp4" not in text  # no video links
    assert text.endswith("lab/alpha line one\nline two\n") and e.presented
    path.write_text("kept")
    e.messages.append({"role": "user", "content": "a question afterwards"})
    asyncio.run(a.converse())
    assert path.read_text() == "kept"  # later answers don't replace the presentation


def test_knockout_videos_copied_for_each_competitor(tmp_path):
    from bamzooki.arena import _keep_video

    src = tmp_path / "game.mp4"
    src.write_bytes(b"mp4")
    _keep_video(src, tmp_path / "lab_alpha" / "Videos", "Knockout Final game 1 - t - Zook Sumo - A vs B (lab/beta) - seed 3")
    out = tmp_path / "lab_alpha" / "Videos" / "Knockout Final game 1 - t - Zook Sumo - A vs B (lab_beta) - seed 3.mp4"
    assert out.read_bytes() == b"mp4"


def test_end_summary_table(tmp_path, capsys):
    from types import SimpleNamespace

    a = arena(Talker(), keyboard())
    a.cfg, a.arena_dir = SimpleNamespace(zoo_dir=tmp_path / "zoo"), tmp_path
    a.entrants = {m: Entrant(m, tokens=t, cost=c) for m, t, c in
                  (("lab/alpha", 120_000, 0.5), ("lab/beta", 8_000, 0.0123), ("lab/gamma", 500, 0.0001))}
    game = {"contest": "Zook Sumo", "opponent": "Spider"}
    a._took(a.entrants["lab/alpha"], {"design": "lab_alpha/Crusher@3", "round": 1, "game": game, "outcome": "win",
                                      "score": 1.0, "status": "finished"})
    a._took(a.entrants["lab/beta"], {"design": "lab_beta/Stomper@1", "round": 1, "game": game, "outcome": "draw",
                                     "score": 0.5, "status": "timeout"})
    a.knockout_places = {"lab/alpha": 1}
    path = a.summary()
    text = path.read_text(encoding="utf-8")
    assert path.parent == tmp_path / "Sessions"
    for want in ("round 1 (Zook Sumo): Crusher@3, WIN", "DRAW at the time limit", "not submitted", "1st", "120,000",
                 "$0.5000", "1 qualified", "128,500", "$0.5124"):
        assert want in text, want
    widths = {len(line) for line in text.splitlines()[2:] if line}
    assert len(widths) == 1  # every row of the table lines up
    assert "TOTAL" in capsys.readouterr().out


def test_places_and_end_while_working():
    from bamzooki.arena import _place

    places = {"a": 1, "b": 2, "c": 3, "d": 3}
    assert [_place(places, m) for m in "abcdx"] == ["1st", "2nd", "=3rd", "=3rd", "-"]
    a = arena(Router(calls=5), keyboard("/end"))
    asyncio.run(a.converse())
    assert a.ending and len(a.router.seen) == 0  # halted before its next request


def test_verbose_shows_everything():
    long = " ".join(["w"] * 150)
    assert arena(Router(), keyboard(), verbose=True)._clip(long) == long
    assert arena(Router(), keyboard())._clip(long).endswith("[+50 words]")


def bout_of(seats, stats="finished, 9 s simulated | decided by fall"):
    from bamzooki.tournament import Bout, Game

    a, b = seats
    return Bout(a, b, games=[Game(a, b, 5, "finished", a, stats)], winner=a, how="won")


def fake_stills(monkeypatch, made):
    from bamzooki import showtime

    def stills(video, finish, out, lead=1.0):
        out.write_bytes(b"\x89PNG")
        made.append((video, finish, out))
        return out
    monkeypatch.setattr(showtime, "end_of_game_stills", stills)


def test_post_match_interviews_see_the_end_and_the_stills_are_deleted(tmp_path, monkeypatch):
    from bamzooki.announcer import Announcer

    made = []
    fake_stills(monkeypatch, made)
    a, r, seats = contest_arena()
    a.show_log = tmp_path / "show.md"
    a.entrants["lab/beta"].vision = False
    a.announcer = Announcer(Talker(), "host/model")
    bout = bout_of(seats)
    asyncio.run(a.post_match("Zook Sumo", "Final", bout, "alpha beat beta", (tmp_path / "g.mp4", 4.2), tmp_path))
    assert made and made[0][:2] == (tmp_path / "g.mp4", 4.2)
    assert not made[0][2].exists()  # temporary: gone after the interviews
    host = a.announcer.router.seen
    assert [m for m, *_ in host] == ["host/model"] * 3  # the call, then a question for each
    assert [m for m, *_ in r.seen] == ["lab/alpha", "lab/beta"]  # winner first
    winner_msg = a.entrants["lab/alpha"].messages[0]["content"]
    assert isinstance(winner_msg, list) and winner_msg[1]["type"] == "image_url"
    assert "Stills of the deciding game" in winner_msg[0]["text"] and "host/model line one" in winner_msg[0]["text"]
    loser_msg = a.entrants["lab/beta"].messages[0]["content"]
    assert isinstance(loser_msg, str) and "decided by fall" in loser_msg  # can't see: told in words
    assert "ANNOUNCER" in a.show_log.read_text(encoding="utf-8")


def test_post_match_without_announcer_asks_stock_questions(tmp_path, monkeypatch, capsys):
    fake_stills(monkeypatch, [])
    a, r, seats = contest_arena()
    a.show_log = tmp_path / "show.md"
    asyncio.run(a.post_match("Zook Sumo", "Final", bout_of(seats), "alpha beat beta", None, tmp_path))
    out = capsys.readouterr().out
    assert "alpha, what won you that game?" in out and "beta, what happened out there?" in out
    assert "attached" not in a.entrants["lab/alpha"].messages[0]["content"]  # no video, no stills


def test_preview_match_shows_the_tape_and_the_announcer_sees_both(tmp_path, monkeypatch, capsys):
    from bamzooki import showtime
    from bamzooki.announcer import Announcer

    a, r, seats = contest_arena()
    a.show_log = tmp_path / "show.md"
    a.announcer = Announcer(Talker(), "host/model")
    pics = []
    for n in ("a", "b"):
        (tmp_path / f"{n}.png").write_bytes(b"png")
    cards = [showtime.ZookCard(s.zook, s.zook_name, {"Weight": f"0.{i}5 kg"}, limbs=4 + i,
                               previews={"three_quarter": str(tmp_path / f"{n}.png")})
             for i, (s, n) in enumerate(zip(seats, "ab"))]

    def versus(left, right, out):
        pics.append((left, right))
        out.write_bytes(b"png")
        return out
    monkeypatch.setattr(showtime, "versus_card", versus)
    asyncio.run(a.preview_match("Zook Sumo", "Final", *seats, *cards, tmp_path))
    out = capsys.readouterr().out
    assert "Crusher" in out and "0.15 kg" in out and "Limbs" in out
    assert pics == [(str(tmp_path / "a.png"), str(tmp_path / "b.png"))]
    sent = a.announcer.router.seen[0][2]
    assert "Green corner: lab/alpha" in sent[0]["text"] and sent[1]["type"] == "image_url"
    assert not list(tmp_path.glob("versus-*"))  # the card is temporary too


def test_sign_off_tells_the_story(tmp_path):
    from bamzooki.announcer import Announcer

    a, r, seats = contest_arena()
    a.show_log = tmp_path / "show.md"
    a.announcer = Announcer(Talker(), "host/model")
    a._took(a.entrants["lab/alpha"], {"design": "lab_alpha/Crusher@1", "round": 1, "outcome": "win",
                                      "game": {"contest": "Zook Sumo"}})
    a.knockout_places = {"lab/alpha": 1, "lab/beta": 2}
    story = a.story()
    assert "lab/alpha: round 1 (Zook Sumo): Crusher@1, WIN, 1st in the knockout." in story
    assert "lab/beta: didn't submit, 2nd in the knockout." in story and "Champion: lab/alpha." in story
    asyncio.run(a.sign_off())
    assert "Champion: lab/alpha" in a.announcer.router.seen[0][2]


def test_pick_before_a_match():
    a, r, seats = contest_arena("/pick bogus", "/pick stomper", "go beta", None)
    pick = {}
    asyncio.run(a.match_talk(seats, "about to play", "to start the game", pick=pick))
    assert pick["seat"] is seats[1] and [m for m, *_ in r.seen] == ["lab/alpha", "lab/beta"]  # picks aren't messages
    a, r, seats = contest_arena("/pick alpha", None)
    asyncio.run(a.match_talk(seats, "just played", "for the next match"))  # no picking after a match
    assert r.seen == [] or all("/pick" not in c for *_, c in r.seen)


def test_exhibition_picks_two_submitted_zooks():
    a, r, seats = contest_arena()
    a.entrants["lab/alpha"].design, a.entrants["lab/beta"].design = "lab_alpha/Crusher@1", "lab_beta/Stomper@2"
    played = []

    async def contest(seats=None, exhibition=False):
        played.append(([s.zook_name for s in seats], exhibition))
    a.contest = contest
    asyncio.run(a.exhibition("stomper alpha"))
    assert played == [(["Stomper", "Crusher"], True)]
    for bad in ("alpha", "alpha alpha", "alpha nobody", ""):
        asyncio.run(a.exhibition(bad))
    assert len(played) == 1


def test_round_title_for_a_single_match():
    import random

    from bamzooki.tournament import Seat, knockout

    seen = []
    rounds, places = knockout([Seat("l/a", "l_a/A@1"), Seat("l/b", "l_b/B@1")],
                              lambda g, r, s: ("finished", "green", ""), lambda *_: None, random.Random(2),
                              before=lambda a, b, name: seen.append(name), round_title="Exhibition match")
    assert seen == ["Exhibition match"] and len(rounds) == 1


def test_challenge_call_names_zooks_not_sides(tmp_path):
    from types import SimpleNamespace

    from bamzooki.announcer import Announcer

    a = arena(Talker(), keyboard())
    a.cfg, a.show_log = SimpleNamespace(zoo_dir=tmp_path), tmp_path / "show.md"
    a.announcer = Announcer(Talker(), "host/model")
    e = a.entrant
    e.to_call = {"design": "lab_model/IronHalo@1", "outcome": "loss", "status": "finished",
                 "game": {"contest": "Zook Sumo", "opponent": "Spider", "side": "green"},
                 "fell": {"green": True, "red": False}, "finish_after_go_s": 5.08}
    asyncio.run(a._call_challenge_game(e))
    sent = a.announcer.router.seen[0][2]
    assert "fell off: IronHalo" in sent and "fell off: green" not in sent
    assert "won't be in it" in sent and e.to_call is None


def test_hall_of_fame_only_on_yes():
    for typed, want in (("y", True), ("YES", True), ("", False), ("n", False), (None, False)):
        a = arena(Router(), Typist(typed))
        assert asyncio.run(a.confirm("Add it? [y/N] ")) is want, typed
    a = arena(Router(), None)
    assert asyncio.run(a.confirm("Add it? [y/N] ")) is False  # no keyboard: never recorded


def test_without_showtime_no_extra_model_calls(tmp_path, monkeypatch):
    from bamzooki import showtime

    made = []
    fake_stills(monkeypatch, made)
    a, r, seats = contest_arena(show=False)
    a.show_log = tmp_path / "show.md"
    cards = {s.model: showtime.ZookCard(s.zook, s.zook_name) for s in seats}
    asyncio.run(a.trash_talk("Zook Sumo knockout", "Final", *seats, cards))
    asyncio.run(a.post_match("Zook Sumo knockout", "Final", bout_of(seats), "alpha beat beta",
                             (tmp_path / "g.mp4", 4.2), tmp_path))
    assert r.seen == [] and made == []  # no lines, no interviews, no stills
    a.keyboard.lines.put("/beta good luck")
    a.keyboard.lines.put(None)  # then input ends (Enter)
    asyncio.run(a.match_talk(seats, "about to play", "to start the game"))
    assert [m for m, *_ in r.seen] == ["lab/beta"]  # talking to a model works without the show


def test_trash_talk_with_showtime_shows_the_opponent(tmp_path):
    from bamzooki import showtime

    a, r, seats = contest_arena()
    png = tmp_path / "p.png"
    png.write_bytes(b"png")
    cards = {s.model: showtime.ZookCard(s.zook, s.zook_name, previews={"three_quarter": str(png)}) for s in seats}
    asyncio.run(a.trash_talk("Zook Sumo knockout", "Final", *seats, cards))
    assert [m for m, *_ in r.seen] == ["lab/alpha", "lab/beta"]
    first = a.entrants["lab/alpha"].messages[0]["content"]
    assert isinstance(first, list) and "Pictures of Stomper" in first[0]["text"]
    assert "has just said" in a.entrants["lab/beta"].messages[0]["content"][0]["text"]


def test_announcer_only_with_showtime(tmp_path, monkeypatch):
    import bamzooki.arena as A

    monkeypatch.setattr(A, "load_api_key", lambda: "test-key")
    monkeypatch.setenv("BAMZOOKI_ARENA_DIR", str(tmp_path))
    plain = A.Arena(plan(), [], announcer="lab/host", speak=True)
    assert plain.show is False and plain.announcer is None and plain.voice is None
    show = A.Arena(plan(), [], show=True, announcer="lab/host")
    assert show.announcer.model == "lab/host"
    assert A.Arena(plan(), [], show=True, announcer=None).announcer is None  # --no-announcer


def test_victory_speech_only_with_showtime():
    for show in (False, True):
        a, r, seats = contest_arena(show=show)

        async def connect(model):
            pass
        a._connect = connect
        asyncio.run(a.crown(seats[0], "Zook Sumo", ["beat lab/beta (Stomper) in 1 game(s)"], "1. lab/alpha"))
        e = a.entrants["lab/alpha"]
        assert a.entrant is e and "has just WON" in e.messages[0]["content"]  # it knows, ready for questions
        if show:
            assert "victory speech" in e.messages[0]["content"] and len(r.seen) == 1
        else:
            assert "victory speech" not in e.messages[0]["content"] and r.seen == []  # no call made


def test_challenger_header_names_the_game_and_map(monkeypatch):
    from types import SimpleNamespace

    from bamzooki import arena, install

    a = arena.Arena.__new__(arena.Arena)

    def no_install():
        raise FileNotFoundError("no install")

    monkeypatch.setattr(install.Install, "discover", staticmethod(no_install))
    assert a._game_and_map("Zook Sumo") == "  |  game: Zook Sumo"
    a.plan = plan(contests=["Zook Sumo", "Zook Smash"], opponents=["Spider", "Ant"])
    assert a._rounds_line() == "  |  1. Zook Sumo v Spider  2. Zook Smash v Ant"
    a.plan = plan("full")
    assert a._rounds_line() == "  |  1 round(s), contests secret"
    assert arena._spaced("TableSumo") == "Table Sumo"


def test_showcase_runs_during_the_presentation_until_stopped(monkeypatch):
    import threading
    from types import SimpleNamespace

    from bamzooki import arena, runner
    from bamzooki.sdk import store

    started, stopped = threading.Event(), threading.Event()

    class FakeEngine:
        install, root = None, arena.Path(".")

        def showcase(self, path, name, cancel, **kw):
            started.set()
            self.kw = kw
            cancel.wait(5)
            stopped.set()

    class FakeZoo:
        def __init__(self, *a, **k):
            pass

        def resolve(self, ref):
            return SimpleNamespace(path=arena.Path("z.zook"), name="Crusher")

    monkeypatch.setattr(runner, "Engine", FakeEngine)
    monkeypatch.setattr(store, "Zoo", FakeZoo)
    a = arena.Arena.__new__(arena.Arena)
    a.show, a.position, a.view_fit, a.fullscreen = False, "right-half", (4 / 3, 944), False
    e = SimpleNamespace(design="lab_model/Crusher@2")
    a._showcase_start(e)
    assert not started.is_set()  # only with the show
    a._showcase_stop()  # nothing running: fine
    a.show = True
    a._showcase_start(e)
    assert started.wait(2) and not stopped.is_set()
    a._showcase_stop()  # /next
    assert stopped.is_set() and a._showcase is None


def test_auto_answers_the_shows_questions_itself():
    import asyncio

    from bamzooki import arena

    a = arena.Arena.__new__(arena.Arena)
    a.auto, a.keyboard, a.voice = True, None, None

    async def no_pause(extra=0.0):
        pass

    a._settle = no_pause
    assert asyncio.run(a.confirm("Run the knockout contest now? [Y/N] ")) is True
    asyncio.run(a.match_talk([], "between games", "to go on"))  # returns without waiting for Enter
    a.auto = False
    assert asyncio.run(a.confirm("Add it to the Hall of Fame? [y/N] ")) is False  # no keyboard: no


class Picker(Talker):
    """Names a Zook when asked to pick one."""

    def __init__(self, reply):
        super().__init__()
        self.reply = reply

    async def chat(self, model, messages, tools, on_delta=None, single_tool=False, tool_choice="auto"):
        self.seen.append((model, tool_choice, messages[-1]["content"]))
        return {"choices": [{"message": {"content": self.reply}}], "usage": {}}


def test_zook_named_in_a_reply():
    from bamzooki.arena import _match_zook

    options = ["m/Crusher@1", "m/Crusher@2", "m/Stomp@1"]
    assert _match_zook("I'll go with Crusher@2.", options) == "m/Crusher@2"
    assert _match_zook("stomp, obviously", options) == "m/Stomp@1"
    assert _match_zook("the red one", options) is None


def test_visible_rounds_play_the_zook_put_forward():
    from types import SimpleNamespace

    a, r, seats = contest_arena()
    a.plan = plan(contests=["Zook Sumo", "Zook Smash"], opponents=["Spider", "Ant"])
    e = a.entrants["lab/alpha"]
    for n in (1, 2):
        a._took(e, {"design": f"lab_alpha/R{n}@1", "round": n, "game": {"contest": "x"}, "outcome": "win"})
    now = {"info": SimpleNamespace(name="Zook Smash", contest_file="CP_ZOOK_SMASH"), "slot": 2, "event": "e"}
    options = a._knockout_options(e, 2, set(), False)
    assert options == ["lab_alpha/R2@1"]
    assert asyncio.run(a.choose_zook(seats[0], seats[1], now, "Final", options)) == "lab_alpha/R2@1"
    assert r.seen == []  # no model call: it designed this one for the round


def test_a_round_without_a_zook_is_forfeited():
    a, _, _ = contest_arena()
    a.plan = plan(contests=["Zook Sumo", "Zook Smash"], opponents=["Spider", "Ant"])
    e = a.entrants["lab/alpha"]
    a._took(e, {"design": "lab_alpha/R1@1", "round": 1, "game": {"contest": "x"}, "outcome": "win"})
    assert a._knockout_options(e, 2, set(), False) == []  # nothing put forward for round 2: forfeit
    assert a._knockout_options(e, None, set(), False) == ["lab_alpha/R1@1"]  # an extra early round: any Zook
    assert a._knockout_options(e, 2, set(), True) == ["lab_alpha/R1@1"]  # an exhibition: any Zook


def test_short_rules_qualify_in_round_1_and_a_later_lost_qualifier_forfeits(tmp_path):
    a, _, _ = contest_arena()
    rules = tmp_path / "all.toml"
    rules.write_text(Challenge.load("short").path.read_text(encoding="utf-8").replace('qualifying = "first"',
                                                                                     'qualifying = "all"'))
    a.plan = Session.plan(Challenge.load(rules), ["Zook Sumo", "Zook Smash"], ["Spider", "Ant"])
    e = a.entrants["lab/alpha"]
    e.qualified = False
    a._took(e, {"design": "lab_alpha/R2@1", "round": 2, "outcome": "win"})
    assert not e.qualified  # only round 1 gets a model in
    a._took(e, {"design": "lab_alpha/R1@1", "round": 1, "outcome": "win"})
    assert e.qualified
    beta = a.entrants["lab/beta"]
    a._took(beta, {"design": "lab_beta/S2@1", "round": 2, "outcome": "loss"})
    assert a._knockout_options(beta, 2, set(), False) == [] and "qualifying" in a._forfeit_reason(beta, 2)
    assert a._knockout_options(e, 2, set(), False) == ["lab_alpha/R2@1"]


def test_the_knockout_field_is_capped_and_seeded(tmp_path):
    from types import SimpleNamespace

    a, _, _ = contest_arena()
    a.cfg = SimpleNamespace(zoo_dir=tmp_path)
    a.plan = plan()
    assert a.cap == 8 and not a._field_full()
    for i in range(8):
        a.entrants[f"lab/m{i}"] = Entrant(f"lab/m{i}", qualified=True)
    assert a._field_full()
    weak, strong = Entrant("lab/weak", entries=[{"round": 1, "score": 0.5}]), Entrant("lab/strong", entries=[{"round": 1, "score": 1.0}])
    assert a._seeding([weak, strong]) == [strong, weak]  # the best qualifiers first (they get the byes)


def test_the_queue_is_checked_against_the_rounds():
    a, _, _ = contest_arena()
    a.plan = plan(contests=["Zook Sumo", "Zook Smash", "Zookball Red To Score"], opponents=["Spider", "Ant", "Ant"])
    a.queue = [f"lab/m{i}" for i in range(8)]
    assert a.rounds_check() == (False, [])  # 8 models, 3 rounds
    a.queue = a.queue[:6]
    mismatch, lines = a.rounds_check()
    assert not mismatch and "2 model(s) will get a bye" in lines[0]
    a.queue = a.queue[:4]
    mismatch, lines = a.rounds_check()
    assert mismatch and "1 round(s) will be skipped: round 2 (Zook Smash)" in lines[1]  # short: round 1 and the final stay
    a.queue = [f"lab/m{i}" for i in range(12)]
    mismatch, lines = a.rounds_check()
    assert not mismatch  # capped at max_qualifiers = 8
    a.plan = plan("full", contests=["Zook Sumo", "Zook Smash"], opponents=["", ""])
    mismatch, lines = a.rounds_check()
    assert mismatch and "1 extra random round(s) will be added before round 1" in lines[1]
    a.queue = []
    assert a.rounds_check() == (False, [])  # called by hand


def test_hidden_team_picks_each_zook_once():
    from types import SimpleNamespace

    a, _, seats = contest_arena()
    a.plan = plan("full", contests=["", ""], opponents=["", ""])
    a.router = Picker("Stomp, it pushes hardest")
    e = a.entrants["lab/alpha"]
    for z in ("Crusher", "Stomp", "Hopper"):
        a._took(e, {"design": f"lab_alpha/{z}@1", "round": None})
    now = {"info": SimpleNamespace(name="Zook Sumo", contest_file="CP_ZOOK_SUMO"), "slot": 1, "event": "Zook Sumo knockout"}
    used = set()

    def choose(round_name):
        return asyncio.run(a.choose_zook(seats[0], seats[1], now, round_name,
                                         a._knockout_options(e, 1, used, False), used))

    assert choose("Semi-finals") == "lab_alpha/Stomp@1"
    asked = a.router.seen[0][2]
    assert "Zook Sumo" in asked and "Crusher@1, Stomp@1, Hopper@1" in asked and "only once" in asked
    # Stomp is used up: the same answer now falls back to the first Zook still free
    assert choose("Final") == "lab_alpha/Crusher@1"
    assert "already played: Stomp@1" in a.router.seen[1][2] and "Stomp@1," not in a.router.seen[1][2].split("play:")[1]
    choose("Final")  # the last one: no question to ask
    assert used == {"lab_alpha/Crusher@1", "lab_alpha/Stomp@1", "lab_alpha/Hopper@1"} and len(a.router.seen) == 2
    assert a._knockout_options(e, 1, used, False) == []  # the team is used up: it forfeits
    assert len(a._knockout_options(e, None, used, False)) == 3  # an extra early round: any of them
