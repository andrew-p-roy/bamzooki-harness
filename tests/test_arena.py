"""The arena's agent loop: messages typed while a model works, /stop, verbose output."""

import asyncio
import json
import queue

from bamzooki.arena import Arena, Entrant, Keyboard


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

    async def chat(self, model, messages, tools):
        self.seen.append([dict(m) for m in messages])
        if self.calls:
            self.calls -= 1
            msg = {"content": "", "tool_calls": [{"id": "1", "function": {"name": "list_zooks", "arguments": "{}"}}]}
        else:
            msg = {"content": "done"}
        return {"choices": [{"message": msg}], "usage": {}}


def arena(router, kb, verbose=False):
    a = Arena.__new__(Arena)
    a.router, a.keyboard, a.verbose, a.max_turns, a.tools = router, kb, verbose, 10, []
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
        async def chat(self, model, messages, tools, on_delta=None):
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


def test_verbose_shows_everything():
    long = " ".join(["w"] * 150)
    assert arena(Router(), keyboard(), verbose=True)._clip(long) == long
    assert arena(Router(), keyboard())._clip(long).endswith("[+50 words]")
