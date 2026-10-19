# BAMZOOKi AI harness

Lets AI models design BAMZOOKi creatures (Zooks) through an API and test them in the
**real** Bonsai engine, with results returned as data. See
[Documentation/BAMZOOKI_HANDOFF.md](Documentation/BAMZOOKI_HANDOFF.md) for the file formats,
engine findings and roadmap.

Status: M0 (setup and feasibility). The codec and key derivation work; the SDK,
engine runner and MCP server are stubs.

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

## Layout

```
src/bamzooki/
  codec/        archive (.zook/.bmi) and script (.ssx/.sax) codecs
  install.py    install discovery and runtime key derivation
  config.py     config file and env var handling
  guard.py      asset/secret guard (used by pre-commit, CI and tests)
  sdk/          Zook SDK (M1, stub)
  runner/       engine runner (M2, stub)
  mcp_server/   MCP server (M4, stub)
tools/          extract.py, check_assets.py, spike scripts
tests/          synthetic codec tests plus install-backed tests (skipped without an install)
```
