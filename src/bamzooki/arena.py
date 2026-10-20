"""BAMZOOKi Arena: let any OpenRouter model take the challenge, then talk to it.

    python tools/arena.py                                  # challengers from bamzooki.toml [arena]
    python tools/arena.py --challenger openai/gpt-5 --challenger anthropic/claude-sonnet-4.5
    python tools/arena.py --challenge challenges/short.toml

For each challenger the runner starts the bamzooki MCP server (stdio) with the challenger's full
model name, working in the arena folder (outside the repo), and runs an agent loop over
OpenRouter's chat-completions API: the model sees only the MCP tools (plus preview/still images
when it supports vision). When it has submitted and presented its challenger Zook, you get a
prompt to talk to it. Commands:

    /next                 next challenger in the queue      /challenger <model>  a specific model
    /leaderboard          standings in this challenge       /status              current budget
    /open                 open the current entrant's latest video and stills
    /contest              knockout tournament between this session's qualified Zooks (a model
                          qualifies by WINNING its challenge game); the winner then makes a speech
    /end                  finish: a table of every model's result, runs, tokens and cost (saved to
                          Sessions/), then Enter quits
    /list                 the queue                         /quit                leave
    anything else         a message to the current model (it may use its tools to answer)

While a model is working you can still type: a message is handed to it after its current step
(the tool call or game it's in), it takes it into account and carries on; /stop halts it there,
/next halts it and calls the next challenger (for a model that's stuck or not playing).
Each run starts afresh: the previous Zook Entries move to Archive/<date_time>/ (--keep-entries
carries on with them instead).

In /contest both models comment before each match. Then, and after the game, talk to one with
/<model or Zook name> <message> or type to both; Enter plays on.

--verbose streams its reasoning and notes as it writes them and shows them, and its tool
arguments, in full (normally each reply is shown when complete, cut to 100 words).

The API key comes from OPENROUTER_API_KEY (environment or the repo's .env file).
"""

from __future__ import annotations

import asyncio
import json
import os
import queue
import re
import shutil
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from . import tournament
from .challenge import Challenge, ChallengeState
from .config import REPO_ROOT, load_config
from .manual import MODEL_INSTRUCTIONS
from .sdk.store import entrant_folder

API = "https://openrouter.ai/api/v1"
PROMPT_PATH = MODEL_INSTRUCTIONS / "Agent Prompt.md"
KICKOFF = ("Begin the challenge now. Start with get_challenge. Work only through the bamzooki tools, and "
           "finish with submit_design followed by your presentation to the audience.")
SHOW_WORDS = 100  # model text between tool calls is cut to this many words on screen (not in transcripts)
NUDGE = ("You haven't submitted yet. Keep going with the bamzooki tools; when you're ready (or out of "
         "runs), call submit_design with your best design, then present your challenger.")
PRE_MATCH = ("The {contest} knockout, {round}: your Zook {mine} is about to play {opp_model}'s Zook {opp_zook}. "
             "{heard}Say something to the audience before the game: a comment, some trash talk, or a boast "
             "about {mine}. Answer in ONE line (no line breaks, no lists) and don't call any tools.")
AUDIENCE = "[{context}] Someone in the audience says to you: {msg}\n(Reply to them; don't call any tools.)"
REMARK_CHARS = 280  # the pre-match comment is one line of at most this many characters
INTERJECTION = ("[A message from the audience, sent while you were working. Take it into account (answer "
                "briefly if it asks something), then carry on where you were.]\n\n{}")

C = {"dim": "\033[2m", "b": "\033[1m", "g": "\033[32m", "y": "\033[33m", "c": "\033[36m", "m": "\033[35m",
     "r": "\033[31m", "x": "\033[0m"}


def say(text: str, colour: str = "", end: str = "\n") -> None:
    print(f"{C.get(colour, '')}{text}{C['x'] if colour else ''}", end=end, flush=True)


def load_api_key() -> str:
    key = os.environ.get("OPENROUTER_API_KEY")
    env = REPO_ROOT / ".env"
    if not key and env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            m = re.match(r"\s*OPENROUTER_API_KEY\s*=\s*(.+)\s*$", line)
            if m:
                key = m.group(1).strip().strip("'\"")
    if not key:
        raise SystemExit("OPENROUTER_API_KEY not set (environment or .env in the repo)")
    return key


# --- OpenRouter -------------------------------------------------------------------------

class OpenRouter:
    def __init__(self, key: str, max_tokens: int = 4000, temperature: float | None = None):
        self.http = httpx.AsyncClient(base_url=API, timeout=300, headers={
            "Authorization": f"Bearer {key}", "HTTP-Referer": "https://github.com/bamzooki-harness",
            "X-Title": "BAMZOOKi Arena"})
        self.max_tokens = max_tokens
        self.temperature = temperature
        self._models: dict | None = None

    async def model_info(self, model: str) -> dict:
        if self._models is None:
            r = await self.http.get("/models")
            r.raise_for_status()
            self._models = {m["id"]: m for m in r.json().get("data", [])}
        return self._models.get(model, {})

    async def chat(self, model: str, messages: list[dict], tools: list[dict], on_delta=None,
                   single_tool: bool = False, tool_choice: str = "auto") -> dict:
        """One completion. With on_delta(kind, text) it is streamed: each piece of "reasoning" or
        "content" goes to on_delta as it arrives, and the reply is put back together into the
        same shape a non-streamed request returns."""
        body = {"model": model, "messages": messages, "tools": tools, "tool_choice": tool_choice,
                "max_tokens": self.max_tokens, "usage": {"include": True}}
        if self.temperature is not None:
            body["temperature"] = self.temperature
        if not tools:  # an empty tool list is an error for some APIs
            del body["tools"], body["tool_choice"]
        elif single_tool:
            body["parallel_tool_calls"] = False
        if on_delta:
            return await self._stream({**body, "stream": True}, on_delta)
        for attempt in range(5):
            r = await self.http.post("/chat/completions", json=body)
            if r.status_code in (429, 500, 502, 503, 504):
                await asyncio.sleep(2 * (attempt + 1))
                continue
            data = r.json() if r.content else {}
            if r.status_code != 200 or "error" in data:
                raise RuntimeError(f"OpenRouter {r.status_code}: {json.dumps(data.get('error', data))[:500]}")
            return data
        raise RuntimeError("OpenRouter: too many retries")

    async def _stream(self, body: dict, on_delta) -> dict:
        for attempt in range(5):
            async with self.http.stream("POST", "/chat/completions", json=body) as r:
                if r.status_code in (429, 500, 502, 503, 504):
                    await r.aread()
                    await asyncio.sleep(2 * (attempt + 1))
                    continue
                if r.status_code != 200:
                    raise RuntimeError(f"OpenRouter {r.status_code}: {(await r.aread()).decode(errors='replace')[:500]}")
                return await _read_stream(r.aiter_lines(), on_delta)
        raise RuntimeError("OpenRouter: too many retries")


