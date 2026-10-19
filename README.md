# BAMZOOKi AI harness

Lets AI models design BAMZOOKi creatures (Zooks) through an API and test them in the
**real** Bonsai engine, with results returned as data. See
[Documentation/BAMZOOKI_HANDOFF.md](Documentation/BAMZOOKI_HANDOFF.md) for the file formats,
engine findings and roadmap.

Status: M0–M4 working: codecs and key derivation, Zook SDK, on-screen contest runner with
verdicts, video and replays, and an MCP server for models.

## Bring your own copy: no BBC files or keys in this repo

This repository contains **no** game binaries, scripts, Zooks, meshes, textures, encryption
keys or passwords, and it must never contain any. You need your own installed copy of
BAMZOOKi (Zook Kit v3.1 / Bonsai Engine build 158). At runtime the tools read your install
and derive whatever they need from it: the script key comes from `he.skx`, and the Zook
password from your install's own config script.

Everything extracted or decrypted goes to a gitignored cache (`.cache/bamzooki/` by
default). An asset guard enforces this in pre-commit and CI. It fails if the repo would
contain `.ssx`/`.sax`/`.skx`/`.zook`/`.dff` (and similar) files, any Bonsai archive, or the
password or key derived from your install. Patches to game scripts must be
shipped as diffs or generated code that is applied locally, never as decrypted files.

## Setup

Requires Python 3.11+. Windows runs the engine natively. Linux/macOS will need Wine (not
yet verified).

```sh
python -m venv .venv
# Windows: .venv\Scripts\activate    Linux/macOS: source .venv/bin/activate
pip install -e ".[dev]"
pre-commit install                        # enables the asset guard on every commit
cp bamzooki.example.toml bamzooki.toml    # then edit paths (optional on a default Windows install)
```

`bamzooki.toml` is gitignored. Each setting can also be given by an environment variable:

| key            | env var              | meaning                                                         |
|----------------|----------------------|-----------------------------------------------------------------|
| `install_dir`  | `BAMZOOKI_INSTALL`   | folder containing `Bonsai.exe` and `he.skx`                      |
| `save_dir`     | `BAMZOOKI_SAVE_DIR`  | per-user save root (Windows: `%APPDATA%\Bamzooki`)              |
| `wine_command` | `BAMZOOKI_WINE`      | launcher prefix on non-Windows, e.g. `["wine"]`                 |
| `cache_dir`    | `BAMZOOKI_CACHE_DIR` | extraction cache and install sandbox (default `.cache/bamzooki`) |

Set `BAMZOOKI_CONFIG` to use a config file somewhere else. If `install_dir` isn't set, the
standard Program Files locations (or the default Wine prefix) are searched.

## Usage

```sh
python tools/extract.py      # decrypt all scripts and decode example Zooks into the cache
python -m pytest             # install-backed tests skip automatically if no install is found
python -m pytest -m engine   # opt-in tests that launch the game on screen
python tools/check_assets.py # run the asset guard by hand

python tools/run_contest.py --list                                   # contests in your install
python tools/run_contest.py "Zook Sumo" --green Ant --red Spider     # watch a contest, get a verdict
python tools/run_contest.py "Zook Sumo" --green Ant --red Spider --record --video   # + .bvz + MP4

python tools/replay.py play   .cache/bamzooki/runs/<run>     # watch a .bvz in the game's Motion Player
python tools/replay.py render .cache/bamzooki/runs/<run> --view overview   # re-render a replay to MP4
```

**Renders** go to `renders/<model>/<YYYY-MM-DD_HH-MM-SS>_<contest>_<green>-vs-<red>_seed<N>.mp4`,
with a `.json` beside each one (verdict, Zooks, seed, run folder). `<model>` is the model operating
the run: pass `--model claude-opus-5-5` (or `model=` in Python, or set `BAMZOOKI_MODEL`); the
default is `manual`. Replay re-renders are named `<timestamp>_<run>_replay-<view>.mp4`. The
`renders/` folder is gitignored (renders contain game visuals) and configurable as
`render_dir` in `bamzooki.toml`.

Views for `--video` / `--camera single` / `replay.py render`: `follow` (both Zooks, default),
`overview` (whole arena), `green`, `red` (chase cams). Without `--video` the contest shows the
game's own 5-view layout. Capturing frames slows the engine in proportion to the window's
pixel count (the default ~884×662 window runs at about 0.85× real time with capture), so keep
the window small when recording; `--window WxH` and `--position right|left|center` exist
but are off by default. MP4 export needs `ffmpeg` on PATH.

```python
from bamzooki.runner import run_contest
r = run_contest("Zook Sumo", green="a.zook", red="b.zook", seed=1, record=True, video=True)
print(r.winner, r.scores, r.finish_time, r.replay, r.video)
```

