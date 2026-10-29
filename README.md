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
git update-index --skip-worktree bamzooki.toml
echo OPENROUTER_API_KEY=sk-or-...your key...> .env
```

`bamzooki.toml` comes with the defaults the tournament runs with, and works as-is on a default
Windows install; the `skip-worktree` line keeps your own edits to it out of git. `.env` is gitignored, and the key is
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
| `/contest` | Knockout tournament between this session's qualified models, one contest per round (see the rules). The champion then gives a speech and takes your questions |
| `/pick <name>` | Before a knockout match: predict the winner (you're scored at the end) |
| `/exhibition <a> <b>` | One match between any two Zooks submitted this session, qualified or not (with the show if `--showtime`) |
| `/halloffame` | The champions you chose to add after a knockout, across sessions |
| `/open` | Plays the current model's latest video |
| `/status` | The current model's runs left |
| `/end` | Finishes the session: one table of every model's Zook, result, test runs, knockout place, tokens and cost (with totals), saved to `Sessions/`. Enter then quits |
| `/list`, `/help`, `/quit` | The queue, the commands, leave |

Tips:
- **Leave the BAMZOOKi window in front while a game plays, and don't close it.** The game only runs
  while its window has focus.
- The default challenge is "Beat Spider at Sumo" with 20 test runs (contests) and 50 trials. For a
  quick show, add `--challenge challenges/short.toml` (3 runs, 5 trials).
- To skip typing the models, list them in `bamzooki.toml`:
  `[arena]` → `challengers = ["anthropic/claude-sonnet-4.5", "openai/gpt-5"]`, then run
  `tools\arena.py` and use `/next`.
- Results land outside the repo in `~/BAMZOOKi Arena/` (`Zook Entries/<model>/`, `Renders/<model>/`,
  `Transcripts/<model>/`).
- Every run of the arena starts afresh: the previous `Zook Entries/` (designs, budgets,
  submissions) move to `Archive/<date_time>/Zook Entries/`, so each model gets another go. Add
  `--keep-entries` to carry on from the last session instead.
- Token use and cost are printed after each reply.
- Add **`--showtime`** for the full show, at the cost of some extra model calls:
  - an **announcer** (Claude Haiku 5.5 by default, a fraction of a cent per line) introduces each
    challenger with its maker, release month and year, country and some trivia, then calls the games;
  - in the knockout the models **see each other's Zooks** and trade a line before each match, and
    afterwards they're interviewed with stills of how the game ended;
  - while each model presents its Zook, the Zook stands on a **turntable** in the engine (shown
    like the games: its own window, `--monitor` or `--full-screen`) until `/next`;
  - the champion gives a victory speech;
  - challenge and knockout games play their **sound** live (Windows): a starting beeper on the
    countdown and the contest's music looping under the game from Go (the videos and highlights
    have it either way);
  - **design music** plays live while the models design: three chill songs one after another,
    round and round, carrying on through their trials, practice games and presentations. It fades
    out for a qualifying game (which has its contest's music) and for the knockout.

  `--nomusic` turns off all the music, live and in the videos and highlights; the countdown beeps
  stay (`[arena] music = false` in the config does the same).

  With it, `--speak` reads the announcer aloud, and `--announcer-style documentary` changes the host.
  For a natural British announcer, `pip install -e .[voice]` (Kokoro; its 340 MB model downloads on
  first use): the default voice is then `bm_george`. Without it, Windows speech reads the lines.
  Without `--showtime` the arena makes no extra calls. You can still talk to any model yourself,
  and the champion still takes your questions.

Full details are in [The arena](#the-arena-models-compete-openrouter) and [The show](#the-show-announcer-pictures-interviews) below.

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
git update-index --skip-worktree bamzooki.toml   # your edits to the config stay out of git
```

`bamzooki.toml` is in the repo with the default settings (edit its paths on other systems;
`bamzooki.example.toml` documents every setting). With `skip-worktree` set, git ignores your
changes to it; `git update-index --no-skip-worktree bamzooki.toml` undoes that, e.g. to commit new
defaults. Each setting can also be given by an environment variable:

| key            | env var              | meaning                                                         |
|----------------|----------------------|-----------------------------------------------------------------|
| `install_dir`  | `BAMZOOKI_INSTALL`   | folder containing `Bonsai.exe` and `he.skx`                      |
| `save_dir`     | `BAMZOOKI_SAVE_DIR`  | per-user save root (Windows: `%APPDATA%\Bamzooki`)              |
| `wine_command` | `BAMZOOKI_WINE`      | launcher prefix on non-Windows, e.g. `["wine"]`                 |
| `cache_dir`    | `BAMZOOKI_CACHE_DIR` | extraction cache and install sandbox (default `.cache/bamzooki`) |
| `arena_dir`    | `BAMZOOKI_ARENA_DIR` | where models' work goes, **outside the repo** (default `~/BAMZOOKi Arena`) |
| `challenge`    | `BAMZOOKI_CHALLENGE` | rules file, if `[arena] challenge_rules` isn't set (default `short`) |

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

**Challenges: the rules.** Three settings in `bamzooki.toml` `[arena]` decide the challenge
(`--challenge` overrides the rules; `--no-challenge` on the MCP server is free play):

- `challenge_rules = "short"` or `"full"` (`challenges/<name>.toml`, or a path to your own) sets the
  limits, the scoring, qualifying, the knockout's size and whether the challenges are hidden.
- `contest` and `opponent` are arrays with **one element per knockout round**, the final last;
  `""` = random (no contest or opponent picked twice while there are others). The arena plans them
  once (`Zook Entries/session.json`) so every model faces the same rounds.
- `hidden` overrides the rules file's: **short is visible, full is hidden** by default.

| | Visible | Hidden |
|---|---|---|
| **short** | **A design phase before each knockout round**, with **fresh limits** each time: every model designs round 1's Zook, then after each knockout round **only the models still in** design the next round's. `qualifying` (in the rules file): `"first"` (default) = round 1's Zook plays a **qualifying game** against the round's opponent and a **win** gets the model in; `"all"` = every round's does, and losing a later one **forfeits** that round's match; `"none"`. | The same design phases, but **blind**: no qualifying, and each round's contest is **announced once its Zooks are in**. |
| **full** | One set of limits **shared by all the Zooks**, all designed up front: a separate Zook for each challenge, one put forward per challenge (`submit_design(ref, round)`). **No qualifying**: everyone who puts Zooks forward is in. | The model doesn't know the contests: it designs a **team of one Zook per round** (any contest and opponent may be tested). |

**The field.** `max_qualifiers` (in the rules file, default **8**, enough for 3 rounds; `""` = no
limit) caps the knockout: once that many models have qualified (or, without qualifying, put their
Zooks forward) the rest of the queue isn't called and round 1 can start. At the start the arena
**checks the queue against the rounds** (a knockout of n models has ⌈log₂ n⌉ rounds) and, if they
don't match, says which rounds will be skipped or how many random ones added, and asks before
carrying on. A field that isn't a power of two gets **byes in the first round only**, just enough
to leave one (6 models: 2 byes and 2 matches, then 4), for the **best qualifiers** (round 1 score,
then fewest test runs; random without qualifying).

In the **knockout** (`/contest`) each round plays its element. Full rules: with fewer entrants the
first elements are skipped, so the final always plays the last; with more, the extra early rounds
are random. Short rules: **round 1 and the final always stay**, and rounds in between are dropped
(the earliest first) or random ones added after round 1 (a single element is round 1, and every
later round is random). A model plays the Zook it put forward for
that round's challenge. Hidden full rules: each round's contest is **announced as the round
starts** (drawn then if `""`, the same for every match in it) and each model in a match **picks one
of its team Zooks**, each only once. A model with **no Zook for the round** (none put forward, no
team Zook left, or a lost qualifying game under `"all"`) **forfeits** the match: the other goes
through without a game (a coin toss if neither can play). With several rounds the Simulator's Zook
selection screen shows the round, its contest and both Zooks before every match.
Every model's system prompt ends with **this session's rules** written out for the selected mode
(the rules, visible or hidden, how many Zooks it **must** put forward and how, the forfeit, the
limits, qualifying, the field, the knockout; `Session.briefing`), and `get_challenge` returns them
as `rules_of_this_session`.