async def _read_stream(lines, on_delta) -> dict:
    """Put a chat-completions event stream back together: text, reasoning, tool calls (sent in
    fragments, keyed by index) and the usage chunk at the end."""
    content, reasoning, calls, usage = [], [], {}, {}
    async for line in lines:
        if not line.startswith("data:"):
            continue  # blank lines and ": OPENROUTER PROCESSING" keep-alives
        data = line[5:].strip()
        if data == "[DONE]":
            break
        chunk = json.loads(data)
        if chunk.get("error"):
            raise RuntimeError(f"OpenRouter (mid-stream): {json.dumps(chunk['error'])[:500]}")
        usage = chunk.get("usage") or usage
        for choice in chunk.get("choices") or []:
            delta = choice.get("delta") or {}
            for kind, parts in (("reasoning", reasoning), ("content", content)):
                if delta.get(kind):
                    parts.append(delta[kind])
                    on_delta(kind, delta[kind])
            for tc in delta.get("tool_calls") or []:
                call = calls.setdefault(tc.get("index", len(calls)),
                                        {"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                call["id"] = tc.get("id") or call["id"]
                fn = tc.get("function") or {}
                call["function"]["name"] = call["function"]["name"] or fn.get("name") or ""
                call["function"]["arguments"] += fn.get("arguments") or ""
    msg = {"content": "".join(content), "reasoning": "".join(reasoning) or None,
           "tool_calls": [calls[i] for i in sorted(calls)] or None}
    return {"choices": [{"message": msg}], "usage": usage}


# --- the keyboard ----------------------------------------------------------------------------

class Keyboard:
    """Reads stdin on its own thread for the whole session, so the audience can type while a
    model works. Lines wait in a queue: the prompt takes them one at a time, a working model
    gets them all after its current step (see Arena.converse)."""

    def __init__(self):
        self.lines: queue.Queue[str | None] = queue.Queue()  # None = end of input
        self.busy = False  # a model is working: say a line is queued for it
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        while True:
            try:
                line = input()
            except (EOFError, KeyboardInterrupt, OSError):
                self.lines.put(None)
                return
            if self.busy and line.strip():
                say("    (queued: the model gets it after its current step; /stop halts it)", "dim")
            self.lines.put(line)

    def halting(self) -> bool:
        """/stop or /next has been typed and not handled yet (looked at, not taken)."""
        with self.lines.mutex:
            return any(line and line.strip() in ("/stop", "/next") for line in self.lines.queue)

    def get(self) -> str | None:
        """The next line (blocking); None at end of input."""
        return self.lines.get()

    def drain(self) -> list[str]:
        """Every line typed so far, without waiting (end of input is left for get())."""
        out = []
        while True:
            try:
                line = self.lines.get_nowait()
            except queue.Empty:
                return out
            if line is None:
                self.lines.put(None)
                return out
            if line.strip():
                out.append(line.strip())


# --- one challenger ------------------------------------------------------------------------

@dataclass
class Entrant:
    model: str
    vision: bool = True
    messages: list[dict] = field(default_factory=list)
    submitted: bool = False
    last_media: list[str] = field(default_factory=list)
    cost: float = 0.0
    tokens: int = 0
    transcript: Path | None = None
    design: str | None = None  # the submitted design ("folder/Name@n")
    qualified: bool = False  # its challenge game was a win
    single_tool: bool = False  # its provider takes one tool call per reply (found out on the way)
    submission: dict | None = None  # submit_design's result
    presented: bool = False  # its presentation has been saved


class Arena:
    def __init__(self, challenge: Challenge, queue: list[str], max_turns: int = 120, max_tokens: int = 4000,
                 temperature: float | None = None, auto: bool = False, verbose: bool = False):
        self.cfg = load_config()
        self.challenge = challenge
        self.queue = list(queue)
        self.max_turns = max_turns
        self.auto = auto
        self.verbose = verbose
        self.keyboard: Keyboard | None = None  # started by repl()
        self.skip = False  # /next typed while a model worked: move on to the next challenger
        self.ending = False  # /end: finish with the session's table
        self.knockout_places: dict[str, int] = {}  # model -> place in the last /contest
        self.router = OpenRouter(load_api_key(), max_tokens, temperature)
        self.arena_dir = self.cfg.arena_dir
        self.arena_dir.mkdir(parents=True, exist_ok=True)
        self.entrant: Entrant | None = None
        self.entrants: dict[str, Entrant] = {}  # everyone called this session, in order
        self.session = None
        self.tools: list[dict] = []
        self._stack = None

    def archive_entries(self) -> Path | None:
        """Move the previous sessions' Zook folders (designs, budgets, submissions) into
        <arena>/Archive/<date_time>/Zook Entries/, so every model gets another go."""
        zoo = self.cfg.zoo_dir
        old = [p for p in zoo.iterdir()] if zoo.is_dir() else []
        if not old:
            return None
        dest = self.arena_dir / "Archive" / time.strftime("%Y-%m-%d_%H-%M-%S") / zoo.name
        dest.mkdir(parents=True, exist_ok=True)
        for p in old:
            shutil.move(str(p), str(dest / p.name))
        return dest

    # --- MCP server per entrant ---
    async def _connect(self, model: str) -> None:
        from contextlib import AsyncExitStack

        from mcp import ClientSession
        from mcp.client.stdio import StdioServerParameters, get_default_environment, stdio_client

        await self._disconnect()
        self._stack = AsyncExitStack()
        # stdio_client only passes a minimal environment by default: hand on our settings explicitly
        env = {**get_default_environment(), **{k: v for k, v in os.environ.items() if k.startswith("BAMZOOKI_")},
               "BAMZOOKI_ARENA_DIR": str(self.arena_dir)}
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "bamzooki.mcp_server", "--model", model, "--challenge", str(self.challenge.path)],
            cwd=str(self.arena_dir), env=env)
        # The server's own output (tracebacks from tools a model called wrongly) goes beside the
        # transcript, not over the audience's screen; the model still gets the error as the result.
        t = self.entrant.transcript if self.entrant else None
        errlog = self._stack.enter_context(
            t.with_suffix(".mcp.log").open("a", encoding="utf-8") if t else open(os.devnull, "w"))
        r, w = await self._stack.enter_async_context(stdio_client(params, errlog=errlog))
        self.session = await self._stack.enter_async_context(ClientSession(r, w))
        await self.session.initialize()
        listed = await self.session.list_tools()
        self.tools = [{"type": "function", "function": {
            "name": t.name, "description": t.description or "",
            "parameters": getattr(t, "input_schema", None) or getattr(t, "inputSchema", None) or {"type": "object"}}}
            for t in listed.tools if t.name != "register_entrant"]  # the runner names the entrant

    async def _disconnect(self) -> None:
        if self._stack:
            try:
                await self._stack.aclose()
            except Exception:
                pass
        self._stack, self.session = None, None

    # --- transcript ---
    def _log(self, kind: str, text: str, entrant: Entrant | None = None) -> None:
        e = entrant or self.entrant
        if not e or not e.transcript:
            return
        with e.transcript.open("a", encoding="utf-8") as f:
            f.write(f"\n### {kind} ({time.strftime('%H:%M:%S')})\n\n{text}\n")

    # --- the agent loop ---
    async def start(self, model: str) -> None:
        info = await self.router.model_info(model)
        if not info:
            say(f"Unknown OpenRouter model {model!r} (check the id at openrouter.ai/models).", "r")
            return
        params = info.get("supported_parameters") or []
        if params and "tools" not in params:
            say(f"{model} doesn't support tool calling on OpenRouter; skipping.", "r")
            return
        vision = "image" in ((info.get("architecture") or {}).get("input_modalities") or [])
        self.entrant = Entrant(model, vision)
        self.entrants[model] = self.entrant
        folder = entrant_folder(model)
        if win := self._winning_submission(folder):  # won in an earlier session: qualified, can't resubmit
            self.entrant.submitted, self.entrant.qualified, self.entrant.design = True, True, win["design"]
        tdir = self.cfg.transcripts_dir / folder
        tdir.mkdir(parents=True, exist_ok=True)
        self.entrant.transcript = tdir / f"{time.strftime('%Y-%m-%d_%H-%M-%S')}.md"
        self.entrant.transcript.write_text(f"# {model} - {self.challenge.title}\n", encoding="utf-8")
        say(f"\n=== Challenger: {model} ===", "b")
        say(f"    vision: {'yes' if vision else 'no'}  |  challenge: {self.challenge.title}  |  "
            f"arena: {self.arena_dir}", "dim")
        await self._connect(model)
        system = PROMPT_PATH.read_text(encoding="utf-8") + (
            f"\n\nYou are registered as the entrant \"{model}\" (your full model name); you don't need "
            f"to call register_entrant.")
        self.entrant.messages = [{"role": "system", "content": system}, {"role": "user", "content": KICKOFF}]
        self._log("system", system)
        await self.converse(nudges=3)

    async def converse(self, nudges: int = 0) -> None:
        """Let the model work until it answers without calling tools. Lines the audience typed
        meanwhile are handed over between steps; /stop ends the work there."""
        e = self.entrant
        if self.keyboard:
            self.keyboard.busy = True
        try:
            await self._work(e, nudges)
        finally:
            if self.keyboard:
                self.keyboard.busy = False

    def _interjections(self, e: Entrant) -> tuple[int, bool]:
        """Pass on what the audience typed while the model worked: (messages passed, /stop typed)."""
        passed, stop = 0, False
        for line in self.keyboard.drain() if self.keyboard else []:
            if line in ("/stop", "/quit", "/exit"):
                stop = True
            elif line == "/end":
                stop, self.ending = True, True
            elif line == "/next":
                stop, self.skip = True, True
            elif line.startswith("/"):
                say(f"    ({line.split()[0]} waits until the model is done: type it again then, or /stop / /next)", "y")
            else:
                e.messages.append({"role": "user", "content": INTERJECTION.format(line)})
                self._log("audience (while working)", line)
                say(f"    >> passed to {e.model}: {_clip(line, 20)}", "c")
                passed += 1
        return passed, stop

    async def _work(self, e: Entrant, nudges: int) -> None:
        for _ in range(self.max_turns):
            _, stop = self._interjections(e)
            if stop:
                say(f"[stopped {e.model}]" if self.skip else f"[stopped: type to talk to {e.model}, or /next]", "y")
                return
            _prune_images(e.messages, keep=2)
            shown: list[str] = []  # kinds already streamed to the screen
            try:
                # verbose: stream, so reasoning and text appear as the model writes them
                data = await self.router.chat(e.model, e.messages, self.tools, single_tool=e.single_tool,
                                              on_delta=self._printer(e, shown) if self.verbose else None)
                if shown:
                    say("")
            except (RuntimeError, httpx.HTTPError) as err:
                if not e.single_tool and "single tool" in str(err).lower():
                    e.single_tool = True  # this provider takes one call per reply: redo the history that way
                    _one_call_per_turn(e.messages)
                    say("    [this model's provider takes one tool call at a time; carrying on that way]", "y")
                    continue
                say(f"\n[api error] {err}", "r")
                return
            usage = data.get("usage") or {}
            e.cost += float(usage.get("cost") or 0)
            e.tokens += int(usage.get("total_tokens") or 0)
            msg = (data.get("choices") or [{}])[0].get("message") or {}
            text = (msg.get("content") or "").strip()
            thought = (msg.get("reasoning") or "").strip()
            calls = msg.get("tool_calls") or []
            if e.single_tool and len(calls) > 1:
                say(f"    [{len(calls)} tool calls at once: running the first; this model gets one per turn]", "y")
                calls = calls[:1]
            e.messages.append({k: v for k, v in {"role": "assistant", "content": msg.get("content") or "",
                                                 "tool_calls": calls or None}.items() if v is not None})
            if thought:
                if "reasoning" not in shown:
                    say(f"\n{e.model} (thinking): {self._clip(thought)}", "dim")
                self._log("model reasoning", thought)
            if text:
                if "content" not in shown:
                    say(f"\n{e.model}: ", "c", end="")
                    say(self._clip(text) if calls else text)  # working notes between tool calls are cut; answers aren't
                self._log("model", text)
            if not calls and text and e.submission and not e.presented:
                self._save_presentation(e, text)
            if not calls:
                passed, stop = self._interjections(e)  # typed while it was answering
                if passed and not stop:
                    continue
                if not e.submitted and nudges > 0 and not stop:
                    nudges -= 1
                    e.messages.append({"role": "user", "content": NUDGE})
                    self._log("runner", NUDGE)
                    continue
                say(f"    [{e.tokens:,} tokens, ${e.cost:.4f} so far]", "dim")
                return
            images = []
            for call in calls:
                if self.keyboard and self.keyboard.halting():  # don't sit through the rest of a batch
                    e.messages.append({"role": "tool", "tool_call_id": call.get("id", ""),
                                       "content": "(not run: the audience stopped you)"})
                    continue
                images += await self._run_tool(call)
            if images and e.vision:
                e.messages.append({"role": "user", "content": [
                    {"type": "text", "text": "Images returned by the tool call(s) above:"}, *images]})
        say(f"[stopped after {self.max_turns} turns]", "y")

    async def _run_tool(self, call: dict) -> list[dict]:
        e = self.entrant
        fn = call.get("function") or {}
        name = fn.get("name", "")
        args, bad = _tool_args(fn.get("arguments"))
        say(f"  -> {name}({json.dumps(args) if self.verbose and args is not None else _short(args)})", "dim")
        images: list[dict] = []
        if args is None:
            text = bad
        else:
            try:
                res = await self.session.call_tool(name, args)
                texts, first = [], None
                for c in res.content:
                    if getattr(c, "type", "") == "text":
                        texts.append(c.text)
                        first = c.text if first is None else first
                    elif getattr(c, "type", "") == "image":
                        images.append({"type": "image_url",
                                       "image_url": {"url": f"data:{c.mimeType if hasattr(c, 'mimeType') else c.mime_type};base64,{c.data}"}})
                text = "\n".join(texts) or "(no text)"
                if getattr(res, "is_error", False) or getattr(res, "isError", False):
                    text = "ERROR: " + text
            except Exception as err:
                text = f"ERROR: {err}"
        self._summarise(name, first if args is not None and first else text)
        self._log(f"tool {name}", f"args: {json.dumps(args)[:2000]}\n\nresult: {text[:4000]}")
        e.messages.append({"role": "tool", "tool_call_id": call.get("id", ""), "content": text})
        return images

    def _summarise(self, name: str, text: str) -> None:
        """One line per tool result for the audience."""
        try:
            d = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            d = None
        e = self.entrant
        if not isinstance(d, dict):
            say(f"     {text[:160]}", "dim")
            return
        if d.get("error"):
            say(f"     ! {d['error']}", "y")
        for key in ("video", "stills"):
            if d.get(key):
                e.last_media = [p for p in (d.get("video"), d.get("stills")) if p]
        if name in ("save_zook", "edit_zook") and d.get("saved"):
            s = d.get("stats", {})
            say(f"     saved {d['ref']}: {s.get('Weight', '?')}, {s.get('Components', '?')} parts"
                f"{' (clamped)' if d.get('clamped') else ''}", "g")
        elif name == "run_trial" and d.get("result"):
            m = d.get("moved") or {}
            say(f"     {d['result'].get('name')}: {d['result'].get('value')} {d['result'].get('unit')}"
                f"  (drift {m.get('drift_deg')} deg, fell {m.get('fell')})", "g")
        elif name == "run_contest" and "winner" in d:
            res = (f"winner {d['winner_zook']}" if d.get("winner_zook") else
                   "draw (time limit)" if d.get("status") == "timeout" else
                   "draw" if d.get("status") == "finished" else f"no result ({d.get('status')})")
            say(f"     {d.get('contest')}: {d.get('green')} vs {d.get('red')} -> {res}", "g")
        elif name == "submit_design" and "outcome" in d:
            e.submitted, e.design, e.submission = True, d["design"], d
            e.qualified = e.qualified or d["outcome"] == "win"
            how = " (time limit)" if d["outcome"] == "draw" and d.get("status") == "timeout" else ""
            say(f"     SUBMITTED {d['design']}: {d['outcome'].upper()}{how} (score {d['score']})", "m")
            say("     QUALIFIED for the /contest knockout" if e.qualified else
                "     not qualified for /contest (only a win qualifies)", "m" if e.qualified else "y")
        if isinstance(d.get("budget"), dict) and d["budget"].get("reminder"):
            say(f"     {d['budget']['reminder']}", "y")

    def _clip(self, text: str) -> str:
        return text if self.verbose else _clip(text)

    @staticmethod
    def _printer(e: Entrant, shown: list[str]):
        """on_delta for a streamed reply: a heading whenever it switches between reasoning and
        text, then the pieces as they come."""
        def on_delta(kind: str, piece: str) -> None:
            thinking = kind == "reasoning"
            if not shown or shown[-1] != kind:
                if shown:
                    say("")
                say(f"\n{e.model}{' (thinking)' if thinking else ''}: ", "dim" if thinking else "c", end="")
                shown.append(kind)
            say(piece, "dim" if thinking else "", end="")
        return on_delta

    def _save_presentation(self, e: Entrant, text: str) -> None:
        """Zook Entries/<model>/Presentation - <challenge>.txt: the model's presentation of its
        challenger, under a header with the facts (design, result, runs, cost; no video links)."""
        s = e.submission or {}
        folder = self.cfg.zoo_dir / entrant_folder(e.model)
        state = ChallengeState(self.challenge, folder)
        game = s.get("game") or {}
        how = " (time limit)" if s.get("outcome") == "draw" and s.get("status") == "timeout" else ""
        header = [
            f"BAMZOOKi Arena - {e.model} presents its challenger",
            f"Challenge:       {self.challenge.title} ({self.challenge.name})",
            f"Date:            {time.strftime('%Y-%m-%d %H:%M')}",
            f"Submitted Zook:  {s.get('design', '?')}",
            f"Challenge game:  {game.get('contest', '?')} v {game.get('opponent', '?')}: "
            f"{str(s.get('outcome', '?')).upper()}{how}, score {s.get('score', '?')}"
            + ("  -> qualified for the knockout" if e.qualified else ""),
            f"Test runs used:  {state.runs_used} of {self.challenge.runs or 'unlimited'}",
            f"Tokens / cost:   {e.tokens:,} / ${e.cost:.4f}",
        ]
        path = folder / f"Presentation - {self.challenge.name}.txt"
        try:
            folder.mkdir(parents=True, exist_ok=True)
            path.write_text("\n".join(header) + "\n\n" + text.strip() + "\n", encoding="utf-8")
            e.presented = True
            say(f"    [presentation saved: {path}]", "dim")
        except OSError as err:
            say(f"    [couldn't save the presentation: {err}]", "y")

    def _winning_submission(self, folder: str) -> dict | None:
        d = self.cfg.zoo_dir / folder
        if not (d / f"challenge-{self.challenge.name}.json").exists():
            return None
        subs = ChallengeState(self.challenge, d).data.get("submissions", [])
        return next((s for s in subs if s.get("outcome") == "win"), None)

    # --- commands ---
    def leaderboard(self) -> None:
        rows = []
        root = self.cfg.zoo_dir
        if root.is_dir():
            for d in root.iterdir():
                if (d / f"challenge-{self.challenge.name}.json").exists():
                    st = ChallengeState(self.challenge, d)
                    subs = st.data.get("submissions", [])
                    best = max(subs, key=lambda s: s.get("score", 0)) if subs else None
                    rows.append((best["score"] if best else -1, d.name, best, st.runs_used))
        say(f"\n  Leaderboard - {self.challenge.title}", "b")
        if not rows:
            say("  (no entrants yet)", "dim")
        for score, name, best, used in sorted(rows, key=lambda r: -r[0]):
            res = f"{best['outcome'].upper():5s} score {best['score']}  with {best['design']}" if best else "not submitted"
            q = "  qualified" if best and best["outcome"] == "win" else ""
            say(f"  {name:45s} {res}  ({used} runs){q}")

    def open_media(self) -> None:
        if not self.entrant or not self.entrant.last_media:
            say("Nothing to open yet.", "dim")
            return
        for p in self.entrant.last_media:
            if Path(p).exists() and hasattr(os, "startfile"):
                os.startfile(p)  # noqa: S606 (local media files we produced)
                say(f"opened {p}", "dim")

    async def contest(self) -> None:
        """Knockout between the Zooks that won their challenge game this session, then the
        winner's speech; the audience's next messages go to the winner."""
        seats = [tournament.Seat(e.model, e.design) for e in self.entrants.values() if e.qualified and e.design]
        if len(seats) < 2:
            names = ", ".join(s.billing() for s in seats) or "nobody yet"
            say(f"A contest needs two qualified Zooks (a win in the challenge game); qualified: {names}.", "y")
            return
        from .runner import Engine, Job, contests
        from .sdk.store import Zoo

        engine = Engine()
        engine.prepare()
        zoo = Zoo(engine.install, examples_root=engine.root / "Teams" / "examples" / "Creatures")
        info = contests.find(self.challenge.evaluation.contest, engine.install)

        now = {"round": "", "game": 0}  # for the video titles

        def play(green: tournament.Seat, red: tournament.Seat, seed: int) -> tuple[str, str | None, str]:
            job = Job(info, zoo.resolve(green.zook).path, zoo.resolve(red.zook).path, seed=seed,
                      duration=self.challenge.time_limit, video=True, model="Tournament")
            r = engine.run(job, name=f"cup-{info.contest_file}")
            now["game"] += 1
            if r.video:  # a copy for each competitor, beside its test videos
                for me, opp in ((green, red), (red, green)):
                    if me.folder == "examples":
                        continue
                    _keep_video(r.video, self.cfg.zoo_dir / me.folder / "Videos",
                                f"Knockout {now['round']} game {now['game']} - {self.challenge.name} - {info.name} - "
                                f"{me.zook_name} vs {opp.zook_name} ({opp.model}) - seed {seed}")
            return r.status, r.winner, _game_stats(r, green.zook_name, red.zook_name)

        loop = asyncio.get_running_loop()
        matches = [len(seats) - 1]  # matches still to play (a knockout of n has n - 1), to word the prompt

        def before(a: tournament.Seat, b: tournament.Seat, round_name: str) -> None:
            """Both models comment (the second hears the first), then the audience may answer."""
            # "Semi-finals" -> "Semi-final" for one match's videos
            now["round"], now["game"] = round_name[:-1] if round_name.endswith("finals") else round_name, 0

            async def talk():
                heard = ""
                for me, opp in ((a, b), (b, a)):
                    line = await self.remark(me, PRE_MATCH.format(
                        contest=info.name, round=round_name, mine=me.zook_name, opp_model=opp.model,
                        opp_zook=opp.zook_name, heard=heard), one_line=True)
                    heard = f'{me.model} has just said: "{line}" ' if line else ""
                await self.match_talk([a, b], f"{info.name} knockout, {round_name}: "
                                              f"{a.billing()} v {b.billing()}, about to play", "to start the game")
            asyncio.run_coroutine_threadsafe(talk(), loop).result()

        def after(bout: tournament.Bout) -> None:
            matches[0] -= 1
            result = (f"{bout.winner.billing()} beat {bout.loser.billing()} in {len(bout.games)} game(s)"
                      + (" on a coin toss" if bout.how == "coin toss" else ""))
            nxt = "for the next match" if matches[0] else "for the final ranking"
            asyncio.run_coroutine_threadsafe(self.match_talk(
                [bout.a, bout.b], f"{info.name} knockout, just played: {result}", nxt), loop).result()

        say(f"\n*** THE {info.name.upper()} KNOCKOUT: {len(seats)} qualified Zooks ***", "b")
        for s in seats:
            say(f"    {s.billing()}  [{s.zook}]")
        say("    Leave the game window in front while the games play. Between games, talk to a model with "
            "/<its name or its Zook's name> <message>, type to both, or press Enter to go on.", "dim")
        rounds, places = await asyncio.to_thread(tournament.knockout, seats, play, say, before=before, after=after)
        champ = places[0][1]
        self.knockout_places = {s.model: p for p, s in places}
        say("\n  Final ranking", "b")
        for place, s in places:
            say(f"  {place:>2}. {s.billing()}")
        say(f"\n*** {champ.model} WINS THE KNOCKOUT with {champ.zook_name}! ***", "m")

        path = []
        for b in (b for bouts in rounds for b in bouts if b.b is not None and champ in (b.a, b.b)):
            other = b.b if b.a is champ else b.a
            path.append(f"beat {other.billing()} in {len(b.games)} game(s)"
                        + (" on a coin toss" if b.how == "coin toss" else ""))
        standings = "; ".join(f"{p}. {s.billing()}" for p, s in places)
        self.entrant = self.entrants[champ.model]
        await self._connect(champ.model)
        prompt = (f"Your Zook {champ.zook_name} has just WON the {info.name} knockout tournament between the "
                  f"qualified models' Zooks. Your path: {'; '.join(path)}. Final ranking: {standings}. "
                  "Give the audience a short victory speech (under 150 words). After that the audience may "
                  "ask you questions.")
        self.entrant.messages.append({"role": "user", "content": prompt})
        self._log("runner", prompt)
        await self.converse()
        say("\n(Questions for the champion: just type them. /next or /challenger carries on.)", "dim")

    async def remark(self, seat: tournament.Seat, prompt: str, one_line: bool = False) -> str:
        """A contestant's answer in its own conversation (no tools): printed and returned. The
        pre-match comment is held to one line."""
        e = self.entrants[seat.model]
        e.messages.append({"role": "user", "content": prompt})
        self._log("runner", prompt, e)
        try:
            # tools stay declared (some providers require it once a history has tool calls) but unused
            data = await self.router.chat(e.model, e.messages, self.tools, single_tool=e.single_tool,
                                          tool_choice="none")
        except (RuntimeError, httpx.HTTPError) as err:
            e.messages.pop()
            say(f"\n  [{seat.billing()} has nothing to say: {err}]", "r")
            return ""
        usage = data.get("usage") or {}
        e.cost += float(usage.get("cost") or 0)
        e.tokens += int(usage.get("total_tokens") or 0)
        msg = (data.get("choices") or [{}])[0].get("message") or {}
        text = (msg.get("content") or "").strip()
        line = _one_line(text) if one_line else text
        e.messages.append({"role": "assistant", "content": line or "(no comment)"})
        self._log("model", line, e)
        say(f"\n  {seat.model} ({seat.zook_name}): ", "c", end="")
        say(line or "(no comment)")
        return line

    async def match_talk(self, seats: list[tournament.Seat], context: str, enter: str) -> None:
        """Between games: '/<name> message' to one contestant, plain text to both, Enter to go on.
        Lines typed while a game played are taken first."""
        if not self.keyboard:
            return
        pending = self.keyboard.drain()
        hint = " or ".join(f"/{_short_name(s.model)}" for s in seats)
        while True:
            if pending:
                line = pending.pop(0)
            else:
                say(f"\n  [Enter] {enter}  ({hint} <message> to one, or type to both) ", "dim", end="")
                line = await asyncio.to_thread(self.keyboard.get)
            if line is None or not line.strip():
                return
            line = line.strip()
            targets, msg = seats, line
            if line.startswith("/"):
                who, _, msg = line[1:].partition(" ")
                seat = _find_seat(seats, who)
                if not seat:
                    say(f"  No one called {who!r} in this match: {hint} (a model's name or its Zook's name).", "y")
                    continue
                if not msg.strip():
                    say(f"  Type your message after /{who}.", "y")
                    continue
                targets = [seat]
            for s in targets:
                self._log("audience", msg.strip(), self.entrants[s.model])
                await self.remark(s, AUDIENCE.format(context=context, msg=msg.strip()))

    async def offer_contest(self) -> None:
        """When the queue has run out, ask whether to play the knockout now."""
        if self.queue or not self.keyboard or self.ending:
            return
        qualified = [e for e in self.entrants.values() if e.qualified and e.design]
        if len(qualified) < 2:
            say(f"\nThe queue is empty. {len(qualified)} model(s) qualified: /contest needs two "
                "(add more with /challenger <model>).", "dim")
            return
        self.keyboard.drain()
        say(f"\nThe queue is empty and {len(qualified)} models have qualified. Run the knockout contest now? [Y/N] ",
            "b", end="")
        answer = await asyncio.to_thread(self.keyboard.get)
        if (answer or "").strip().lower() in ("y", "yes"):
            await self.contest()
        else:
            say("No contest for now: /contest starts it whenever you like.", "dim")

    async def status(self) -> None:
        if not self.session:
            say("No challenger running.", "dim")
            return
        res = await self.session.call_tool("get_challenge", {})
        d = json.loads(res.content[0].text)
        say(json.dumps({"entrant": self.entrant.model, "budget": d.get("budget"),
                        "submitted": [s.get("design") for s in d.get("submitted", [])]}, indent=1))

    async def repl(self) -> None:
        if os.name == "nt":
            os.system("")  # enable ANSI colours in cmd.exe
        say("BAMZOOKi Arena - type /next to call the first challenger, /help for commands.", "b")
        say("You can type while a model works: it gets your message after its current step (/stop halts it).",
            "dim")
        self.keyboard = Keyboard()
        if self.auto:
            while self.queue:
                await self._safely(self.start(self.queue.pop(0)))
            self.leaderboard()
            await self._disconnect()
            self.summary()
            return
        while not self.ending:
            say("\nyou> ", end="")
            try:
                line = await asyncio.to_thread(self.keyboard.get)
            except KeyboardInterrupt:
                break
            if line is None:
                break
            line = line.strip()
            if not line or line == "/stop":
                continue
            cmd, _, arg = line.partition(" ")
            if cmd in ("/quit", "/exit"):
                break
            elif cmd == "/help":
                say(__doc__.split("Commands:")[1].split("The API key")[0])
            elif cmd == "/next":
                if not self.queue:
                    say("The queue is empty: use /challenger <openrouter model id>.", "y")
                    continue
                await self.call(self.queue.pop(0))
            elif cmd == "/challenger":
                if not arg:
                    say("usage: /challenger <openrouter model id>, e.g. /challenger openai/gpt-5", "y")
                    continue
                await self.call(arg.strip())
            elif cmd == "/end":
                self.ending = True
            elif cmd == "/list":
                say("queue: " + (", ".join(self.queue) or "(empty)"))
            elif cmd == "/leaderboard":
                self.leaderboard()
            elif cmd == "/open":
                self.open_media()
            elif cmd == "/status":
                await self.status()
            elif cmd == "/contest":
                await self.contest()
            elif cmd.startswith("/"):
                say("unknown command; /help lists them", "y")
            else:
                if not self.entrant:
                    say("No challenger yet: /next or /challenger <model>.", "y")
                    continue
                self.entrant.messages.append({"role": "user", "content": line})
                self._log("audience", line)
                await self._safely(self.converse())
                if self.skip and self.queue:  # /next typed while it answered
                    await self.call(self.queue.pop(0))
                self.skip = False
        await self._disconnect()
        if self.ending:
            self.summary()
            if self.keyboard:
                self.keyboard.drain()
                say("\nPress Enter to quit. ", "b", end="")
                await asyncio.to_thread(self.keyboard.get)
        say("bye", "dim")

    def summary(self) -> Path | None:
        """The session in one table: every model called, its Zook, result, runs, knockout place,
        tokens and cost, with totals. Printed and saved to <arena>/Sessions/<date_time>.txt."""
        if not self.entrants:
            say("\nNo models were called this session.", "dim")
            return None
        rows = []
        for e in self.entrants.values():
            s = e.submission or {}
            outcome = str(s.get("outcome", "")).upper()
            if outcome == "DRAW" and s.get("status") == "timeout":
                outcome += " (time limit)"
            runs = ChallengeState(self.challenge, self.cfg.zoo_dir / entrant_folder(e.model)).runs_used
            rows.append([e.model, (e.design or "-").split("/", 1)[-1], outcome or "not submitted",
                         f"{s['score']:g}" if "score" in s else "-", "yes" if e.qualified else "no",
                         f"{runs} of {self.challenge.runs or 'unlimited'}",
                         _place(self.knockout_places, e.model), f"{e.tokens:,}", f"${e.cost:.4f}"])
        total = ["TOTAL", "", "", "", f"{sum(e.qualified for e in self.entrants.values())} qualified", "", "",
                 f"{sum(e.tokens for e in self.entrants.values()):,}",
                 f"${sum(e.cost for e in self.entrants.values()):.4f}"]
        lines = _table(["Model", "Zook", "Challenge game", "Score", "Qualified", "Test runs", "Knockout",
                        "Tokens", "Cost"], rows, total)
        title = f"BAMZOOKi Arena - {self.challenge.title} - session ending {time.strftime('%Y-%m-%d %H:%M')}"
        say(f"\n{title}\n", "b")
        for line in lines:
            say(line)
        path = self.arena_dir / "Sessions" / f"{time.strftime('%Y-%m-%d_%H-%M-%S')}.txt"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(title + "\n\n" + "\n".join(lines) + "\n", encoding="utf-8")
            say(f"\n(saved to {path})", "dim")
        except OSError as err:
            say(f"\n(couldn't save the table: {err})", "y")
            return None
        return path

    async def call(self, model: str) -> None:
        """Run a challenger. /next typed while it works (or a model that breaks down) moves straight
        on to the next one in the queue; when the queue runs out, offer the contest."""
        while True:
            self.skip = False
            await self._safely(self.start(model))
            if not self.skip or self.ending:
                break
            self.skip = False
            if not self.queue:
                say("The queue is empty: /challenger <model> calls another, /contest runs the knockout.", "y")
                break
            model = self.queue.pop(0)
            say(f"[moving on to {model}]", "y")
        await self.offer_contest()

    async def _safely(self, work) -> None:
        """One model's failure (a crash in the loop, its MCP server dying) mustn't end the session."""
        try:
            await work
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as err:  # noqa: BLE001 (report it and keep the arena running)
            name = self.entrant.model if self.entrant else "the model"
            say(f"\n[{name} broke down: {type(err).__name__}: {err}] /next moves on, or talk to it to retry.", "r")
            self._log("runner error", f"{type(err).__name__}: {err}")
            if self.keyboard:
                self.keyboard.busy = False


