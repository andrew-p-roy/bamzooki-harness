"""BAMZOOKi Arena: let any OpenRouter model take the challenge, then talk to it.

    python tools/arena.py                                  # challengers from bamzooki.toml [arena]
    python tools/arena.py --challenger openai/gpt-5 --challenger anthropic/claude-sonnet-4.5
    python tools/arena.py --challenge challenges/short.toml
    python tools/arena.py --monitor                        # one screen: test/tournament games on the right
    python tools/arena.py --full-screen                    # test/tournament games in windowed full screen

For each challenger the runner starts the bamzooki MCP server (stdio) with the challenger's full
model name, working in the arena folder (outside the repo), and runs an agent loop over
OpenRouter's chat-completions API: the model sees only the MCP tools (plus preview/still images
when it supports vision). When it has submitted and presented its challenger Zook, you get a
prompt to talk to it. Commands:

    /next                 next challenger in the queue      /challenger <model>  a specific model
    /leaderboard          standings in this challenge       /status              current budget
    /open                 open the current entrant's latest video and stills
    /contest              knockout tournament between this session's qualified models (a round 1
                          qualifying WIN, or with no qualifying having put Zooks forward; at most
                          the rules' max_qualifiers); each round plays its contest (the final the
                          last), and the winner then makes a speech. Short rules: before each later
                          round the models still in design their Zook for it
    /exhibition <a> <b>   one match between any two Zooks submitted this session (doesn't count)
    /halloffame           the champions you added after a knockout (kept across sessions)
    /end                  finish: a table of every model's result, runs, tokens and cost (saved to
                          Sessions/), then Enter quits
    /list                 the queue                         /quit                leave
    anything else         a message to the current model (it may use its tools to answer)

While a model is working you can still type: a message is handed to it after its current step
(the tool call or game it's in), it takes it into account and carries on; /stop halts it there,
/next halts it and calls the next challenger (for a model that's stuck or not playing).
Each run starts afresh: the previous Zook Entries move to Archive/<date_time>/ (--keep-entries
carries on with them instead).

In /contest (and /exhibition), before and after each match, talk to one model with
/<model or Zook name> <message> or type to both; Enter plays on. Before a match, /pick <name>
predicts the winner.

--showtime adds the show, at the cost of extra model calls: an announcer (--announcer, default
anthropic/claude-haiku-5.5; --no-announcer) introduces each challenger (maker, release month,
country, trivia) and calls the games (--speak reads it aloud); in the knockout both models see the
other's Zook and say a line before each match, and afterwards they are interviewed with stills of
how the game ended; while a model presents its Zook, the Zook stands on a turntable in the engine
until /next; returning champions' Hall of Fame records are mentioned. Its qualifying and
knockout games also play their sound live: a starting beeper on the countdown and the contest's
music looping from Go (the game videos and the highlights have it either way). And through the
design phases (trials and practice games included) the design songs play one after another, round
and round, fading out for a qualifying game and for the knockout's games. --nomusic: no music
anywhere, live or in the videos (the beeps stay).

--verbose streams its reasoning and notes as it writes them and shows them, and its tool
arguments, in full (normally each reply is shown when complete, cut to 100 words).

The API key comes from OPENROUTER_API_KEY (environment or the repo's .env file).
"""

from __future__ import annotations

import asyncio
import json
import os
import queue
import random
import re
import shutil
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from . import music, showtime, tournament
from .announcer import DEFAULT_MODEL as DEFAULT_ANNOUNCER
from .announcer import Announcer
from .challenge import ChallengeState, Session, plan_from_config
from .commentary import DEFAULT_MODEL as COMMENTATOR
from .config import REPO_ROOT, load_config
from .manual import MODEL_INSTRUCTIONS
from .sdk.store import entrant_folder

API = "https://openrouter.ai/api/v1"
PROMPT_PATH = MODEL_INSTRUCTIONS / "Agent Prompt.md"
KICKOFF = ("Begin the challenge now. Start with get_challenge. Work only through the bamzooki tools, and "
           "finish by putting your Zooks forward with submit_design as get_challenge describes, followed by your "
           "presentation to the audience.")
SHOW_WORDS = 100  # model text between tool calls is cut to this many words on screen (not in transcripts)
NUDGE = ("You haven't put all your Zooks forward yet (get_challenge shows what's still to submit), and in the "
         "knockout you FORFEIT any match you have no Zook for. Keep going with the bamzooki tools; when you're ready "
         "(or out of runs), put the rest forward with submit_design, each a distinctly different design, then "
         "present your challenger.")
# Short rules: a later round's design phase, for the models still in.
ROUND_KICKOFF = ("[{round}] You're through to the {round}: round {k} of {n}. Design a NEW Zook for this round now: "
                 "{what}. Your limits start afresh for this round (get_challenge shows them and the round). It MUST "
                 "be a distinctly different design from the Zooks you've already put forward: submit_design refuses "
                 "one that is too close. {qualifying}Put it forward with submit_design, then present it to the "
                 "audience. If you don't put a Zook forward for this round, you FORFEIT its match.")
CHOOSE = ("[The {event}, {round}] This round's contest is {contest}: {rules}\nYour opponent is {opp}. Pick the Zook "
          "you'll play: {options}.{once} Answer with the Zook's name only (no tools).")
# --showtime: the presentation is read out on stage, so it's kept short ([arena] presentation_words).
SHOW_PRESENTATION = " Keep your presentation to at most {words} words: it is read out on stage."
PRE_MATCH = ("The {contest}, {round}: your Zook {mine} is about to play {opp_model}'s Zook {opp_zook} "
             "({opp_card}). {seen}{heard}Say something to the audience before the game: a comment, some "
             "trash talk, or a boast about {mine}. Answer in ONE line (no line breaks, no lists) and don't "
             "call any tools.")
AUDIENCE = "[{context}] Someone in the audience says to you: {msg}\n(Reply to them; don't call any tools.)"
INTERVIEW = ("[{context}] Post-match interview. {seen}The announcer asks you: {question}\n"
             "Answer the audience in at most 60 words (no lists, and don't call any tools).")
STOCK_QUESTIONS = ("{name}, what won you that game?", "{name}, what happened out there?")  # no announcer
SELECTION_SECONDS = 8.0  # the selection screen stays up at least this long before a game
REMARK_CHARS = 280  # the pre-match comment is one line of at most this many characters
INTERJECTION = ("[A message from the audience, sent while you were working. Take it into account (answer "
                "briefly if it asks something), then carry on where you were.]\n\n{}")

C = {"dim": "\033[2m", "b": "\033[1m", "g": "\033[32m", "y": "\033[33m", "c": "\033[36m", "m": "\033[35m",
     "r": "\033[31m", "a": "\033[1;33m", "x": "\033[0m"}  # a: the announcer


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
    design: str | None = None  # the latest design put forward ("folder/Name@n")
    qualified: bool = False  # in the knockout: a qualifying win, or (no qualifying) a Zook put forward
    single_tool: bool = False  # its provider takes one tool call per reply (found out on the way)
    submission: dict | None = None  # the latest submit_design result
    entries: list[dict] = field(default_factory=list)  # every Zook put forward: {"design", "round", ...}
    played: set[str] = field(default_factory=set)  # team Zooks already played in the knockout (hidden)

    def for_round(self, round_no: int | None) -> str | None:
        """The design put forward for a round (1-based), if any."""
        return next((x["design"] for x in self.entries if round_no is not None and x.get("round") == round_no), None)

    def zooks(self) -> list[str]:
        """Every design it put forward, in order, each once."""
        return list(dict.fromkeys(x["design"] for x in self.entries))
    presented: bool = False  # its presentation has been saved
    facts: dict = field(default_factory=dict)  # showtime.model_facts: maker, home, release month...
    to_call: dict | None = None  # a challenge game result the announcer hasn't called yet


