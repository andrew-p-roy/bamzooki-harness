# BAMZOOKi AI harness

Lets AI models design BAMZOOKi creatures (Zooks) through an API and test them in the
**real** Bonsai engine, with results returned as data. See
[Documentation/Bamzooki_Harness.md](Documentation/Bamzooki_Harness.md) for the file formats,
engine findings, how everything works and what we learned from model test sessions.

Status: working end to end. Codecs and key derivation, Zook SDK, on-screen contest and trial
runner with verdicts, video and replays, an MCP server for models (with challenges and run
budgets), and an **arena** in which any OpenRouter model takes the challenge while you watch
and chat with it.

## Quickstart: let OpenRouter models compete

You need BAMZOOKi installed (see [Bring your own copy](#bring-your-own-copy-no-bbc-files-or-keys-in-this-repo)),
Python 3.11+, ffmpeg on PATH (for videos), and an [OpenRouter](https://openrouter.ai) API key.

**1. One-time setup** (Windows shown; see [Setup](#setup) for details and other systems)

```
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[dev]"
copy bamzooki.example.toml bamzooki.toml
echo OPENROUTER_API_KEY=sk-or-...your key...> .env
```

`bamzooki.toml` works as-is on a default Windows install. `.env` is gitignored, and the key is
never printed or logged. Optionally copy your own BAMZOOKi manual PDF to
`Model Instructions/BAMZOOKi Manual.pdf` so models can read it with `read_manual`.

**2. Start the arena, naming the models to call** (any OpenRouter model id that supports tool
calling, from [openrouter.ai/models](https://openrouter.ai/models)):

```
.venv\Scripts\python.exe tools\arena.py --challenger anthropic/claude-sonnet-4.5 --challenger openai/gpt-5
```

**3. At the `you>` prompt**

| Type | What happens |
|---|---|
| `/next` | Calls the next challenger. It plays the whole challenge on screen, then presents its Zook, design and testing strategy. |
| `/challenger <id>` | Calls any model now, e.g. `/challenger google/gemini-2.5-pro` |
| anything else | Talks to the current model, e.g. "why four legs?" |
| `/leaderboard` | Everyone's result so far |
| `/contest` | Knockout tournament between the Zooks that **won** their challenge game this session. The champion then gives a speech and takes your questions |
| `/open` | Plays the current model's latest video |
| `/status` | The current model's runs left |
| `/list`, `/help`, `/quit` | The queue, the commands, leave |

Tips:
- **Leave the BAMZOOKi window in front while a game plays, and don't close it.** The game only runs
  while its window has focus.
- The default challenge is "Beat Spider at Sumo" with 20 test runs. For a quick show, add
  `--challenge challenges/short.toml` (3 runs).
- To skip typing the models, list them in `bamzooki.toml`:
  `[arena]` → `challengers = ["anthropic/claude-sonnet-4.5", "openai/gpt-5"]`, then run
  `tools\arena.py` and use `/next`.
- Results land outside the repo in `~/BAMZOOKi Arena/` (`Zook Entries/<model>/`, `Renders/<model>/`,
  `Transcripts/<model>/`).
- Each model gets one attempt per challenge. Let one try again with
  `.venv\Scripts\python.exe tools\challenge.py reset --model <id> --challenge challenges/short.toml`.
- Token use and cost are printed after each reply.

Full details are in [The arena](#the-arena-models-compete-openrouter) below.

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
| `arena_dir`    | `BAMZOOKI_ARENA_DIR` | where models' work goes, **outside the repo** (default `~/BAMZOOKi Arena`) |
| `challenge`    | `BAMZOOKI_CHALLENGE` | challenge file (default `challenges/default.toml`)              |

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

**Renders** go to `<arena_dir>/Renders/<model>/<YYYY-MM-DD_HH-MM-SS>_<contest>_<green>-vs-<red>_seed<N>.mp4`,
with a `.json` beside each one (verdict, Zooks, seed, run folder). `<model>` is the model operating
the run: pass `--model claude-opus-5-5` (or `model=` in Python, or set `BAMZOOKI_MODEL`); the
default is `manual`. Replay re-renders are named `<timestamp>_<run>_replay-<view>.mp4`.
`render_dir` in `bamzooki.toml` overrides the folder.

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
claude mcp add bamzooki -- <repo>/.venv/Scripts/python.exe -m bamzooki.mcp_server --model anthropic/claude-opus-5-5
```

`--model` is the entrant's **full model name**. Its work is filed under
`<arena_dir>/Zook Entries/<name with / replaced by _>/`. Leave `--model` out and the model
registers itself with `register_entrant("<its full model name>")` on its first call (only
new names are accepted).

For Claude Desktop, add the same command to `claude_desktop_config.json` under `mcpServers`.
With stdio the client starts the server itself, so there is nothing else to run. For other agents
or frameworks there is a **local HTTP endpoint** instead:

```sh
python -m bamzooki.mcp_server --model my-agent --http          # http://127.0.0.1:8765/mcp
```

Tools: `register_entrant`, `get_challenge`, `submit_design`, `get_design_guide`, `read_manual`,
`list_contests`, `get_contest`, `list_zooks`, `get_zook`, `validate_zook`, `save_zook`,
`edit_zook`, `preview_zook`, `run_trial`, `run_contest`, `list_results`. Results come straight
back in the tool response (the verdict, scores, distance travelled, falls, plus a 2×2 sheet of
stills for vision models) and are logged to `<arena_dir>/Zook Entries/<model>/results.jsonl`. The game window appears whenever a tool needs the engine
(save/preview: a few seconds; trial: 10–30 s; contest: up to about two and a half minutes). If your
client times out long tool calls, raise its limit (Claude Code: `MCP_TOOL_TIMEOUT`, in ms).

**Challenges.** The server gives the model a task from a challenge file
(`challenges/default.toml`; choose another with `challenge = "..."` in `bamzooki.toml`,
`BAMZOOKI_CHALLENGE` or `--challenge`, or run `--no-challenge` for free play). A challenge sets
the task text, the **test-run budget** (default 20; failed engine runs are free) and the **one
evaluation game** that `submit_design` plays (contest, opponent, seed, side; outside the budget;
win 1, draw 0.5, loss 0). The evaluation seed is **secret** by default (`reveal_seed = false`):
games are deterministic, so a model that knows the seed can simply rehearse the evaluation game.
`challenges/short.toml` is the same task with 3 runs. Every contest in a challenge stops after
`time_limit` seconds of play (default 120, in `[scoring]`); no winner by then is a draw. **Every test run is a single game that the model chooses** through the
tool arguments: a trial for one of its Zooks, or one contest with the two Zooks and the seed it
picks. Which games to spend runs on is its testing strategy. Every run result reminds the model
how many runs are left. In its tests a model can only use the example Zooks
and its own designs; other models' designs are hidden and can't be referenced. Progress is kept
per model in `<arena_dir>/Zook Entries/<model>/challenge-<name>.json`:

```sh
python tools/challenge.py status --model claude-opus-5-5
python tools/challenge.py reset  --model claude-opus-5-5     # give it a fresh budget
```

For a fair run, give the model **only the MCP tools**, not file access to this repo or the
arena folder (the arena runner below, Claude Desktop, or Claude Code started in an empty
folder with `Model Instructions/Agent Prompt.md` pasted as the first message). The MCP server
hides other models' designs, but a model that can read files could still open them directly.

**Model Instructions.** Everything models are told is in the top-level
[`Model Instructions/`](Model%20Instructions/README.md) folder, so you can read and reword it:
`Agent Prompt.md` (system prompt: tools only, how to work, the presentation at the end),
`Zook Design Guide.md` (served by `get_design_guide`: how parts attach and point, joints, the
stride cycle and paths, steering, targeting, body waves, gait recipes, what wins, testing
strategy), and the official manual, served by `read_manual`. The manual is BBC material, so
**copy your own PDF** there as `BAMZOOKi Manual.pdf` (gitignored; the asset guard rejects PDFs).

**The arena folder** (`arena_dir`, default `~/BAMZOOKi Arena`) keeps all model output outside
the repo:

```
Zook Entries/<model>/<Name>/<n>.0.zook (+ .json, previews)   designs (spec, stats, notes)
Zook Entries/<model>/challenge-<name>.json, results.jsonl     budget, submissions, results
Renders/<model>/...mp4 (+ _stills.png, .json)                 videos and contact sheets
Transcripts/<model>/<timestamp>.md                            arena conversations
```

The same features are available in Python via `bamzooki.studio.Studio`.

## The arena: models compete (OpenRouter)

`tools/arena.py` lets **any tool-calling model on OpenRouter** take the challenge, one after
another. Each challenger gets its own MCP server running in the arena folder and sees nothing
but the bamzooki tools (plus the preview and contest images if it has vision). You watch its
reasoning in the terminal and its games on screen. When it has submitted, it presents its
challenger Zook, its design and its testing strategy, and then **you can talk to it**.

```sh
# put OPENROUTER_API_KEY=... in .env (gitignored) or the environment
python tools/arena.py --challenger anthropic/claude-sonnet-4.5 --challenger openai/gpt-5
python tools/arena.py --challenge challenges/short.toml           # quick 3-run sessions
python tools/arena.py --auto --challenger google/gemini-2.5-flash # unattended, then leaderboard
```

At the `you>` prompt:

| command | |
|---|---|
| `/next` | call the next challenger in the queue |
| `/challenger <id>` | call any OpenRouter model, e.g. `/challenger x-ai/grok-4` |
| `/leaderboard` | best submission per entrant in this challenge |
| `/contest` | knockout tournament between this session's qualified Zooks (see below) |
| `/status` | the current challenger's budget and submissions |
| `/open` | open its latest video and stills |
| `/list`, `/help`, `/quit` | queue, commands, leave |
| anything else | a message to the current challenger ("why four legs?", "try a heavier one") |

Between tool calls the terminal shows what the model says and, when OpenRouter returns it, its
reasoning, each cut to 100 words. Its final answers and the transcripts are always shown in full.
Add `--verbose` (`-v`) to see the reasoning, notes and tool arguments uncut. The reply is then
streamed, so it appears word by word as the model writes it. Streaming costs the same per token.

**Talking to a model while it works.** You can type at any time. The model gets your message
after its current step (the tool call or game it's in), takes it into account and carries on.
`/stop` halts it after that step, and you can then talk to it or move on with `/next`. Other
commands wait until the model is done.

**Qualifying and `/contest`.** A model qualifies by **winning** its challenge game. When the
last challenger in the queue has finished and at least two models have qualified, the arena asks
whether to run the contest now (`Y`/`N`). `N` leaves `/contest` for later. A draw (no
winner by the time limit) scores 0.5 but doesn't qualify. `/contest` can be called at any point
in the session, whether or not the queue is finished. It plays a knockout between the
submitted Zooks of every model that has qualified so far, using the challenge's contest and time
limit. Each match is announced as `model (Zook) v model (Zook)`. A drawn game is replayed with
sides swapped and a new seed, up to three games, and is then settled by a coin toss. A game the
engine fails to finish is played once more, then counts as a draw. Each game's result (scores,
how it was decided, finish time, falls) is printed, and the next match starts when you press
Enter. Winners play winners until one is left. The ranking follows the round each model went
out in, and the champion gives a short speech and takes questions at the `you>` prompt. Videos
are saved to `Renders/Tournament/`.

Defaults can live in `bamzooki.toml`:

```toml
[arena]
challengers = ["anthropic/claude-sonnet-4.5", "openai/gpt-5", "google/gemini-2.5-pro"]
challenge = "challenges/short.toml"
max_turns = 120      # model turns per challenger before the runner stops it
max_tokens = 4000    # per reply
```

**While a game is playing, leave the engine window in front** and don't close it. The game only
runs while its window has focus, and the runner takes focus back only once you've been idle for
3 s. A run that is starved or closed is reported as `stalled` or `engine_error`. It doesn't
count against the budget, and a failed `submit_design` can simply be retried.

Each challenger's whole conversation is saved to `Transcripts/<model>/`, and the tokens and
cost (from OpenRouter) are printed after each reply. Models without tool calling are skipped.
To let a model try again, run
`python tools/challenge.py reset --model <id> --challenge challenges/short.toml` (its designs are
kept; only the budget and submissions reset).

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
  challenge.py  challenge files, run budgets, submissions
  manual.py     read_manual: the Zook Kit PDF as text
  mcp_server/   MCP server wrapping the studio
  arena.py      OpenRouter arena runner
Model Instructions/  agent prompt, design guide, your manual copy (what models are told)
challenges/     challenge files (task, budget, evaluation game)
tools/          arena.py, run_contest.py, replay.py, challenge.py, extract.py, check_assets.py, spikes
tests/          synthetic codec tests plus install-backed tests (skipped without an install)
```