def _prune_images(messages: list[dict], keep: int = 2) -> None:
    """Only the latest `keep` image messages keep their pictures (each request resends history)."""
    seen = 0
    for m in reversed(messages):
        if m.get("role") == "user" and isinstance(m.get("content"), list):
            if any(p.get("type") == "image_url" for p in m["content"]):
                seen += 1
                if seen > keep:
                    m["content"] = [{"type": "text", "text": "[earlier images omitted to save space]"}]


def _one_call_per_turn(messages: list[dict]) -> None:
    """Rewrite, in place, every reply that made several tool calls as consecutive replies of one
    call each, each followed by its own result, for providers that refuse parallel calls."""
    out, i = [], 0
    while i < len(messages):
        m = messages[i]
        calls = m.get("tool_calls") or []
        if m.get("role") != "assistant" or len(calls) < 2:
            out.append(m)
            i += 1
            continue
        results = {}
        j = i + 1
        while j < len(messages) and messages[j].get("role") == "tool":
            results[messages[j].get("tool_call_id")] = messages[j]
            j += 1
        for n, call in enumerate(calls):
            out.append({"role": "assistant", "content": m.get("content", "") if n == 0 else "", "tool_calls": [call]})
            out.append(results.get(call.get("id")) or {"role": "tool", "tool_call_id": call.get("id", ""),
                                                        "content": "(no result)"})
        i = j
    messages[:] = out