class Arena:
    def __init__(self, session: Session, queue: list[str], max_turns: int = 120, max_tokens: int = 4000,
                 temperature: float | None = None, auto: bool = False, verbose: bool = False,
                 show: bool = False, announcer: str | None = DEFAULT_ANNOUNCER,
                 announcer_style: str = "ringside", speak: bool = False, voice: str | None = None,
                 monitor: bool = False, fullscreen: bool = False, commentator: str | None = None,
                 play_music: bool = True):
        self.cfg = load_config()
        # --monitor: test and tournament games put the engine window on the right half of the main
        # screen with a 4:3 picture (at most 1080p's half) in it, leaving the left for this terminal.
        # --full-screen: they run in windowed full screen instead (a borderless window over the whole
        # screen, a 4:3 picture of at most 1024x768 centred in it). Either way, other games (and,
        # without them, every game) stay in the engine's own window.
        from .runner import MONITOR_VIEW

        self.position = "right-half" if monitor else "none"
        self.view_fit = MONITOR_VIEW if monitor else None
        self.fullscreen = fullscreen
        self.plan = session  # the challenge session: its rounds, the same for every model
        if session.round_by_round:
            session.current = 0  # short rules: the arena opens each round; everyone designs round 1 first
        self.challenge = session.rules
        self.queue = list(queue)
        self.max_turns = max_turns
        self.auto = auto
        self.verbose = verbose
        self.keyboard: Keyboard | None = None  # started by repl()
        self.skip = False  # /next typed while a model worked: move on to the next challenger
        self.ending = False  # /end: finish with the session's table
        self.knockout_places: dict[str, int] = {}  # model -> place in the last /contest
        self.picks = [0, 0]  # the audience's /pick predictions in the last /contest: right, made
        self.api_key = load_api_key()
        self.router = OpenRouter(self.api_key, max_tokens, temperature)
        # --showtime: the announcer, the models seeing each other's Zooks, the pre-match lines, the
        # interviews and Hall of Fame mentions (all extra model calls). Off, the arena only calls the
        # models for their own work, the champion's speech and what the audience says to them.
        self.show = show
        # The music under the games (False: --nomusic) and, with the show, the design phases' songs.
        from .runner.audio import Playlist

        self.play_music = play_music
        self.design_music = Playlist(music.design_songs(music.load_tracks())) if show and play_music else None
        self.announcer = Announcer(self.router, announcer, announcer_style) if show and announcer else None
        self.voice = None
        if speak and self.announcer:
            from .voice import KOKORO_FILES, KOKORO_SPEED, Voice

            self.voice = Voice(voice, cache=self.cfg.cache_dir / "kokoro",
                               speed=float(self.cfg.arena.get("voice_speed", KOKORO_SPEED)))
            if self.voice.kokoro and not all((self.cfg.cache_dir / "kokoro" / f).exists() for f in KOKORO_FILES):
                say("(--speak: downloading the Kokoro voice model, about 340 MB, once)", "dim")
            if not self.voice.available:
                say("(--speak: no speech engine found on this system; the announcer stays on screen)", "y")
                self.voice = None
        # Live commentary on the show's games (a fast vision model writing George's lines): needs the voice.
        self.commentator = commentator if self.voice and commentator else None
        self.arena_dir = self.cfg.arena_dir
        self.arena_dir.mkdir(parents=True, exist_ok=True)
        self.session_file = self.cfg.zoo_dir / "session.json"  # written by save_session(), read by the MCP servers
        self.show_log = self.cfg.transcripts_dir / "Announcer" / f"{time.strftime('%Y-%m-%d_%H-%M-%S')}.md"
        self.entrant: Entrant | None = None
        self.entrants: dict[str, Entrant] = {}  # everyone called this session, in order
        self.session = None
        self.tools: list[dict] = []
        self._stack = None

    def save_session(self) -> None:
        """The planned rounds go beside the entries, where every model's MCP server reads them."""
        self.plan.save(self.session_file)

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
            args=["-m", "bamzooki.mcp_server", "--model", model, "--session", str(self.session_file),
                  "--position", self.position] + (["--view-fit", "{},{}".format(*self.view_fit)] if self.view_fit else [])
                 + (["--full-screen"] if self.fullscreen else []) + (["--live-sound"] if self.show else [])
                 + ([] if self.play_music else ["--no-music"]),
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

    def rules_section(self) -> str:
        """This session's rules (mode, visibility, how many Zooks, limits, qualifying, the knockout)
        for the end of every model's system prompt."""
        return "\n\nThis session's rules:\n" + "\n".join(f"- {line}" for line in self.plan.briefing())

    def _rounds_line(self) -> str:
        """The session's rounds for the challenger's card: one round's game and map, the rounds'
        contests and opponents, or how many secret rounds there are."""
        s = self.plan
        if s.hidden:
            return f"  |  {len(s.rounds)} round(s), contests secret"
        if len(s.rounds) == 1:
            return self._game_and_map(s.rounds[0].contest) + f"  |  opponent: {s.rounds[0].opponent}"
        return "  |  " + "  ".join(f"{i}. {m.contest} v {m.opponent}" for i, m in enumerate(s.rounds, 1))

    def _game_and_map(self, name: str) -> str:
        """'  |  game: Zook Sumo  |  map: Polo Table, Table Sumo': a contest and the props its arena is
        built from ("" if the install can't be read)."""
        from .install import Install
        from .runner import contests

        try:
            inst = Install.discover()
            data = contests.load_contest_file(inst, contests.find(name, inst).contest_file)
        except Exception:
            return f"  |  game: {name}"
        props = sorted({a.get("agent") for a in (data.get("agents") or {}).values()
                        if isinstance(a, dict) and a.get("agent") not in (None, "ZookPlaceHolder", "Target")})
        return f"  |  game: {name}" + ("  |  map: " + ", ".join(_spaced(p) for p in props) if props else "")

    def _presentation_limit(self) -> str:
        """With the show, the models are asked to keep their presentation short (read in full anyway)."""
        return SHOW_PRESENTATION.format(words=int(self.cfg.arena.get("presentation_words", 120))) if self.show else ""

    def _speak_as(self, model: str, text: str) -> None:
        """--speak: a contestant's own words, in full, in its own voice (Kokoro; voice.LAB_VOICES, or
        [arena.voices])."""
        if not text or not getattr(self, "voice", None) or not self.voice.kokoro:
            return
        from .voice import model_voice

        self.voice.say(text, voice=model_voice(model, self.cfg.arena.get("voices")))

    def _commentary_brief(self, event: str, round_name: str, green, red, cards: dict) -> str:
        """What the live commentator knows about a game: the event, who made each Zook, the tale of the
        tape, what each looks like and its designer's notes."""
        a, b = cards.get(green.model), cards.get(red.model)
        lines = [f"{event}, {round_name or 'match'}: GREEN side is {green.zook_name}, designed by {green.model}; "
                 f"RED side is {red.zook_name}, designed by {red.model}. (Green and red are only the sides, not the "
                 "Zooks' colours.)"]
        if a and b:
            lines += ["Tale of the tape:"] + showtime.tale_of_the_tape(a, b, "GREEN", "RED")
            if looks := showtime.tell_apart(a, b):
                lines.append(f"What they look like: {looks}")
            for side, card in (("GREEN", a), ("RED", b)):
                if card.notes:
                    lines.append(f"{side} designer's notes: {card.notes[:300]}")
        return "\n".join(lines)

    def _announce(self, text: str, who: str = "ANNOUNCER", live: bool = False) -> None:
        """The announcer speaks: printed in its own colour and kept in Transcripts/Announcer/. live: commentary,
        which replaces anything not yet spoken."""
        if not text:
            if (host := getattr(self, "announcer", None)) and host.last_error:
                say(f"\n  [the announcer missed that one: {host.last_error}"
                    f"{'; it has gone quiet for the rest of the show' if host.quiet else ''}]", "dim")
            return
        say(f"\n  {who}: ", "a", end="")
        say(text, "a")
        if getattr(self, "voice", None):
            (self.voice.say_now if live else self.voice.say)(text)
        try:
            self.show_log.parent.mkdir(parents=True, exist_ok=True)
            with self.show_log.open("a", encoding="utf-8") as f:
                f.write(f"\n### {who} ({time.strftime('%H:%M:%S')})\n\n{text}\n")
        except OSError:
            pass

    async def _call_challenge_game(self, e: Entrant) -> None:
        """The announcer calls a challenge game from its result and stills."""
        d, e.to_call = e.to_call, None
        game = d.get("game") or {}
        how = " at the time limit" if d.get("outcome") == "draw" and d.get("status") == "timeout" else ""
        mine = showtime.zook_card(self.cfg.zoo_dir, d.get("design", "?"))
        side = game.get("side", "green")
        names = {side: mine.name, "red" if side == "green" else "green": game.get("opponent", "its opponent")}
        details = []
        if d.get("finish_after_go_s") is not None:
            details.append(f"decided {d['finish_after_go_s']} s after Go")
        if fell := [names.get(c, c) for c, f in (d.get("fell") or {}).items() if f]:
            details.append("fell off: " + ", ".join(fell))
        rnd = f"round {d['round']} of {len(self.plan.rounds)}" if len(self.plan.rounds) > 1 else ""
        if rnd:
            details.append(f"qualifying {rnd}")
        won = d.get("outcome") == "win"
        if d.get("round", 1) == 1:
            details.append("a win qualifies it for tonight's knockout" if won else
                           "it needed a win to get into tonight's knockout, so it won't be in it")
        else:
            details.append("a win lets it play its knockout match" if won else
                           "it needed a win to play its knockout match, so it forfeits it")
        stills = showtime.image_part(d["stills"]) if d.get("stills") else None
        apart = (f"{showtime.tell_apart(mine)} The other one is {game.get('opponent', 'its opponent')}."
                 if mine.looks() else "")
        self._announce(await self.announcer.challenge_call(
            e.model, mine.name, game.get("contest", "?"), game.get("opponent", "?"),
            f"{str(d.get('outcome', '?')).upper()}{how}", "; ".join(details), stills, apart))

    # --- the agent loop ---
    async def start(self, model: str) -> None:
        self._showcase_stop()  # /next (or another challenger): the last presentation is over
        self._design_music(True)
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
        self._restore(self.entrant)  # earlier sessions' submissions (with --keep-entries)
        tdir = self.cfg.transcripts_dir / folder
        tdir.mkdir(parents=True, exist_ok=True)
        self.entrant.transcript = tdir / f"{time.strftime('%Y-%m-%d_%H-%M-%S')}.md"
        self.entrant.transcript.write_text(f"# {model} - {self.challenge.title}\n", encoding="utf-8")
        self.entrant.facts = showtime.model_facts(model, info)
        if self.show and (record := showtime.arena_record(showtime.hall_of_fame(self.arena_dir), model)):
            self.entrant.facts["arena_record"] = record
        say(f"\n=== Challenger: {model} ===", "b")
        say(f"    {showtime.card_line(self.entrant.facts)}", "dim")
        say(f"    challenge: {self.challenge.title}{self._rounds_line()}", "dim")
        if self.announcer:
            self._announce(await self.announcer.introduce(len(self.entrants), self.challenge.title,
                                                          self.entrant.facts))
        await self._connect(model)
        system = PROMPT_PATH.read_text(encoding="utf-8") + self.rules_section() + (
            f"\n\nYou are registered as the entrant \"{model}\" (your full model name); you don't need "
            f"to call register_entrant.")
        kickoff = KICKOFF + self._presentation_limit()
        self.entrant.messages = [{"role": "system", "content": system}, {"role": "user", "content": kickoff}]
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
                if not calls:  # an answer (its presentation, or to the audience), not notes while working
                    self._speak_as(e.model, text)
            if not calls and text and e.submitted and e.entries and not e.presented:
                self._save_presentation(e, text)
                self._showcase_start(e)
            if not calls:
                passed, stop = self._interjections(e)  # typed while it was answering
                if passed and not stop:
                    continue
                if not e.submitted and nudges > 0 and not stop:
                    nudges -= 1
                    nudge = NUDGE + self._presentation_limit()
                    e.messages.append({"role": "user", "content": nudge})
                    self._log("runner", nudge)
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
            if e.to_call and self.announcer:
                await self._call_challenge_game(e)
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
            # a qualifying game plays its contest's music: the design songs make way for it
            qualifying = name == "submit_design" and self.plan.qualifying_in((self.plan.current or 0) + 1)
            try:
                if qualifying:
                    self._design_music(False)
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
            finally:
                if qualifying:
                    self._design_music(True)
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
        elif name == "test_zook" and d.get("moved"):
            m = d["moved"]
            say(f"     Test: {m.get('forward_cm_per_s')} cm/s forward over {m.get('seconds')} s"
                f"  (drift {m.get('drift_deg')} deg, fell {m.get('fell')})", "g")
        elif name == "run_contest" and "winner" in d:
            res = (f"winner {d['winner_zook']}" if d.get("winner_zook") else
                   "draw (time limit)" if d.get("status") == "timeout" else
                   "draw" if d.get("status") == "finished" else f"no result ({d.get('status')})")
            say(f"     {d.get('contest')}: {d.get('green')} vs {d.get('red')} -> {res}", "g")
        elif name == "submit_design" and d.get("design") and not d.get("error"):
            self._took(e, d)
            n = len(self.plan.rounds)
            if "outcome" in d:
                e.to_call = d
                how = " (time limit)" if d["outcome"] == "draw" and d.get("status") == "timeout" else ""
                say(f"     SUBMITTED {d['design']} for round {d['round']} of {n}: {d['outcome'].upper()}{how} "
                    f"(score {d['score']})", "m")
                if d.get("round", 1) == 1:
                    say("     QUALIFIED for the /contest knockout" if e.qualified else
                        "     not qualified (it needed a round 1 qualifying win)", "m" if e.qualified else "y")
                elif d["outcome"] != "win":
                    say("     lost its qualifying game: it forfeits this round's knockout match", "y")
            elif d.get("round") is None:
                say(f"     TEAM ZOOK {len(e.entries)} of {n}: {d['design']}", "m")
            else:
                contest = "" if self.plan.hidden else f" ({self.plan.rounds[d['round'] - 1].contest})"
                say(f"     PUT FORWARD {d['design']} for round {d['round']} of {n}{contest}", "m")
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

    def _showcase_start(self, e: Entrant) -> None:
        """With the show: while its maker presents it, the submitted Zook stands on a turntable in the
        engine (windowed like the games), until /next or anything else that needs the engine."""
        if not self.show or not e.design:
            return
        self._showcase_stop()
        from .runner import Engine
        from .sdk.store import Zoo

        try:
            engine = Engine()
            zook = Zoo(engine.install, examples_root=engine.root / "Teams" / "examples" / "Creatures").resolve(e.design)
        except Exception as err:  # no install, or the design can't be found: the show goes on without it
            say(f"    [no showcase: {err}]", "dim")
            return
        cancel = threading.Event()
        thread = threading.Thread(target=engine.showcase, args=(zook.path, zook.name, cancel), daemon=True,
                                  kwargs={"position": self.position, "view_fit": self.view_fit,
                                          "fullscreen": self.fullscreen})
        thread.start()
        self._showcase = (thread, cancel)

    def _design_music(self, on: bool) -> None:
        """The design songs (with the show): on through the design phases, off for the games."""
        if self.design_music:
            (self.design_music.play if on else self.design_music.pause)()

    def _showcase_stop(self) -> None:
        if showcase := getattr(self, "_showcase", None):
            thread, cancel = showcase
            cancel.set()
            thread.join(timeout=15)
            self._showcase = None

    def _save_presentation(self, e: Entrant, text: str) -> None:
        """Zook Entries/<model>/Presentation - <challenge>.txt: the model's presentation of its
        challenger, under a header with the facts (design, result, runs, cost; no video links)."""
        folder = self.cfg.zoo_dir / entrant_folder(e.model)
        state = ChallengeState(self.challenge, folder)
        header = [
            f"BAMZOOKi Arena - {e.model} presents its challenger",
            f"Challenge:       {self.challenge.title} ({self.challenge.name}){self._rounds_line()}",
            f"Date:            {time.strftime('%Y-%m-%d %H:%M')}",
            *[f"Put forward:     {_entry_line(x)}" for x in e.entries],
            f"Knockout:        {'qualified' if e.qualified else 'not qualified'}",
            f"Test runs used:  {state.total_runs_used}",
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

    def _restore(self, e: Entrant) -> None:
        """Submissions saved by an earlier session (--keep-entries) count: the model carries on from them."""
        d = self.cfg.zoo_dir / entrant_folder(e.model)
        if (d / f"challenge-{self.challenge.name}.json").exists():
            for sub in ChallengeState(self.challenge, d).submissions:
                self._took(e, sub)

    def _took(self, e: Entrant, d: dict) -> None:
        """A Zook put forward: remember it, and whether the model is now in the knockout and done."""
        e.entries.append(d)
        e.design, e.submission = d["design"], d
        if not self.plan.round_by_round or d.get("round") == 1:  # short rules: round 1 gets a model in
            e.qualified = e.qualified or (d.get("outcome") == "win" if self.plan.qualifying else True)
        e.submitted = self._submitted(e)

    def _submitted(self, e: Entrant) -> bool:
        """Has it put forward every Zook it owes now: the open round's (short rules), or all of them?"""
        if self.plan.round_by_round:
            return e.for_round((self.plan.current or 0) + 1) is not None
        return len(e.entries) >= len(self.plan.rounds)

    @property
    def cap(self) -> int | None:
        """Models in the knockout at most (the rules' max_qualifiers; None = no limit)."""
        return self.plan.rules.max_qualifiers

    def _field_full(self) -> bool:
        """The knockout's places are all taken (max_qualifiers): no more challengers are called."""
        return bool(self.cap) and sum(e.qualified for e in self.entrants.values()) >= self.cap

    # --- commands ---
    def leaderboard(self) -> None:
        rows = []
        root = self.cfg.zoo_dir
        if root.is_dir():
            for d in root.iterdir():
                if (d / f"challenge-{self.challenge.name}.json").exists():
                    st = ChallengeState(self.challenge, d)
                    subs = st.submissions
                    score = sum(x.get("score", 0) for x in subs) if self.plan.qualifying else len(subs)
                    ok = (any(x.get("outcome") == "win" and x.get("round") == 1 for x in subs)
                          if self.plan.qualifying else bool(subs))
                    rows.append((score, d.name, subs, st.total_runs_used, ok))
        say(f"\n  Leaderboard - {self.challenge.title}", "b")
        if not rows:
            say("  (no entrants yet)", "dim")
        for score, name, subs, used, ok in sorted(rows, key=lambda r: -r[0]):
            res = "; ".join(_entry_line(x) for x in subs) or "not submitted"
            say(f"  {name:45s} {res}  ({used} runs){'  qualified' if ok else ''}")

    def hall(self) -> None:
        """/halloffame: the knockout champions added so far, and the most titles."""
        hall = showtime.hall_of_fame(self.arena_dir)
        say(f"\n  Hall of Fame ({self.arena_dir / showtime.HALL_OF_FAME})", "b")
        if not hall:
            say("  (no champions yet: win a /contest knockout to get in)", "dim")
            return
        rows = [[str(h.get("date", ""))[:16], str(h.get("champion", "?")), str(h.get("zook_name", "?")),
                 str(h.get("contest", "")), str(h.get("field", "")), str(h.get("runner_up") or "-")] for h in hall]
        for line in _table(["Date", "Champion", "Zook", "Contest", "Field", "Runner-up"], rows):
            say(f"  {line}")
        from collections import Counter

        best = Counter(h.get("champion") for h in hall).most_common(3)
        say("  Most titles: " + ", ".join(f"{m} ({n})" for m, n in best), "m")

    def open_media(self) -> None:
        if not self.entrant or not self.entrant.last_media:
            say("Nothing to open yet.", "dim")
            return
        for p in self.entrant.last_media:
            if Path(p).exists() and hasattr(os, "startfile"):
                os.startfile(p)  # noqa: S606 (local media files we produced)
                say(f"opened {p}", "dim")

    async def exhibition(self, arg: str) -> None:
        """/exhibition <a> <b>: one match, with the whole show, between two Zooks submitted this
        session (qualified or not). It doesn't change the knockout ranking."""
        seats = [tournament.Seat(e.model, e.design) for e in self.entrants.values() if e.design]
        names = ", ".join(_short_name(s.model) for s in seats) or "nobody has submitted yet"
        picked = [_find_seat(seats, w) for w in arg.split()]
        if len(picked) != 2 or None in picked or picked[0] is picked[1]:
            say(f"usage: /exhibition <model> <model>, two of this session's submitted Zooks: {names} "
                "(a model's name or its Zook's name; any part only one of them has)", "y")
            return
        await self.contest(picked, exhibition=True)

    async def contest(self, seats: list[tournament.Seat] | None = None, exhibition: bool = False) -> None:
        """Knockout between this session's qualified models, then the winner's speech; the audience's
        next messages go to the winner. Each round plays its contest (Session.knockout_round: the final
        is the last round; a random one is drawn as the round starts, the same for all its matches), and
        in each match a model plays the Zook it put forward for that round, or picks one when the
        contest is announced (hidden challenges: each team Zook once). A model with no Zook for the
        round (or no team Zook left, or, qualifying in every round, a lost qualifying game) forfeits
        the match. Short rules: the rounds are fitted to the field first (Session.fit), and before
        each round after the first the models still in design their Zook for it (design_round).
        A field that isn't a power of two has byes in the first round, for the best qualifiers.
        exhibition: one match between the two given seats on the last round's contest, without the
        speech or a ranking."""
        self._showcase_stop()
        self._design_music(False)  # the games have their own music
        seeded = False
        if seats is None:
            ranked = self._seeding([e for e in self.entrants.values() if e.qualified and e.design])
            if self.cap and len(ranked) > self.cap:
                say(f"{len(ranked)} models qualified; the knockout takes the first {self.cap} (max_qualifiers).", "y")
                ranked = ranked[:self.cap]
            seats = [tournament.Seat(e.model, e.design) for e in ranked]
            seeded = self.plan.qualifying  # the byes go to the best qualifiers (else to random models)
        if len(seats) < 2:
            names = ", ".join(s.billing() for s in seats) or "nobody yet"
            how = ("a round 1 qualifying win" if self.plan.qualifying else "Zooks put forward")
            say(f"A contest needs two qualified models ({how}); qualified: {names}.", "y")
            return
        from .runner import Engine, Job, contests
        from .sdk.store import Zoo

        engine = Engine()
        engine.prepare()
        zoo = Zoo(engine.install, examples_root=engine.root / "Teams" / "examples" / "Creatures")
        pool = [n for n, c in contests.catalog(engine.install).items() if c.players == 2 and c.in_pack]
        if self.plan.round_by_round and not exhibition:
            total = tournament.rounds_needed(len(seats))
            for change in self.plan.fit(total, pool, zoo.example_names()):
                say(f"  {len(seats)} models in the knockout need {total} round(s): {change}", "y")
            self.save_session()
        tracks = music.load_tracks() if self.play_music else {}  # no tracks: silent games and reel
        kind = "exhibition" if exhibition else "knockout"
        # the round's contest and music, its session round ("slot", 1-based; None = an extra early
        # round), video titles, and the last game's (video, finish)
        now = {"round": "", "game": 0, "last": None, "info": None, "event": "", "track": None, "slot": None}
        played: list[str] = []  # this knockout's contests, round by round
        used: dict[str, set[str]] = {s.model: set() for s in seats}  # team Zooks already played (hidden)
        reel: list[showtime.ReelGame] = []  # every game, for the highlights
        cards: dict[str, showtime.ZookCard] = {}  # each model's card for the Zook it's playing now
        picks = [0, 0]  # the audience's /pick predictions: right, made
        pictures = Path(tempfile.mkdtemp(prefix="bamzooki-knockout-"))  # end-of-game stills, deleted after
        loop = asyncio.get_running_loop()
        matches = [len(seats) - 1]  # matches still to play (a knockout of n has n - 1), to word the prompt

        def start_round(index: int, total: int, round_name: str, field: list[tournament.Seat]) -> None:
            """The round's contest: the session's round for it, or drawn at random now (not one played
            or still to come while there are others), announced to everyone. Short rules, after the
            first round: the models still in design their Zook for it first (told the contest when
            it's visible; hidden, it's drawn and announced once their Zooks are in)."""
            if exhibition:
                index, total = 0, 1
            slot = len(self.plan.rounds) - total + index
            m = self.plan.rounds[slot] if slot >= 0 else None
            now.update(slot=slot + 1 if m else None)
            label = "ANNOUNCER" if self.announcer else "ROUND"
            design = self.plan.round_by_round and index > 0 and m is not None and not exhibition

            def contest_of_round() -> str:
                name = m.contest if m and m.contest else ""
                if not name:
                    ahead = {r.contest for r in self.plan.rounds[max(slot + 1, 0):]}
                    name = random.choice([c for c in pool if c not in played and c not in ahead]
                                         or [c for c in pool if c not in played] or pool)
                info = contests.find(name, engine.install)
                played.append(info.name)
                now.update(info=info, event=f"{info.name} {kind}",
                           track=music.pick_track(tracks, self.challenge, info.name))
                return info.name

            if design and not self.plan.hidden:
                name = contest_of_round()
                self._announce(f"{round_name}! This round's contest is {name}, and the {len(field)} models still in "
                               f"now design a new Zook for it.", label)
            elif design:
                self._announce(f"{round_name}! The {len(field)} models still in now design a new Zook for it, "
                               f"without knowing the contest.", label)
            if design:
                asyncio.run_coroutine_threadsafe(self.design_round(field, slot, round_name), loop).result()
            if not design or self.plan.hidden:
                self._announce(f"{round_name}! This round's contest is {contest_of_round()}.", label)

        def pick(a: tournament.Seat, b: tournament.Seat, round_name: str) -> tuple[str | None, str | None]:
            """Each side's Zook for the match; None = it has none for this round and forfeits (then
            the other isn't asked, and plays nothing from its team)."""
            options = {s.model: self._knockout_options(self.entrants[s.model], now["slot"], used[s.model],
                                                       exhibition) for s in (a, b)}
            if not (options[a.model] and options[b.model]):
                now["forfeits"] = {s.model: self._forfeit_reason(self.entrants[s.model], now["slot"])
                                   for s in (a, b) if not options[s.model]}
                # the side that can play goes through with the Zook it would have played (its round's own)
                return tuple(None if not options[s.model] else options[s.model][0] if len(options[s.model]) == 1
                             else s.zook for s in (a, b))  # type: ignore[return-value]
            return tuple(asyncio.run_coroutine_threadsafe(  # type: ignore[return-value]
                self.choose_zook(me, opp, now, round_name, options[me.model],
                                 used[me.model] if self.plan.team and now["slot"] and not exhibition else None),
                loop).result()
                for me, opp in ((a, b), (b, a)))

        def play(green: tournament.Seat, red: tournament.Seat, seed: int) -> tuple[str, str | None, str]:
            self._selection_stop()  # the selection screen gives way to the game
            info = now["info"]
            job = Job(info, zoo.resolve(green.zook).path, zoo.resolve(red.zook).path, seed=seed,
                      duration=self.challenge.time_limit, video=True, model="Tournament", countdown=True,
                      music=now["track"], live_sound=self.show,  # the videos get the sound either way
                      position=self.position, view_fit=self.view_fit, fullscreen=self.fullscreen)
            if self.commentator:
                from .commentary import Commentator

                job.commentary = Commentator(self.api_key,
                                             self._commentary_brief(now["event"], now["round"], green, red, cards),
                                             lambda line: self._announce(line, "COMMENTARY", live=True),
                                             model=self.commentator, duration=self.challenge.time_limit)
            r = engine.run(job, name=f"cup-{info.contest_file}")
            now["game"] += 1
            now["last"] = (r.video, r.finish_time, r.video_lead) if r.video else None
            if r.video:  # for the highlights, and a copy for each competitor beside its test videos
                reel.append(showtime.ReelGame(r.video, f"{now['event']} - {now['round']} - game {now['game']}",
                                              (green.model, green.zook_name), (red.model, red.zook_name)))
                for me, opp in ((green, red), (red, green)):
                    if me.folder == "examples":
                        continue
                    _keep_video(r.video, self.cfg.zoo_dir / me.folder / "Videos",
                                f"{'Exhibition' if exhibition else 'Knockout ' + now['round']} game {now['game']} - "
                                f"{self.challenge.name} - {info.name} - "
                                f"{me.zook_name} vs {opp.zook_name} ({opp.model})")
            return r.status, r.winner, _game_stats(r, green.zook_name, red.zook_name)

        def before(a: tournament.Seat, b: tournament.Seat, round_name: str) -> None:
            """The selection screen (several rounds), the tale of the tape and the announcer's build-up;
            both models see the other's Zook and comment (the second hears the first), then the audience
            may answer."""
            # "Semi-finals" -> "Semi-final" for one match's videos
            now["round"], now["game"] = round_name[:-1] if round_name.endswith("finals") else round_name, 0
            for s in (a, b):
                cards[s.model] = showtime.zook_card(self.cfg.zoo_dir, s.zook)
            if len(self.plan.rounds) > 1:
                self._selection_start(zoo, now["info"].name, a, b, f"{round_name}: {now['info'].name}")
            event = now["event"]

            async def talk():
                await self.preview_match(event, round_name, a, b, cards[a.model], cards[b.model], pictures)
                await self.trash_talk(event, round_name, a, b, cards)
                now["pick"] = {}
                await self.match_talk([a, b], f"{event}, {round_name}: "
                                              f"{a.billing()} v {b.billing()}, about to play", "to start the game",
                                      pick=now["pick"])
            asyncio.run_coroutine_threadsafe(talk(), loop).result()

        def after(bout: tournament.Bout) -> None:
            matches[0] -= 1
            if not bout.games:  # a forfeit: no game to talk about
                win, lose = _short_name(bout.winner.model), _short_name(bout.loser.model)
                why = now.pop("forfeits", {})
                self._announce(f"Neither {win} nor {lose} can play this round! A coin toss sends {win} through."
                               if bout.how == "both forfeit" else
                               f"{lose} {why.get(bout.loser.model, 'has no Zook for this round')} and forfeits: "
                               f"{win} goes through without a game.", "ANNOUNCER" if self.announcer else "ROUND")
                return
            result = (f"{bout.winner.billing()} beat {bout.loser.billing()} in {len(bout.games)} game(s)"
                      + (" on a coin toss" if bout.how == "coin toss" else ""))
            nxt = "for the next match" if matches[0] else "to finish" if exhibition else "for the final ranking"
            last, now["last"] = now["last"], None
            if picked := (now.pop("pick", None) or {}).get("seat"):
                right = picked is bout.winner
                picks[0], picks[1] = picks[0] + right, picks[1] + 1
                say(f"\n  The audience picked {picked.zook_name}: "
                    + ("called it!" if right else "wrong this time."), "m" if right else "y")
                result += f". The audience had picked {picked.zook_name}" + (" and called it" if right else "")
            event = now["event"]

            async def talk():
                await self.post_match(event, now["round"], bout, result, last, pictures,
                                      showtime.tell_apart(cards[bout.a.model], cards[bout.b.model]))
                await self.match_talk([bout.a, bout.b], f"{event}, just played: {result}", nxt)
            asyncio.run_coroutine_threadsafe(talk(), loop).result()

        title = "an exhibition match" if exhibition else f"{len(seats)} qualified models"
        say(f"\n*** THE {kind.upper()}: {title} ***", "b")
        for s in seats:
            e = self.entrants[s.model]
            say(f"    {s.model}  [{', '.join(z.split('/', 1)[-1] for z in e.zooks()) or s.zook}]")
        say("    Leave the game window in front while the games play. Between games, talk to a model with "
            "/<its name or its Zook's name> <message>, type to both, or press Enter to go on.", "dim")
        try:
            rounds, places = await asyncio.to_thread(tournament.knockout, seats, play, say, before=before,
                                                     after=after, start_round=start_round, pick=pick,
                                                     round_title="Exhibition match" if exhibition else None,
                                                     seeded=seeded)
        finally:
            self._selection_stop(0)
            shutil.rmtree(pictures, ignore_errors=True)
            if self.plan.round_by_round and not exhibition:
                self.plan.current = 0  # new challengers called after it design round 1 again
                self.save_session()
        champ = places[0][1]
        final = next(b for b in rounds[-1] if b.winner.model == champ.model)
        champ_seat = final.winner  # with the Zook that won the last round
        if exhibition:
            say(f"\n*** {champ.model} wins the exhibition with {champ_seat.zook_name}! ***", "m")
            return
        contest_label = ", ".join(dict.fromkeys(played))  # also in the highlights' file name: no slashes
        self.knockout_places, self.picks = {s.model: p for p, s in places}, picks
        say("\n  Final ranking", "b")
        for place, s in places:
            say(f"  {place:>2}. {s.model}")
        if picks[1]:
            say(f"\n  The audience's picks: {picks[0]} of {picks[1]} right", "m")
        say(f"\n*** {champ.model} WINS THE KNOCKOUT with {champ_seat.zook_name}! ***", "m")
        reel_path = None
        if len(reel) > 1:
            name = (f"{time.strftime('%Y-%m-%d_%H-%M-%S')} Knockout highlights - {self.challenge.name} - "
                    f"{contest_label}.mp4")
            if reel_path := showtime.highlights(reel, self.cfg.render_dir / "Tournament" / name,
                                                champion=(champ.model, champ_seat.zook_name), music=now["track"]):
                say(f"    highlights: {reel_path}  (/open plays it)", "dim")

        path = []
        for r, bouts in enumerate(rounds):
            for b in bouts:
                if b.b is None or champ.model not in (b.a.model, b.b.model):
                    continue
                mine, other = (b.a, b.b) if b.a.model == champ.model else (b.b, b.a)
                if not b.games:
                    path.append(f"went through at {played[r]} when {other.model} forfeited"
                                + (" (neither had a Zook; a coin toss)" if b.how == "both forfeit" else ""))
                    continue
                path.append(f"beat {other.billing()} at {played[r]} with {mine.zook_name} in {len(b.games)} game(s)"
                            + (" on a coin toss" if b.how == "coin toss" else ""))
        standings = "; ".join(f"{p}. {s.model}" for p, s in places)
        record = showtime.arena_record(showtime.hall_of_fame(self.arena_dir), champ.model) if self.show else None
        if await self.confirm(f"\nAdd {champ_seat.billing()} to the Hall of Fame? [y/N] "):
            showtime.record_champion(self.arena_dir, {
                "challenge": self.challenge.title, "contest": contest_label, "champion": champ.model,
                "zook": champ_seat.zook, "zook_name": champ_seat.zook_name, "field": len(seats),
                "runner_up": places[1][1].model if len(places) > 1 else None, "path": path})
            say(f"    added to the Hall of Fame ({self.arena_dir / showtime.HALL_OF_FAME})", "m")
        else:
            say("    not added to the Hall of Fame", "dim")
        if record:
            standings += f". Its record before tonight: {record}"
        if reel:  # /open plays the highlights (else the last game)
            self.entrants[champ.model].last_media = [str(reel_path or reel[-1].video)]
        await self.crown(champ_seat, contest_label, path, standings)

    def _knockout_options(self, e: Entrant, slot: int | None, used: set[str], exhibition: bool) -> list[str]:
        """The Zooks a model may play in a knockout round; none = it forfeits the match. Visible: the
        one it put forward for the round. Hidden: its team Zooks not played yet. An extra early round
        (more knockout rounds than challenges) or an exhibition: any of its Zooks."""
        if exhibition or slot is None:
            return e.zooks() or ([e.design] if e.design else [])
        if self.plan.team:
            return [z for z in e.zooks() if z not in used]
        x = next((x for x in e.entries if x.get("round") == slot), None)
        if not x or (slot > 1 and self.plan.qualifying_in(slot) and x.get("outcome") != "win"):
            return []  # nothing put forward for the round, or its qualifying game lost
        return [x["design"]]

    def _forfeit_reason(self, e: Entrant, slot: int | None) -> str:
        """Why a model can't play its match (for the announcer): '<model> {reason} and forfeits'."""
        if self.plan.team:
            return "has no team Zook left to play"
        x = next((x for x in e.entries if x.get("round") == slot), None)
        return "lost its qualifying game for this round" if x else "has no Zook for this round"

    def _seeding(self, entrants: list[Entrant]) -> list[Entrant]:
        """The knockout's entrants, best first: with qualifying, by round 1's qualifying score, then the
        fewest test runs used; otherwise in the order they qualified."""
        if not self.plan.qualifying:
            return list(entrants)

        def key(e: Entrant):
            score = sum(x.get("score", 0) for x in e.entries if x.get("round") == 1)
            runs = ChallengeState(self.challenge, self.cfg.zoo_dir / entrant_folder(e.model)).total_runs_used
            return -score, runs
        return sorted(entrants, key=key)

    async def design_round(self, seats: list[tournament.Seat], slot: int, round_name: str) -> None:
        """Short rules: the design phase before a knockout round. The models still in design their Zook
        for it in turn, each carrying on its own conversation, with the round's limits afresh (their
        MCP servers read the open round from the session file)."""
        self.plan.current = slot
        self.save_session()
        self._design_music(True)
        for s in seats:
            await self._safely(self.design(self.entrants[s.model], slot, round_name))
            await self._settle(4 if self.show else 0)
        self._showcase_stop()
        self._design_music(False)
        await self._disconnect()

    async def design(self, e: Entrant, slot: int, round_name: str) -> None:
        """One model's design phase for a later round (see design_round)."""
        self._showcase_stop()
        self.entrant = e
        e.submitted, e.presented = False, False
        m, k, n = self.plan.rounds[slot], slot + 1, len(self.plan.rounds)
        round_name = round_name[:-1] if round_name.endswith("finals") else round_name  # one match each
        say(f"\n=== {round_name} design phase: {e.model} ===", "b")
        say(f"    round {k} of {n}" + ("  |  contest secret" if self.plan.hidden else
                                       self._game_and_map(m.contest) + f"  |  opponent: {m.opponent}"), "dim")
        await self._connect(e.model)
        what = ("the contest is still SECRET: it's announced once every model's Zook for this round is in, so design "
                "a Zook that can handle different contests" if self.plan.hidden else
                f"the contest is {m.contest} and the Zook to beat is {m.opponent} (read its rules with get_contest)")
        qualifying = (f"When you submit it, it plays this round's qualifying game against {m.opponent}: you must WIN "
                      f"it to play your knockout match, or you forfeit. " if self.plan.qualifying_in(k) else "")
        kickoff = ROUND_KICKOFF.format(round=round_name, k=k, n=n, what=what, qualifying=qualifying)
        e.messages.append({"role": "user", "content": kickoff + self._presentation_limit()})
        self._log("runner", kickoff)
        await self.converse(nudges=3)

    async def choose_zook(self, me: tournament.Seat, opp: tournament.Seat, now: dict, round_name: str,
                          options: list[str], used: set[str] | None = None) -> str:
        """The Zook a model plays in this round's match, from its options (_knockout_options): the
        only one, or the one it picks (asked in its own conversation; the opponent's pick isn't shown
        to it). used: hidden challenges' team Zooks already played, which the choice joins."""
        e, info = self.entrants[me.model], now["info"]
        choice = options[0]
        if len(options) == 1 and not self.plan.team:
            say(f"  {me.model} plays {choice.split('/', 1)[-1]}, its Zook for {info.name}", "dim")
            return choice
        if len(options) > 1:
            from .runner.contests import RULES

            once = (" Each team Zook can be played only once" + (
                f" (already played: {', '.join(z.split('/', 1)[-1] for z in sorted(used))})" if used else "") + "."
                    if used is not None else "")
            reply = await self._ask(e, CHOOSE.format(
                event=now["event"], round=round_name, contest=info.name, rules=RULES.get(info.contest_file, ""),
                opp=f"{opp.model}", options=", ".join(z.split("/", 1)[-1] for z in options), once=once))
            choice = _match_zook(reply, options) or choice
        if used is not None:
            used.add(choice)
        say(f"  {me.model} picks {choice.split('/', 1)[-1]} for {info.name}", "m")
        return choice

    def _selection_start(self, zoo, contest: str, a: tournament.Seat, b: tournament.Seat, title: str) -> None:
        """The Simulator's Zook selection screen for the next match (green a, red b), in the game window,
        until the match starts."""
        self._selection_stop(0)
        from .runner import Engine

        try:
            engine = Engine()
            zooks = [(zoo.resolve(s.zook).path, f"{s.zook_name} ({_short_name(s.model)})") for s in (a, b)]
        except Exception as err:  # the show goes on without it
            say(f"    [no selection screen: {err}]", "dim")
            return
        cancel = threading.Event()
        thread = threading.Thread(target=engine.selection, args=(contest, zooks, cancel), daemon=True,
                                  kwargs={"title": title, "caption": f"Up next: {a.zook_name} v {b.zook_name}",
                                          "position": self.position, "fullscreen": self.fullscreen})
        thread.start()
        self._selection = (thread, cancel, time.time())

    def _selection_stop(self, shown: float = SELECTION_SECONDS) -> None:
        """Close the selection screen, once it has been up for at least `shown` seconds."""
        if selection := getattr(self, "_selection", None):
            thread, cancel, started = selection
            time.sleep(max(0.0, shown - (time.time() - started)))
            cancel.set()
            thread.join(timeout=15)
            self._selection = None

    async def crown(self, champ: tournament.Seat, contest: str, path: list[str], standings: str) -> None:
        """The champion takes the floor: with --showtime the announcer crowns it and it gives a
        victory speech; either way it knows the result and the audience's next messages go to it."""
        if self.announcer:
            self._announce(await self.announcer.champion(champ.model, contest, champ.zook_name,
                                                         "; ".join(path), standings))
        self.entrant = self.entrants[champ.model]
        await self._connect(champ.model)
        prompt = (f"Your Zook {champ.zook_name} has just WON the {contest} knockout tournament between the "
                  f"qualified models' Zooks. Your path: {'; '.join(path)}. Final ranking: {standings}. ")
        # --showtime: a victory speech now. Without it the champion just knows the result (no call),
        # ready for the audience's questions.
        prompt += ("Give the audience a short victory speech (under 150 words). After that the audience may ask "
                   "you questions." if self.show else "The audience may now ask you questions.")
        self.entrant.messages.append({"role": "user", "content": prompt})
        self._log("runner", prompt)
        if self.show:
            await self.converse()
        if not getattr(self, "auto", False):
            say("\n(Questions for the champion: just type them. /next or /challenger carries on.)", "dim")

    async def preview_match(self, contest: str, round_name: str, a: tournament.Seat, b: tournament.Seat,
                            card_a: showtime.ZookCard, card_b: showtime.ZookCard, folder: Path) -> None:
        """The tale of the tape on screen, then the announcer builds the match up from both Zooks' pictures."""
        say("")
        for line in showtime.tale_of_the_tape(card_a, card_b, _short_name(a.model), _short_name(b.model)):
            say(f"    {line}", "dim")
        if not self.announcer:
            return
        versus = None
        if (pa := card_a.picture("three_quarter")) and (pb := card_b.picture("three_quarter")):
            versus = showtime.versus_card(pa[0], pb[0], folder / f"versus-{time.time_ns()}.png")
        side = [(s.model, self.entrants[s.model].facts.get("country", ""), s.zook_name) for s in (a, b)]
        try:
            self._announce(await self.announcer.match(contest, round_name, side[0], side[1],
                                                      showtime.tale_of_the_tape(card_a, card_b, a.model, b.model),
                                                      showtime.image_part(versus) if versus else None))
        finally:
            if versus:
                versus.unlink(missing_ok=True)

    async def trash_talk(self, contest: str, round_name: str, a: tournament.Seat, b: tournament.Seat,
                         cards: dict[str, showtime.ZookCard]) -> None:
        """--showtime: each model sees the other's Zook (pictures, or its stats if it can't see) and
        says one line to the audience; the second hears the first."""
        if not self.show:
            return
        heard = ""
        for me, opp in ((a, b), (b, a)):
            card = cards[opp.model]
            pics = [showtime.image_part(p) for p in card.picture()] if self.entrants[me.model].vision else []
            seen = f"Pictures of {opp.zook_name} are attached. " if any(pics) else ""
            line = await self.remark(me, PRE_MATCH.format(
                contest=contest, round=round_name, mine=me.zook_name, opp_model=opp.model,
                opp_zook=opp.zook_name, opp_card=card.summary(), seen=seen, heard=heard),
                one_line=True, images=pics)
            heard = f'{me.model} has just said: "{line}" ' if line else ""

    async def post_match(self, contest: str, round_name: str, bout: tournament.Bout, result: str,
                         last: tuple | None, folder: Path, apart: str = "") -> None:
        """--showtime: how the deciding game ended, as stills (temporary): the announcer calls it,
        then interviews the winner and the loser, who see the same stills."""
        if not self.show:
            return
        game = bout.games[-1] if bout.games else None
        stills = None
        if last and last[0]:
            stills = showtime.end_of_game_stills(last[0], last[1], folder / f"end-{time.time_ns()}.png", *last[2:])
        picture = showtime.image_part(stills) if stills else None
        try:
            if self.announcer and game:
                self._announce(await self.announcer.game_call(contest, round_name, game.green.billing(),
                                                              game.red.billing(), result, game.stats, picture,
                                                              apart))
            for seat, won in ((bout.winner, True), (bout.loser, False)):
                question = ""
                if self.announcer:
                    question = await self.announcer.question(round_name, result, seat.model, seat.zook_name, won)
                question = question or STOCK_QUESTIONS[0 if won else 1].format(name=_short_name(seat.model))
                self._announce(question, "ANNOUNCER" if self.announcer else "INTERVIEW")
                vision = self.entrants[seat.model].vision and picture
                seen = (f"Stills of the deciding game are attached ({showtime.END_LABELS}). {apart} " if vision else
                        f"The deciding game: {game.stats}. " if game and game.stats else "")
                await self.remark(seat, INTERVIEW.format(context=f"{contest}, {round_name}: {result}",
                                                         seen=seen, question=question),
                                  images=[picture] if vision else None)
        finally:
            if stills:
                stills.unlink(missing_ok=True)

    async def remark(self, seat: tournament.Seat, prompt: str, one_line: bool = False,
                     images: list[dict | None] | None = None) -> str:
        """A contestant's answer in its own conversation (no tools): printed and returned. The
        pre-match comment is held to one line. Pictures go only to models that can see them."""
        line = await self._ask(self.entrants[seat.model], prompt, images, one_line)
        say(f"\n  {seat.model} ({seat.zook_name}): ", "c", end="")
        say(line or "(no comment)")
        self._speak_as(seat.model, line)
        return line

    async def _ask(self, e: Entrant, prompt: str, images: list[dict | None] | None = None,
                   one_line: bool = False) -> str:
        """A contestant's answer in its own conversation (no tools), kept in its history; "" if the
        call fails. Pictures go only to models that can see them."""
        images = [i for i in images or [] if i] if e.vision else []
        content = [{"type": "text", "text": prompt}, *images] if images else prompt
        e.messages.append({"role": "user", "content": content})
        _prune_images(e.messages, keep=2)
        self._log("runner", prompt + (f" [{len(images)} picture(s)]" if images else ""), e)
        try:
            # tools stay declared (some providers require it once a history has tool calls) but unused
            data = await self.router.chat(e.model, e.messages, self.tools, single_tool=e.single_tool,
                                          tool_choice="none")
        except (RuntimeError, httpx.HTTPError) as err:
            e.messages.pop()
            say(f"\n  [{e.model} has nothing to say: {err}]", "r")
            return ""
        usage = data.get("usage") or {}
        e.cost += float(usage.get("cost") or 0)
        e.tokens += int(usage.get("total_tokens") or 0)
        msg = (data.get("choices") or [{}])[0].get("message") or {}
        text = (msg.get("content") or "").strip()
        line = _one_line(text) if one_line else text
        e.messages.append({"role": "assistant", "content": line or "(no comment)"})
        self._log("model", line, e)
        return line

    async def match_talk(self, seats: list[tournament.Seat], context: str, enter: str,
                         pick: dict | None = None) -> None:
        """Between games: '/<name> message' to one contestant, plain text to both, Enter to go on.
        Lines typed while a game played are taken first. With `pick` (before a match), '/pick <name>'
        predicts the winner: pick["seat"] is set. --auto: no talk, the show goes on once the voice is done."""
        if getattr(self, "auto", False):
            await self._settle(2)
            return
        if not self.keyboard:
            return
        pending = self.keyboard.drain()
        hint = " or ".join(f"/{_short_name(s.model)}" for s in seats)
        while True:
            if pending:
                line = pending.pop(0)
            else:
                picking = ", /pick <name> to call the winner" if pick is not None and "seat" not in pick else ""
                say(f"\n  [Enter] {enter}  ({hint} <message> to one, or type to both{picking}) ", "dim", end="")
                line = await asyncio.to_thread(self.keyboard.get)
            if line is None or not line.strip():
                return
            line = line.strip()
            targets, msg = seats, line
            if pick is not None and line.split()[0] == "/pick":
                seat = _find_seat(seats, line[5:])
                if seat:
                    pick["seat"] = seat
                    say(f"  Your pick: {seat.billing()}", "m")
                else:
                    say(f"  /pick <name>: one of {', '.join(_short_name(s.model) for s in seats)} "
                        "(or a Zook's name)", "y")
                continue
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

    async def confirm(self, question: str) -> bool:
        """A yes/no question at the keyboard; only 'y' or 'yes' is yes (Enter, anything else, or no
        keyboard is no). Lines typed earlier are cleared first, so they can't answer it. --auto: yes."""
        if getattr(self, "auto", False):
            say(question + "y (--auto)", "b")
            return True
        if not self.keyboard:
            return False
        self.keyboard.drain()
        say(question, "b", end="")
        answer = await asyncio.to_thread(self.keyboard.get)
        return (answer or "").strip().lower() in ("y", "yes")

    async def _settle(self, extra: float = 0.0) -> None:
        """--auto pacing: wait until the voice has said everything queued (so nothing is cut off or
        talked over), then `extra` seconds more."""
        if getattr(self, "voice", None):
            await asyncio.to_thread(self.voice.wait_idle)
        await asyncio.sleep(extra)

    def rounds_check(self) -> tuple[bool, list[str]]:
        """Does the queue fit the rounds? (mismatch, lines for the operator): a knockout of n models
        has rounds_needed(n) rounds, so with fewer models rounds are skipped (full rules: the first
        ones; short rules: those after round 1) and with more, random rounds are added (before round
        1; short rules: after it). A field that isn't a power of two also gets first-round byes."""
        s, r = self.plan, len(self.plan.rounds)
        n = len(self.queue) if not self.cap else min(len(self.queue), self.cap)
        if n < 2:  # models called by hand (/challenger) or none yet
            return False, [f"1 model queued: the knockout needs at least two."] if n else []
        k = tournament.rounds_needed(n)
        lines = []
        if k != r:
            lines.append(f"{n} models queued{' (if they all qualify)' if s.qualifying else ''} for {r} round(s), but "
                         f"a knockout of {n} has {k} round(s):")
        if k < r:
            gone = list(enumerate(s.rounds, 1))
            gone = gone[1:] if s.round_by_round and k == 1 else gone[1:1 + r - k] if s.round_by_round else gone[:r - k]
            lines.append(f"{r - k} round(s) will be skipped: "
                         + ", ".join(f"round {i}" + ("" if s.hidden else f" ({m.contest})") for i, m in gone)
                         + (" (only round 1 is played)" if s.round_by_round and k == 1 else ""))
        elif k > r:
            lines.append(f"{k - r} extra random round(s) will be added "
                         + ("after round 1" if s.round_by_round else "before round 1") + ".")
        mismatch = bool(lines)
        if n != 2 ** k:
            lines.append(f"{2 ** k - n} model(s) will get a bye in the first round "
                         + ("(the best qualifiers)." if s.qualifying else "(at random)."))
        return mismatch, lines

    async def check_rounds(self) -> bool:
        """At the start: tell the operator if the queue doesn't match the rounds, and ask to carry on."""
        mismatch, lines = self.rounds_check()
        if not lines:
            return True
        say("\nThe queue doesn't match the rounds!" if mismatch else "", "y" if mismatch else "")
        for line in lines:
            say(f"  {line}", "y" if mismatch else "dim")
        return not mismatch or await self.confirm("Carry on with these rounds? [y/N] ")

    async def offer_contest(self) -> None:
        """When the queue has run out, or the knockout is full (max_qualifiers), ask whether to play
        the knockout now."""
        full = self._field_full()
        if (self.queue and not full) or not self.keyboard or self.ending:
            return
        qualified = [e for e in self.entrants.values() if e.qualified and e.design]
        if len(qualified) < 2:
            say(f"\nThe queue is empty. {len(qualified)} model(s) qualified: /contest needs two "
                "(add more with /challenger <model>).", "dim")
            return
        why = (f"The knockout is full ({self.cap} qualified, max_qualifiers)" if full else
               f"The queue is empty and {len(qualified)} models have qualified")
        if await self.confirm(f"\n{why}. Run the knockout contest now? [Y/N] "):
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
        if not await self.check_rounds():
            say("Change the queue ([arena] challengers) or the rounds ([arena] contest / opponent) and start again.",
                "y")
            return
        if self.auto:  # the whole show, unattended: every challenger, the knockout, the champion, the table
            while self.queue and not self._field_full():
                await self._safely(self.start(self.queue.pop(0)))
                await self._settle(10 if self.show and not self.voice else 4)  # the presentation plays out
            if self.queue:
                say(f"\nThe knockout is full ({self.cap} qualified, max_qualifiers): not called: "
                    f"{', '.join(self.queue)}.", "y")
            self._showcase_stop()
            self.leaderboard()
            await self._safely(self.offer_contest())
            await self._settle(3)
            await self._disconnect()
            await self.sign_off()
            await self._settle()
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
                if self._field_full():
                    say(f"The knockout is full ({self.cap} qualified, max_qualifiers): /contest starts it.", "y")
                    continue
                await self.call(self.queue.pop(0))
            elif cmd == "/challenger":
                if not arg:
                    say("usage: /challenger <openrouter model id>, e.g. /challenger openai/gpt-5", "y")
                    continue
                if self._field_full():
                    say(f"The knockout is full ({self.cap} qualified, max_qualifiers): /contest starts it.", "y")
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
            elif cmd == "/exhibition":
                await self.exhibition(arg)
            elif cmd in ("/halloffame", "/hall"):
                self.hall()
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
        self._showcase_stop()
        await self._disconnect()
        if self.ending:
            await self.sign_off()
            self.summary()
            if self.keyboard:
                self.keyboard.drain()
                say("\nPress Enter to quit. ", "b", end="")
                await asyncio.to_thread(self.keyboard.get)
        say("bye", "dim")

    async def sign_off(self) -> None:
        """The announcer closes the show with how the evening went."""
        if not self.announcer or not self.entrants:
            return
        self._announce(await self.announcer.closing(self.story()))

    def story(self) -> str:
        """The session in a few sentences (for the announcer): each model's result and the knockout."""
        parts = []
        for e in self.entrants.values():
            res = "; ".join(_entry_line(x) for x in e.entries) or "didn't submit"
            if e.model in self.knockout_places:
                res += f", {_place(self.knockout_places, e.model)} in the knockout"
            parts.append(f"{e.model}: {res}.")
        champ = [m for m, p in self.knockout_places.items() if p == 1]
        if champ:
            parts.append(f"Champion: {champ[0]}.")
        elif not self.knockout_places:
            parts.append("No knockout was played.")
        picks = getattr(self, "picks", [0, 0])
        if picks[1]:
            parts.append(f"The audience predicted {picks[0]} of {picks[1]} knockout matches right.")
        return " ".join(parts)

    def summary(self) -> Path | None:
        """The session in one table: every model called, its Zook, result, runs, knockout place,
        tokens and cost, with totals. Printed and saved to <arena>/Sessions/<date_time>.txt."""
        if not self.entrants:
            say("\nNo models were called this session.", "dim")
            return None
        rows = []
        for e in self.entrants.values():
            runs = ChallengeState(self.challenge, self.cfg.zoo_dir / entrant_folder(e.model)).total_runs_used
            score = sum(x.get("score", 0) for x in e.entries)
            rows.append([e.model, "; ".join(_entry_line(x) for x in e.entries) or "not submitted",
                         f"{score:g}" if self.plan.qualifying and e.entries else "-",
                         "yes" if e.qualified else "no", str(runs),
                         _place(self.knockout_places, e.model), f"{e.tokens:,}", f"${e.cost:.4f}"])
        tokens, cost = sum(e.tokens for e in self.entrants.values()), sum(e.cost for e in self.entrants.values())
        if host := getattr(self, "announcer", None):
            rows.append([f"(announcer) {host.model}", "-", "-", "-", "-", "-", f"{host.tokens:,}",
                         f"${host.cost:.4f}"])
            tokens, cost = tokens + host.tokens, cost + host.cost
        total = ["TOTAL", "", "", f"{sum(e.qualified for e in self.entrants.values())} qualified", "", "",
                 f"{tokens:,}", f"${cost:.4f}"]
        lines = _table(["Model", "Zooks put forward", "Score", "Qualified", "Test runs", "Knockout",
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
            if self._field_full():
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


def _match_zook(reply: str, options: list[str]) -> str | None:
    """The option a model named in its reply ("Crusher@3" before "Crusher"; the longest name wins)."""
    text = reply.lower()
    for key in (lambda z: z.split("/", 1)[-1], lambda z: z.split("/", 1)[-1].split("@", 1)[0]):
        named = [z for z in options if key(z).lower() in text]
        if named:
            return max(named, key=lambda z: len(key(z)))
    return None


def _entry_line(d: dict) -> str:
    """One Zook put forward: 'round 1 (Zook Sumo): Crusher@3, WIN', or 'team: Crusher@3'."""
    zook = str(d.get("design", "?")).split("/", 1)[-1]
    if d.get("round") is None:
        return f"team: {zook}"
    out = f"round {d['round']} ({(d.get('game') or {}).get('contest', '?')}): {zook}"
    if "outcome" in d:
        how = " at the time limit" if d["outcome"] == "draw" and d.get("status") == "timeout" else ""
        out += f", {str(d['outcome']).upper()}{how}"
    return out


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


def _spaced(name: str) -> str:
    """'TableSumo' -> 'Table Sumo' (the game's agent class names)."""
    return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", name)


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
    ap.add_argument("--challenge", help="the rules: short, full or a TOML path (default: [arena] challenge_rules); "
                                        "the rounds come from [arena] contest and opponent")
    ap.add_argument("--max-turns", type=int, default=int(a_cfg.get("max_turns", 120)))
    ap.add_argument("--max-tokens", type=int, default=int(a_cfg.get("max_tokens", 4000)))
    ap.add_argument("--temperature", type=float, default=a_cfg.get("temperature"))
    ap.add_argument("--auto", action="store_true",
                    help="the whole show unattended: every queued challenger in turn, then the knockout (when two "
                         "qualify), the champion and the session table; no /next or Enter needed")
    ap.add_argument("--keep-entries", action="store_true",
                    help="carry on with the previous Zook Entries (default: move them to Archive/<date_time>/ "
                         "so every model starts afresh)")
    ap.add_argument("--verbose", "-v", action="store_true",
                    help="stream the model's reasoning and notes as it writes them, and show them and "
                         "its tool arguments in full (not cut to 100 words)")
    ap.add_argument("--showtime", action="store_true", default=bool(a_cfg.get("showtime", False)),
                    help="the show, at the cost of extra model calls: an announcer introduces the challengers and "
                         "calls the games; in the knockout the models see each other's Zooks, trade pre-match "
                         "lines and are interviewed with stills of how each game ended; Hall of Fame records are "
                         "mentioned ([arena] showtime). Talking to the models works either way")
    ap.add_argument("--announcer", default=a_cfg.get("announcer", DEFAULT_ANNOUNCER),
                    help=f"with --showtime: the OpenRouter model that runs the show (default {DEFAULT_ANNOUNCER}; "
                         f"[arena] announcer)")
    ap.add_argument("--no-announcer", action="store_true", help="with --showtime: the show without an announcer")
    ap.add_argument("--announcer-style", default=a_cfg.get("announcer_style", "ringside"),
                    help="with --showtime: ringside (default), documentary, football, noir, or your own description "
                         "of a style")
    ap.add_argument("--speak", action="store_true", default=bool(a_cfg.get("speak", False)),
                    help="with --showtime: the announcer speaks out loud (Kokoro if installed, else Windows speech, "
                         "macOS say, Linux espeak; [arena] speak)")
    ap.add_argument("--voice", default=a_cfg.get("voice"),
                    help='speech voice: a Kokoro voice (default bm_george, British male; bm_lewis, bf_emma, ...) or '
                         'a system one, e.g. "Microsoft Hazel Desktop" ([arena] voice)')
    ap.add_argument("--commentator", nargs="?", const=COMMENTATOR, default=a_cfg.get("commentator"),
                    help=f"with --showtime --speak: live commentary on the knockout games by this vision model "
                         f"(off unless given; --commentator alone uses {COMMENTATOR}; [arena] commentator)")
    ap.add_argument("--nomusic", "--no-music", action="store_true", default=not a_cfg.get("music", True),
                    help="no music: not under the games (live or in their videos and the highlights) and not "
                         "through the design phases; the countdown beeps stay ([arena] music = false)")
    screen = ap.add_mutually_exclusive_group()
    screen.add_argument("--monitor", action="store_true", default=bool(a_cfg.get("monitor", False)),
                        help="single-monitor mode: test and tournament games put the engine window on the right half "
                             "of the main screen, with a 4:3 picture (at most 1080p's half) in it, leaving the left "
                             "for this terminal ([arena] monitor)")
    screen.add_argument("--full-screen", action="store_true", default=bool(a_cfg.get("full_screen", False)),
                        help="test and tournament games run in windowed full screen: a borderless window over the "
                             "whole screen, with a 4:3 picture (at most 1024x768) centred in it ([arena] "
                             "full_screen). Default: the engine's own window, where it opens")
    a = ap.parse_args(argv)
    if a.full_screen:
        a.monitor = False  # e.g. [arena] monitor = true in the config, --full-screen on the command line
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    # The rounds are planned once (random picks included) and shared by every model; --keep-entries
    # carries on with the saved plan.
    saved = cfg.zoo_dir / "session.json"
    session = Session.load(saved) if a.keep_entries and saved.exists() else plan_from_config(cfg, a.challenge)
    queue = a.challenger or list(a_cfg.get("challengers", []))
    arena = Arena(session, queue, a.max_turns, a.max_tokens, a.temperature, a.auto, a.verbose, show=a.showtime,
                  announcer=None if a.no_announcer or str(a.announcer).lower() in ("", "none", "off") else a.announcer,
                  announcer_style=a.announcer_style, speak=a.speak, voice=a.voice, monitor=a.monitor,
                  fullscreen=a.full_screen,
                  commentator=None if str(a.commentator).lower() in ("", "none", "off") else a.commentator,
                  play_music=not a.nomusic)
    if not a.keep_entries and (moved := arena.archive_entries()):
        say(f"Previous Zook Entries archived to {moved}: every model starts afresh (--keep-entries to carry on).",
            "dim")
    arena.save_session()
    say(f"Rules: {session.rules.name}{' (challenges hidden)' if session.hidden else ''}"
        f"{arena._rounds_line() if not session.hidden else ''}", "dim")
    try:
        asyncio.run(arena.repl())
    finally:
        if arena.design_music:
            arena.design_music.close()
