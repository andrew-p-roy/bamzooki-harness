"""MCP server: lets a model design BAMZOOKi Zooks and test them in the real game (M4).

    python -m bamzooki.mcp_server --model claude-opus-5-5        # stdio (the client starts it)
    python -m bamzooki.mcp_server --model my-agent --http        # local endpoint http://127.0.0.1:8765/mcp

The model name decides where its designs (zooks/<model>/) and videos (renders/<model>/) go.
Every engine-backed tool (save/edit/preview Zooks, run contests) launches the game on screen
and takes from a few seconds (save, preview) to about a minute (a contest).
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from mcp.server.mcpserver import Image, MCPServer

from ..studio import DEFAULT_VIEWS, Studio

INSTRUCTIONS = """\
You design creatures ("Zooks") for BAMZOOKi and test them in the real 2006 game engine, which
runs on the user's screen. Typical loop:
  1. get_design_guide (once) and list_contests / get_contest for the rules.
  2. get_zook an example ("Ant", "Spider", ...) to see a working design.
  3. save_zook your design (JSON spec). The game's own Zook Kit code lays it out, clamps
     out-of-range values, computes its size/weight, and you get preview images back.
  4. run_trial it (sprint, hurdles, lap, block_push, high_jump: the Zook Kit's own tests, ~10-30 s)
     to check it walks, then run_contest it against an example or another design; read the
     verdict, scores, distance travelled, falls, and look at the stills. Iterate with edit_zook.
Refs: "Ant" (example), "MyZook" (your latest version), "MyZook@2", "<model>/<Name>".
Contests are deterministic for a given seed. Only one engine job runs at a time.
"""


def _dump(obj) -> str:
    return json.dumps(obj, indent=1, default=str)


def _with_images(summary: dict, previews: dict) -> list:
    out: list = [_dump(summary)]
    for view, path in (previews or {}).items():
        if path and Path(path).exists():
            out.append(f"preview: {view}")
            out.append(Image(path=path))
    return out


def build_server(studio: Studio) -> MCPServer:
    server = MCPServer(name="bamzooki", title="BAMZOOKi Zook lab", instructions=INSTRUCTIONS)

    @server.tool(description="How Zooks are built: the part schema with every slider's range and "
                             "meaning, available textures, example Zooks, and design tips. Read this first.")
    def get_design_guide() -> str:
        return _dump(studio.design_guide())

    @server.tool(description="All contests in the user's game, with players, arena colours and rules.")
    def list_contests() -> str:
        return _dump(studio.contests())

    @server.tool(description="Rules and details of one contest (pack name like 'Zook Sumo' or file like CP_ZOOK_SUMO).")
    def get_contest(contest: str) -> str:
        return _dump(studio.contest(contest))

    @server.tool(description="Example Zooks plus your saved designs (name -> versions) and other models' designs.")
    def list_zooks() -> str:
        return _dump(studio.zooks())

    @server.tool(description="A Zook's design as a JSON spec (the same format save_zook takes), its passport "
                             "stats and notes. include_defaults=true lists every slider, not just non-default ones.")
    def get_zook(ref: str, include_defaults: bool = False) -> str:
        return _dump(studio.get_zook(ref, include_defaults))

    @server.tool(description="Check a spec without saving it. Returns problems (empty when valid).")
    def validate_zook(spec: dict) -> str:
        return _dump(studio.validate(spec))

    @server.tool(description="Save a design as a new version (spec: {name, body}). It is validated, laid out by "
                             "the game's Zook Kit code, and photographed; returns stats and preview images. "
                             "Takes a few seconds; the game window appears briefly.")
    def save_zook(spec: dict, notes: str = "", show_previews: bool = True) -> list:
        r = studio.save_zook(spec, notes=notes)
        return _with_images(r, r.get("previews") if show_previews else None)

    @server.tool(description="Change a saved or example Zook with edit operations and save the result as a new "
                             "version: [{op: 'set'|'add'|'remove', path: 'body.children[0].size.width', value}]. "
                             "new_name saves it under another name (required when editing an example).")
    def edit_zook(ref: str, edits: list[dict], notes: str = "", new_name: str | None = None,
                  show_previews: bool = True) -> list:
        r = studio.edit_zook(ref, edits, notes=notes, new_name=new_name)
        return _with_images(r, r.get("previews") if show_previews else None)

    @server.tool(description="Pictures of a Zook. views from: three_quarter, front, back, left, right, top.")
    def preview_zook(ref: str, views: list[str] | None = None) -> list:
        r = studio.preview(ref, tuple(views or DEFAULT_VIEWS))
        return _with_images({k: v for k, v in r.items() if k != "previews"}, r.get("previews"))

    @server.tool(description="Run a contest in the real game (on screen, ~20-90 s) and get the verdict: winner by "
                             "colour and Zook, scores, finish time, distance travelled, falls, the MP4 path, and "
                             "(with video) a 2x2 sheet of stills: Go, halfway, the finish, the end. "
                             "green/red are Zook refs; single-player contests take only green.")
    def run_contest(contest: str, green: str, red: str | None = None, seed: int = 1, video: bool = True,
                    duration: float = 90.0) -> list:
        r = studio.run(contest, green, red, seed=seed, video=video, duration=duration)
        return _with_images(r, {"stills (Go | halfway / finish | end)": r.get("stills")})

    @server.tool(description="Test one Zook on its own with the Zook Kit's built-in trials (the Test tool): "
                             "sprint (cm/s over 50 units, 20 s), hurdles (cm/s, 20 s), lap (s per lap, 60 s), "
                             "block_push (cm pushed, 13 s), high_jump (cm, 10 s); or free play in a test "
                             "environment for `duration` seconds: 'free:BoxEnv', 'free:SlopeEnv', 'free:StepEnv', ... "
                             "Returns the trial result as the Zook Kit reports it, speed/drift/height stats and "
                             "a sheet of stills. Faster than a contest (~10-30 s).")
    def run_trial(ref: str, trial: str = "sprint", duration: float = 20.0, video: bool = True) -> list:
        r = studio.trial(ref, trial, duration=duration, video=video)
        return _with_images(r, {"stills (start | 1/3 / 2/3 | end)": r.get("stills")})

    @server.tool(description="Your most recent contest results (newest last).")
    def list_results(limit: int = 20) -> str:
        return _dump(studio.results(limit))

    return server


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", default=os.environ.get("BAMZOOKI_MODEL", "manual"),
                    help="name of the model using the server (folder for its designs and videos)")
    ap.add_argument("--http", action="store_true", help="serve MCP over HTTP (streamable-http) instead of stdio")
    ap.add_argument("--host", default="127.0.0.1", help="HTTP bind address (keep it local)")
    ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args(argv)
    server = build_server(Studio(model=a.model))
    if a.http:
        server.run("streamable-http", host=a.host, port=a.port)
    else:
        server.run("stdio")