def _one_line(text: str, limit: int = REMARK_CHARS) -> str:
    """A model's comment as one line: its first non-empty line, at most `limit` characters."""
    line = next((ln.strip() for ln in text.strip().splitlines() if ln.strip()), "")
    line = re.sub(r"\s+", " ", line).strip("*_ ")
    if len(line) > limit:
        line = line[:limit].rsplit(" ", 1)[0].rstrip(",;:-") + "..."
    return line


def _table(headers: list[str], rows: list[list[str]], total: list[str] | None = None) -> list[str]:
    """A plain-text table (it reads the same in the terminal and in a .txt): numbers right-aligned."""
    body = rows + ([total] if total else [])
    widths = [max(len(str(r[i])) for r in [headers] + body) for i in range(len(headers))]
    numeric = [all(re.fullmatch(r"[$\d,.\- ]*", str(r[i])) for r in body) for i in range(len(headers))]

    def line(r):
        return "| " + " | ".join(str(c).rjust(w) if num else str(c).ljust(w)
                                 for c, w, num in zip(r, widths, numeric)) + " |"
    rule = "+" + "+".join("-" * (w + 2) for w in widths) + "+"
    out = [rule, line(headers), rule, *(line(r) for r in rows)]
    if total:
        out += [rule, line(total)]
    return out + [rule]