**Models never see or choose seeds.** Practice contests get a fresh random seed each, and the
qualifying games' `seed` in `[evaluation]` is blank (random, decided as the session starts); seeds
are kept only in the operator's `results.jsonl`. Games are deterministic for a seed, so a model
that controlled or knew seeds could rehearse the real game; to the models, games are simply
non-deterministic. Every contest stops after `time_limit` seconds of play after Go, in real time
(default 120, in `[scoring]`); no winner by then is a draw and the game is stopped. **Every game
is one the model chooses** through the tool arguments: a test or trial for one of its Zooks, or one
contest with the two Zooks it picks.
**Copying an example is refused**: `save_zook`, `edit_zook`, `validate_zook` and `submit_design`
reject a design containing most of an example's parts nearly unchanged (`sdk.zook.likeness`:
angles within 18°, sizes within ×0.6–×1.65, at the same place in the tree, 70% of the example's
parts), with a message that names neither the example nor any setting. A model's own Zooks must
also be **distinct**: `submit_design` refuses one that shares half its parts, either way, with a
Zook it has already put forward (stricter than the 70% for examples), so a small change doesn't
make another Zook.
Trials don't use test runs; they have their own allowance (`trials` in `[budget]`; only
`run_contest` is in `counts`), so models are encouraged to compare different designs on them.
`test_zook` is the Zook Kit's Test button (the Zook walks to the Builder's target in BoxEnv, for
the 1–30 s the model chooses) on a third allowance, `test` in `[budget]`: 0 (unlimited) in the
standard challenges, so models can check every design and tweak.
Contests use the test runs, and which ones to play is its testing strategy. Every result reminds
the model how many runs and trials are left. In its tests a model can only use the example Zooks
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
Zook Entries/<model>/Videos/Test 03 - <challenge> - <contest> - <Zook> vs <opponent>.mp4
                         .../Test 04 - <challenge> - Trial sprint - <Zook>.mp4
                         .../Challenge game - <challenge> - <contest> - <Zook> vs <opponent>.mp4
                         .../Knockout Final game 1 - ... vs <opponent> (<its model>) - seed N.mp4
                                                              every game it played (+ _stills.png, .json)