The game runs **on screen**: its main loop only advances while its window has focus. The
runner focuses the window when it launches, and takes focus back once you've been idle for
3 seconds (`focus="idle"`; `"launch"` and `"always"` are also available). Results are
deterministic for a given `seed`. Each run's files (job, logs, result table, replay, frames,
MP4) go to `.cache/bamzooki/runs/<timestamp>-<contest>/`. The engine runs from a copy of
your install under `.cache/bamzooki/sandbox/`; your install is never modified.

## Letting a model design Zooks (MCP)

The MCP server gives any MCP client (Claude Code, Claude Desktop, ...) tools to read the
design guide and contest rules, inspect example Zooks, **save designs** (validated, laid out by
the game's own Zook Kit code, returned with **preview images**), edit them, and **run
contests** in the real game with a verdict and an MP4.

```sh
# Claude Code (from this repo; the --model name picks the folder for designs and videos)
claude mcp add bamzooki -- <repo>/.venv/Scripts/python.exe -m bamzooki.mcp_server --model claude-opus-5-5
```

For Claude Desktop, add the same command to `claude_desktop_config.json` under `mcpServers`.
With stdio the client starts the server itself, so there is nothing else to run. For other agents
or frameworks there is a **local HTTP endpoint** instead:

```sh
python -m bamzooki.mcp_server --model my-agent --http          # http://127.0.0.1:8765/mcp
```

Tools: `get_design_guide`, `list_contests`, `get_contest`, `list_zooks`, `get_zook`,
`validate_zook`, `save_zook`, `edit_zook`, `preview_zook`, `run_trial`, `run_contest`,
`list_results`. Results come straight back in the tool response (the verdict, scores, distance
travelled, falls, plus a 2×2 sheet of stills for vision models) and are logged to
`zooks/<model>/results.jsonl`. The game window appears whenever a tool needs the engine
(save/preview: a few seconds; trial: 10–30 s; contest: up to about a minute and a half). If your
client times out long tool calls, raise its limit (Claude Code: `MCP_TOOL_TIMEOUT`, in ms).

**Challenges.** The server gives the model a task from a challenge file
(`challenges/default.toml`; choose another with `challenge = "..."` in `bamzooki.toml`,
`BAMZOOKI_CHALLENGE` or `--challenge`, or run `--no-challenge` for free play). A challenge sets
the task text, the **test-run budget** (default 20; failed engine runs are free) and the **one
evaluation game** that `submit_design` plays (contest, opponent, seed, side; outside the budget;
win 1, draw 0.5, loss 0). **Every test run is a single game that the model chooses** through the
tool arguments: a trial for one of its Zooks, or one contest with the two Zooks and the seed it
picks. Which games to spend runs on is its testing strategy. Every run result reminds the model
how many runs are left. In its tests a model can only use the example Zooks
and its own designs; other models' designs are hidden and can't be referenced. Progress is kept
per model in `zooks/<model>/challenge-<name>.json`:

```sh
python tools/challenge.py status --model claude-opus-5-5
python tools/challenge.py reset  --model claude-opus-5-5     # give it a fresh budget
```

For a fair run, give the model **only the MCP tools**, not file access to this repo (Claude
Desktop, or Claude Code started in an empty folder). The MCP server hides other models' designs,
but a model that can read files could still open `zooks/` directly.

The model-facing design guide is `src/bamzooki/docs/zook_design_guide.md`, also served by
`get_design_guide`. It covers how parts attach and point, joints, the stride cycle and paths
(single/double), steering, targeting, body waves, gait recipes, what wins each kind of
contest, and the testing loop.

Designs are kept per model in `zooks/<model>/<Name>/<n>.0.zook` with a `.json` (spec, stats,
notes) and preview PNGs; results append to `zooks/<model>/results.jsonl`. `zooks/` is
gitignored. The same features are available in Python via `bamzooki.studio.Studio`.

A Zook spec is a tree of Blob parts (see `src/bamzooki/sdk/schema.py` or `get_design_guide`):

```json
{"name": "Quaddy", "body": {"size": {"width": 1.2, "height": 0.6, "length": 1.8},
  "gait": {"cycle_speed": 0.6}, "look": {"rgb": [0.2, 0.7, 1.0], "texture": "spots"},
  "children": [
    {"name": "front leg", "size": {"width": 0.25, "height": 0.25, "length": 1.1},
     "attach": {"elevation": 35, "around": 55}, "rotate": {"up_down": 45},
     "movement": {"type": "single", "phase": 0}, "mirror": true, "mirror_phase": 0.5}]}}
```

## Layout

```
src/bamzooki/
  codec/        archive (.zook/.bmi) and script (.ssx/.sax) codecs
  install.py    install discovery and runtime key derivation
  config.py     config file and env var handling
  guard.py      asset/secret guard (used by pre-commit, CI and tests)
  sdk/          Zook SDK: genome codec, design schema, spec <-> genome, validation, zoo store
  runner/       engine runner: sandbox, Lua harness, contests, judge, video, replays, workshop
  studio.py     high-level API for models (design, preview, run, results)
  mcp_server/   MCP server wrapping the studio
tools/          extract.py, check_assets.py, spike scripts
tests/          synthetic codec tests plus install-backed tests (skipped without an install)
```