def _place(places: dict[str, int], model: str) -> str:
    """'1st', '=3rd' (shared), or '-' when it wasn't in the knockout."""
    if model not in places:
        return "-"
    p = places[model]
    suffix = "th" if 10 <= p % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(p % 10, "th")
    shared = list(places.values()).count(p) > 1
    return f"{'=' if shared else ''}{p}{suffix}"


def _keep_video(video: Path, folder: Path, label: str) -> None:
    """Copy a knockout game's video into an entrant's Videos folder as '<label>.mp4'."""
    try:
        folder.mkdir(parents=True, exist_ok=True)
        name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", label).strip(" .")
        shutil.copy2(video, folder / f"{name}.mp4")
    except OSError as err:
        say(f"    (couldn't copy the video to {folder}: {err})", "y")


def _short_name(model: str) -> str:
    return model.split("/")[-1]


def _find_seat(seats: list, who: str):
    """The contestant meant by `who`: its full model name, the name after the '/', or its Zook's
    name, exactly, else by a prefix, else by any part of one, as long as only one matches (any case)."""
    who = who.lower().strip()
    if not who:
        return None
    names = [(s, {s.model.lower(), _short_name(s.model).lower(), s.zook_name.lower()}) for s in seats]
    for fits in (lambda x: x == who, lambda x: x.startswith(who), lambda x: who in x):
        found = [s for s, n in names if any(fits(x) for x in n)]
        if len(found) == 1:
            return found[0]
    return None