Zook Entries/<model>/Presentation - <challenge>.txt           its presentation, with result, runs and cost
Renders/Tournament/..., Renders/<name>/...                    knockout games (+ the highlights reel); runs from tools/
Transcripts/<model>/<timestamp>.md (+ .mcp.log)              arena conversations (+ the MCP server's own log)
Transcripts/Announcer/<timestamp>.md                          everything the announcer said in a session
Hall of Fame.jsonl                                            knockout champions you chose to keep (across sessions)
Archive/<date_time>/Zook Entries/...                          earlier arena runs' entries (moved at start)
Sessions/<date_time>.txt                                      the /end table of each session
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
python tools/arena.py --auto --showtime --speak                  # the whole show, unattended
python tools/arena.py --monitor                                   # one screen: games on the right
python tools/arena.py --full-screen                               # games in windowed full screen
```

**Unattended (`--auto`).** The whole show runs start to finish with no `/next` or Enter: every
queued challenger in turn (each presentation, and its turntable, plays out before the next), then
the knockout if two qualify (every game in a row, Hall of Fame yes), the champion's speech and
the session table. With `--speak` it waits for the voice before moving on, so nothing is cut off.

**Full screen (`--full-screen`).** Test, challenge and tournament games, the turntable and the
selection screen run in windowed full screen: a borderless window covering the whole main screen,
taskbar included. The display mode never changes, so switching windows is quick and safe. The game's
picture is 4:3, at most 1024×768, centred on black. Capture slows the engine in proportion to the
picture's pixels: 1024×768 runs at about 0.77× real time (as fast as `--monitor`), while filling a 1080p screen's height
(1440×1080) ran at 0.3×, and games stop at a real-time limit. Videos are 1024×768. The terminal is
hidden while a game plays, and switching to it pauses the game. Without `--monitor` or `--full-screen`, every game uses the
engine's own window, where it opens. In every mode the window is black while a game loads (after
a moment of white while the engine itself starts).

**One screen (`--monitor`).** Test games (a model's `run_contest` and `run_trial`), its challenge
game (`submit_design`) and tournament games (`/contest`, `/exhibition`) fill the right half of the main screen with the engine window,
so the terminal can take the left half (Win+Left snaps it there). Inside the window the picture
stays 4:3, centred with black bars, and at most 944×708 (half a 1080p screen) on bigger screens:
capture slows the engine in proportion to the picture's pixels, and a slower engine plays less
of each game before the real-time limit (944×708 runs at about 0.79× real time against 0.83×
for the default window). Videos are the picture, so they stay 4:3. The window moves about 2 s
after it opens, when the engine starts handling window messages; nothing can move it sooner.
Previews and replays stay as they open. Without the flag, every window stays
where it opens (for example, on a second monitor). `tools/run_contest.py --monitor` does the same
for one game.

At the `you>` prompt:

| command | |
|---|---|
| `/next` | call the next challenger in the queue |
| `/challenger <id>` | call any OpenRouter model, e.g. `/challenger x-ai/grok-4` |
| `/leaderboard` | best submission per entrant in this challenge |
| `/contest` | knockout tournament between this session's qualified Zooks (see below) |
| `/pick <name>` | before a knockout match: predict the winner |
| `/exhibition <a> <b>` | one match between any two Zooks submitted this session (doesn't change the ranking) |
| `/halloffame` | the champions you added after a knockout (`y` at its prompt), across sessions |
| `/status` | the current challenger's budget and submissions |
| `/open` | open its latest video and stills |
| `/end` | finish with the session table (also saved to `Sessions/<date_time>.txt`), then Enter quits; works while a model is working too |
| `/list`, `/help`, `/quit` | queue, commands, leave |
| anything else | a message to the current challenger ("why four legs?", "try a heavier one") |

Between tool calls the terminal shows what the model says and, when OpenRouter returns it, its
reasoning, each cut to 100 words. Its final answers and the transcripts are always shown in full.
Add `--verbose` (`-v`) to see the reasoning, notes and tool arguments uncut. The reply is then
streamed, so it appears word by word as the model writes it. Streaming costs the same per token.

**Talking to a model while it works.** You can type at any time. The model gets your message
after its current step (the tool call or game it's in), takes it into account and carries on.
`/stop` halts it after that step, and you can then talk to it or move on with `/next`. `/next`
typed while it works halts it and calls the next challenger straight away, which is the way out
of a model that is stuck or not playing. Other commands wait until the model is done. If a model
breaks down (an error in its run), the arena reports it and stays at the prompt.

**Qualifying and `/contest`.** Under short rules with visible challenges a model qualifies by
**winning** round 1's qualifying game, played like a knockout game: the "3, 2, 1, Go!"
countdown and beeps, then the match music (live with `--showtime`, in its video either way). A draw
(no winner by the time limit) scores 0.5 but doesn't qualify. With `qualifying = "none"`, under
full rules, or with hidden challenges, there's no qualifying: every model that puts Zooks forward
is in. When the last challenger in the queue has finished, or `max_qualifiers` models have
qualified, and at least two have, the arena asks whether to run the contest now (`Y`/`N`). `N`
leaves `/contest` for later. `/contest` can be called at any point in the session, whether or not
the queue is finished. It plays a knockout between the models that have qualified so far (the best
`max_qualifiers` of them). Under short rules, before each round after the first, the arena calls
each model still in back to its own conversation to design that round's Zook (its MCP server opens
the round with fresh limits); the round's matches start once every one has put a Zook forward or
stopped. Each round announces its contest (see the rules above), and
each match is announced as `model (Zook) v model (Zook)` with the Zooks played in it. A drawn game is replayed with
sides swapped and a new seed, up to three games, and is then settled by a coin toss. A game the
engine fails to finish is played once more, then counts as a draw. With `--showtime`, before each
match both models say one line to the audience (a comment, trash talk or a boast about their
Zook), and the second one hears the first. You can talk to them before the game and again after it
(with or without `--showtime`): `/<name> <message>` talks
to one model (its model name, the part after the `/`, or its Zook's name; any part of a name only one of them has will do, e.g. `/nano`),
plain text goes to both, and Enter plays on. Each game's result (scores, how it was decided,
finish time, falls) is printed. Every game starts with the game's own countdown, a big "3, 2, 1,
Go!" over the arena (also in the video; `tools/run_contest.py --countdown` for a single game).
Each game video has sound: a starting beeper (a beep on each count and a higher one on Go), then
from Go the contest's music looping under the game and fading out as it ends; the
highlights reel keeps it. With `--showtime` the same sound also plays live on your speakers
(Windows). `tools/run_contest.py --music synth_funk` for a single game; the beeps come with
`--countdown`.
Winners play winners until one is left. The ranking follows the round each model went
out in. The champion takes your questions at the `you>` prompt; with `--showtime` it first gives a
short victory speech. Videos
are saved to `Renders/Tournament/`, along with a highlights reel of every game, which `/open`
plays. The reel is made by ffmpeg alone, with no model calls and no tokens:
- each game starts with a 3 s title card: the round and game, then each Zook's name in its arena
  colour (green/red) with its model underneath;
- a caption bar names both Zooks while the game plays;
- it ends on a champion card;
- each game keeps its own sound: the countdown's beeps, then the **music** from Go, fading out
  over a second as the game ends (a game video without sound gets the music from its top); the
  cards are silent.

The music is in [`Music/`](Music/tracks.toml): original instrumental tracks made with Google's
Lyria 3 through OpenRouter, so there are no copyright claims. The game tracks are 30 s clips,
three written for each contest; each has loop points, whole 4-bar phrases that stop before any fade
at the end of the clip, so the loop is seamless. Which track plays under a game:
1. the challenge's own `music = "<track>"` (`challenges/*.toml`; `"none"` for silence; unset in
   the rules as shipped);
2. else the contest's own track in the `[contests]` table of `tracks.toml` (Zook Sumo:
   `sumo_wrestling_entrance`, Zookball: `marbles_jazz_funk_race`, ...); swap in any other track there;
3. else the first track whose `tags` include the contest (e.g. `"zook sumo"`);
4. else the track marked `default` (now `synth_funk`).

The design music is the tracks tagged `"design"`, about three minutes each (Lyria 3 Pro), played
whole in the order listed: Berlin-school analogue synths, dream pop, and lo-fi beats.

`python tools/music.py list` shows each challenge's pick. To add a track, drop the MP3 in
`Music/` and run `python tools/music.py analyse Music/<file>.mp3`, which prints its tempo, fade
and loop points as an entry for `tracks.toml`. `python tools/music.py loop <track> out.mp3` plays
a track's loop three times, so you can listen to the seam.

After the knockout the arena asks `Add <model> (<Zook>) to the Hall of Fame? [y/N]`. Only `y`
records the champion, so test sessions stay out of it. `/halloffame` shows the list.

### The show: announcer, pictures, interviews

Everything in this section that calls a model needs **`--showtime`** (or `[arena] showtime = true`).
Without it the arena calls the models only for their own challenge, for what you say to them,
and for the champion's answers to your questions. These work either way, since they use no
tokens: the tale of the tape on screen, the fact card under each banner, `/pick`, `/exhibition`,
the highlights reel with music, and the Hall of Fame prompt and `/halloffame`.

- **The announcer** is a second, cheap model with no tools. The default is
  `anthropic/claude-haiku-5.5`: expressive, it sees images, and costs $0.10 / $0.50 per million
  tokens, so a whole one-match show costs well under a cent.
  - It **introduces each challenger** from a fact sheet built from OpenRouter's model list: maker,
    release month and year (from the dated model slug), home country (a table of labs; unknown labs
    get none rather than a guess), age, context window, price and description. It is told to stick
    to those facts. The same facts give a one-line card under each challenger's banner.
  - It **calls each challenge game** from its stills.
  - In the knockout it **builds up each match** from both Zooks' pictures, **calls the result** from
    stills of how the game ended, and **interviews** the winner and then the loser.
  - It crowns the champion and **signs off** at `/end`.
  - A returning champion's Hall of Fame record goes into its fact sheet and the final standings.
- **The champion's victory speech** before it takes questions.
- **Seeing the opponent.** Before each match a tale of the tape (weight, parts, size, limbs, stride,
  look) is printed. Each model sees the other's preview pictures, or reads its stats if it can't
  see images, before it gives its pre-match line.
- **End-of-game stills.** After each match a 2×2 sheet (Go | 4 s before the end / just before the
  end | the end) is cut from the deciding game's video into a temporary folder. The announcer and
  both interviewees see it, so they know what happened. It is deleted after the interviews.
- **Audience games.** `/pick <name>` before a match: the result tells you whether you called it,
  and the knockout ends with your score. `/exhibition <a> <b>` plays one match with the whole show
  between any two submitted Zooks.

| option | |
|---|---|
| `--showtime` | turn the show on (`[arena] showtime = true`); the options below need it |
| `--announcer <model>` | any OpenRouter model (`[arena] announcer`) |
| `--no-announcer` | the show without an announcer; interviews use stock questions |
| `--announcer-style <style>` | `ringside` (default), `documentary`, `football`, `noir`, or describe your own: `--announcer-style "a pirate captain who is new to robots"` |
| `--speak` | read the announcer aloud (Kokoro if installed with `pip install -e .[voice]`, else Windows speech, macOS `say`, Linux `espeak`; `[arena] speak = true`) |
| `--voice <name>` | a Kokoro voice: `bm_george` (default, British male), `bm_lewis`, `bm_daniel`, `bf_emma`, ...; or a system one, e.g. `"Microsoft Hazel Desktop"`. `[arena] voice_speed` sets Kokoro's pace (default 1.1) |
| | With Kokoro, each model also speaks its presentation, trash talk, interview answers and replies to you in its own voice: Gemini and Qwen female, GPT and Claude male (`[arena.voices]`) |

The announcer's tokens and cost are a row in the `/end` table. Everything it said is kept in
`Transcripts/Announcer/`.

Defaults can live in `bamzooki.toml`:

```toml
[arena]
challengers = ["anthropic/claude-sonnet-4.5", "openai/gpt-5", "google/gemini-2.5-pro"]
challenge = "challenges/short.toml"
max_turns = 120      # model turns per challenger before the runner stops it
max_tokens = 4000    # per reply
showtime = true      # the show: announcer, pre-match lines, interviews, victory speech
announcer = "anthropic/claude-haiku-5.5"
announcer_style = "ringside"
speak = true
voice = "bm_george"   # the announcer: Kokoro (pip install -e .[voice]); or e.g. "Microsoft Hazel Desktop"
voice_speed = 1.1
presentation_words = 120  # with showtime: the presentation length the models are asked for (read in full)

[arena.voices]        # --speak: each model's own voice (by lab or full model id)
google = "af_heart"   # Gemini: American female
qwen = "bf_emma"      # Qwen: British female
openai = "am_puck"    # GPT / Luna: American male
anthropic = "am_michael"  # Claude: American male
monitor = true       # one screen: test and tournament games on the right
# full_screen = true # or: test and tournament games in windowed full screen
```

**While a game is playing, leave the engine window in front** and don't close it. The game only
runs while its window has focus, and the runner takes focus back only once you've been idle for
3 s. The time limit is real time: a game that has no winner `time_limit` seconds after Go is
stopped and counts as a draw. The game runs slower than real time while it records video, so the
limit is measured on both the game's clock and the real one, whichever runs out first. If the
engine doesn't stop itself, the runner stops it 20 s later, still as a draw. A run that never gets
to Go (starved of focus, or closed) is reported as `stalled` or `engine_error`. It doesn't count
against the budget, and a failed `submit_design` can simply be retried.

Each challenger's whole conversation is saved to `Transcripts/<model>/`, and the tokens and
cost (from OpenRouter) are printed after each reply. Models without tool calling are skipped.
Each run of the arena starts afresh: the previous `Zook Entries/` move to
`Archive/<date_time>/Zook Entries/` (`--keep-entries` carries on with them instead). To let one
model try again within a session, run
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
  tournament.py the /contest knockout bracket
  announcer.py  the show's host (introductions, calls, interviews)
  showtime.py   model fact sheets, Zook cards, end-of-game stills, Hall of Fame, highlights
  voice.py      --speak: the announcer read aloud (Kokoro, or the system's speech engine)
  music.py      the music (live, in the videos and the reel): tracks, loop points, each contest's track, the design songs
Music/          the music (Lyria 3) and tracks.toml (loop points, tags, each contest's track, design songs)
Model Instructions/  agent prompt, design guide, your manual copy (what models are told)
challenges/     rules files: short.toml, full.toml (limits, scoring, hidden, qualifying, max_qualifiers)
tools/          arena.py, run_contest.py, replay.py, challenge.py, extract.py, check_assets.py, spikes
tests/          synthetic codec tests plus install-backed tests (skipped without an install);
                test_modes.py: every rules/config combination; test_smoke.py: whole arena sessions
                under every game mode (scripted models, a stand-in engine) and the real MCP server
```
