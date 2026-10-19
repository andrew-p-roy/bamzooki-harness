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
    /list                 the queue                         /quit                leave
    anything else         a message to the current model (it may use its tools to answer)

The API key comes from OPENROUTER_API_KEY (environment or the repo's .env file).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from .challenge import Challenge, ChallengeState
from .config import REPO_ROOT, load_config
from .manual import MODEL_INSTRUCTIONS
from .sdk.store import entrant_folder

API = "https://openrouter.ai/api/v1"
PROMPT_PATH = MODEL_INSTRUCTIONS / "Agent Prompt.md"
KICKOFF = ("Begin the challenge now. Start with get_challenge. Work only through the bamzooki tools, and "
           "finish with submit_design followed by your presentation to the audience.")
NUDGE = ("You haven't submitted yet. Keep going with the bamzooki tools; when you're ready (or out of "
         "runs), call submit_design with your best design, then present your challenger.")

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

    async def chat(self, model: str, messages: list[dict], tools: list[dict]) -> dict:
        body = {"model": model, "messages": messages, "tools": tools, "tool_choice": "auto",
                "max_tokens": self.max_tokens, "usage": {"include": True}}
        if self.temperature is not None:
            body["temperature"] = self.temperature
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


class Arena:
    def __init__(self, challenge: Challenge, queue: list[str], max_turns: int = 120, max_tokens: int = 4000,
                 temperature: float | None = None, auto: bool = False):
        self.cfg = load_config()
        self.challenge = challenge
        self.queue = list(queue)
        self.max_turns = max_turns
        self.auto = auto
        self.router = OpenRouter(load_api_key(), max_tokens, temperature)
        self.arena_dir = self.cfg.arena_dir
        self.arena_dir.mkdir(parents=True, exist_ok=True)
        self.entrant: Entrant | None = None
        self.session = None
        self.tools: list[dict] = []
        self._stack = None
        self._transcript: Path | None = None

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
        r, w = await self._stack.enter_async_context(stdio_client(params))
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
    def _log(self, kind: str, text: str) -> None:
        if not self._transcript:
            return
        with self._transcript.open("a", encoding="utf-8") as f:
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
        folder = entrant_folder(model)
        tdir = self.cfg.transcripts_dir / folder
        tdir.mkdir(parents=True, exist_ok=True)
        self._transcript = tdir / f"{time.strftime('%Y-%m-%d_%H-%M-%S')}.md"
        self._transcript.write_text(f"# {model} - {self.challenge.title}\n", encoding="utf-8")
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
        """Let the model work until it answers without calling tools."""
        e = self.entrant
        for _ in range(self.max_turns):
            _prune_images(e.messages, keep=2)
            try:
                data = await self.router.chat(e.model, e.messages, self.tools)
            except RuntimeError as err:
                say(f"[api error] {err}", "r")
                return
            usage = data.get("usage") or {}
            e.cost += float(usage.get("cost") or 0)
            e.tokens += int(usage.get("total_tokens") or 0)
            msg = (data.get("choices") or [{}])[0].get("message") or {}
            text = (msg.get("content") or "").strip()
            calls = msg.get("tool_calls") or []
            e.messages.append({k: v for k, v in {"role": "assistant", "content": msg.get("content") or "",
                                                 "tool_calls": calls or None}.items() if v is not None})
            if text:
                say(f"\n{e.model}: ", "c", end="")
                say(text)
                self._log("model", text)
            if not calls:
                if not e.submitted and nudges > 0:
                    nudges -= 1
                    e.messages.append({"role": "user", "content": NUDGE})
                    self._log("runner", NUDGE)
                    continue
                say(f"    [{e.tokens:,} tokens, ${e.cost:.4f} so far]", "dim")
                return
            images = []
            for call in calls:
                images += await self._run_tool(call)
            if images and e.vision:
                e.messages.append({"role": "user", "content": [
                    {"type": "text", "text": "Images returned by the tool call(s) above:"}, *images]})
        say(f"[stopped after {self.max_turns} turns]", "y")

    async def _run_tool(self, call: dict) -> list[dict]:
        e = self.entrant
        fn = call.get("function") or {}
        name = fn.get("name", "")
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except json.JSONDecodeError as err:
            args, bad = None, f"Invalid JSON arguments: {err}"
        say(f"  -> {name}({_short(args)})", "dim")
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
            say(f"     {d.get('contest')}: {d.get('green')} vs {d.get('red')} -> winner "
                f"{d.get('winner_zook') or 'none'}", "g")
        elif name == "submit_design" and "outcome" in d:
            e.submitted = True
            say(f"     SUBMITTED {d['design']}: {d['outcome'].upper()} (score {d['score']})", "m")
        if isinstance(d.get("budget"), dict) and d["budget"].get("reminder"):
            say(f"     {d['budget']['reminder']}", "y")

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
            say(f"  {name:45s} {res}  ({used} runs)")

    def open_media(self) -> None:
        if not self.entrant or not self.entrant.last_media:
            say("Nothing to open yet.", "dim")
            return
        for p in self.entrant.last_media:
            if Path(p).exists() and hasattr(os, "startfile"):
                os.startfile(p)  # noqa: S606 (local media files we produced)
                say(f"opened {p}", "dim")

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
        if self.auto:
            while self.queue:
                await self.start(self.queue.pop(0))
            self.leaderboard()
            await self._disconnect()
            return
        while True:
            try:
                line = (await asyncio.to_thread(input, "\nyou> ")).strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not line:
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
                await self.start(self.queue.pop(0))
            elif cmd == "/challenger":
                if not arg:
                    say("usage: /challenger <openrouter model id>, e.g. /challenger openai/gpt-5", "y")
                    continue
                await self.start(arg.strip())
            elif cmd == "/list":
                say("queue: " + (", ".join(self.queue) or "(empty)"))
            elif cmd == "/leaderboard":
                self.leaderboard()
            elif cmd == "/open":
                self.open_media()
            elif cmd == "/status":
                await self.status()
            elif cmd.startswith("/"):
                say("unknown command; /help lists them", "y")
            else:
                if not self.entrant:
                    say("No challenger yet: /next or /challenger <model>.", "y")
                    continue
                self.entrant.messages.append({"role": "user", "content": line})
                self._log("audience", line)
                await self.converse()
        await self._disconnect()
        say("bye", "dim")


def _prune_images(messages: list[dict], keep: int = 2) -> None:
    """Only the latest `keep` image messages keep their pictures (each request resends history)."""
    seen = 0
    for m in reversed(messages):
        if m.get("role") == "user" and isinstance(m.get("content"), list):
            if any(p.get("type") == "image_url" for p in m["content"]):
                seen += 1
                if seen > keep:
                    m["content"] = [{"type": "text", "text": "[earlier images omitted to save space]"}]


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
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    challenge_path = a.challenge or a_cfg.get("challenge") or cfg.challenge
    if challenge_path and not Path(challenge_path).is_absolute():
        challenge_path = REPO_ROOT / challenge_path
    challenge = Challenge.load(challenge_path)
    queue = a.challenger or list(a_cfg.get("challengers", []))
    arena = Arena(challenge, queue, a.max_turns, a.max_tokens, a.temperature, a.auto)
    asyncio.run(arena.repl())