def _tool_args(raw) -> tuple[dict | None, str]:
    """A tool call's arguments as a dict, or (None, the error to send back). Some models send
    "null" or nothing for a call without arguments."""
    if isinstance(raw, dict):
        return raw, ""
    try:
        args = json.loads(raw or "{}")
    except (json.JSONDecodeError, TypeError) as err:
        return None, f"ERROR: the arguments aren't valid JSON ({err}). Send a JSON object, e.g. {{\"ref\": \"Ant\"}}."
    if args is None:
        return {}, ""
    if not isinstance(args, dict):
        return None, f"ERROR: the arguments must be a JSON object of named parameters, not {type(args).__name__}."
    return args, ""


def _game_stats(r, green: str, red: str) -> str:
    """One line of a tournament game's result for the audience."""
    names = {"green": green, "red": red}
    v = r.verdict
    parts = [f"{r.status}, {r.sim_time:.0f} s simulated"]
    if r.scores:
        parts.append("scores " + ", ".join(f"{names.get(c, c)} {s:g}" for c, s in r.scores.items()))
    if v and v.decided_by != "none":
        parts.append(f"decided by {v.decided_by}")
    if r.finish_time is not None:
        parts.append(f"finish {r.finish_time:.1f} s after Go")
    if v and any(v.fell.values()):
        parts.append("fell: " + ", ".join(names.get(c, c) for c, f in v.fell.items() if f))
    return " | ".join(parts)


def _clip(text: str, words: int = SHOW_WORDS) -> str:
    parts = text.split()
    if len(parts) <= words:
        return text
    return " ".join(parts[:words]) + f" ... [+{len(parts) - words} words]"


def _short(args) -> str:
    if not isinstance(args, dict):
        return ""
    parts = []
    for k, v in args.items():
        s = json.dumps(v) if not isinstance(v, str) else repr(v)
        parts.append(f"{k}={s[:60]}{'...' if len(s) > 60 else ''}")
    return ", ".join(parts)


def main(argv: list[str] | None = None) -> None:
    import argparse

    cfg = load_config()
    a_cfg = cfg.arena
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--challenger", action="append", help="OpenRouter model id (repeatable); default: [arena] challengers")
    ap.add_argument("--challenge", help="challenge TOML (default: [arena] challenge, then bamzooki.toml challenge)")
    ap.add_argument("--max-turns", type=int, default=int(a_cfg.get("max_turns", 120)))
    ap.add_argument("--max-tokens", type=int, default=int(a_cfg.get("max_tokens", 4000)))
    ap.add_argument("--temperature", type=float, default=a_cfg.get("temperature"))
    ap.add_argument("--auto", action="store_true", help="run every queued challenger without the prompt, then show the leaderboard")
    ap.add_argument("--keep-entries", action="store_true",
                    help="carry on with the previous Zook Entries (default: move them to Archive/<date_time>/ "
                         "so every model starts afresh)")
    ap.add_argument("--verbose", "-v", action="store_true",
                    help="stream the model's reasoning and notes as it writes them, and show them and "
                         "its tool arguments in full (not cut to 100 words)")
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    challenge_path = a.challenge or a_cfg.get("challenge") or cfg.challenge
    if challenge_path and not Path(challenge_path).is_absolute():
        challenge_path = REPO_ROOT / challenge_path
    challenge = Challenge.load(challenge_path)
    queue = a.challenger or list(a_cfg.get("challengers", []))
    arena = Arena(challenge, queue, a.max_turns, a.max_tokens, a.temperature, a.auto, a.verbose)
    if not a.keep_entries and (moved := arena.archive_entries()):
        say(f"Previous Zook Entries archived to {moved}: every model starts afresh (--keep-entries to carry on).",
            "dim")
    asyncio.run(arena.repl())
