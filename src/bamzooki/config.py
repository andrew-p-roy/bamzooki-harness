"""Harness configuration: install path, save path, Wine command, cache and arena folders.

Lookup order for the config file:
  1. $BAMZOOKI_CONFIG
  2. ./bamzooki.toml (current directory)
  3. <repo root>/bamzooki.toml

Each value can be overridden by an environment variable. Missing values are
left as None; `bamzooki.install` does auto-discovery.

Everything models produce lives in the **arena folder**, outside the repo by default:
    <arena_dir>/Zook Entries/<model>/...   designs, challenge progress, results log
    <arena_dir>/Renders/<model>/...        contest/trial videos and stills
    <arena_dir>/Transcripts/<model>/...    arena runner conversations (tools/arena.py)
"""

from __future__ import annotations

import os
import shlex
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_NAME = "bamzooki.toml"
DEFAULT_ARENA = Path.home() / "BAMZOOKi Arena"

_ENV = {
    "install_dir": "BAMZOOKI_INSTALL",
    "save_dir": "BAMZOOKI_SAVE_DIR",
    "wine_command": "BAMZOOKI_WINE",
    "cache_dir": "BAMZOOKI_CACHE_DIR",
    "arena_dir": "BAMZOOKI_ARENA_DIR",
    "render_dir": "BAMZOOKI_RENDER_DIR",
    "zoo_dir": "BAMZOOKI_ZOO_DIR",
    "challenge": "BAMZOOKI_CHALLENGE",
}


@dataclass
class Config:
    install_dir: Path | None = None
    save_dir: Path | None = None
    wine_command: list[str] = field(default_factory=list)
    cache_dir: Path = REPO_ROOT / ".cache" / "bamzooki"
    arena_dir: Path = DEFAULT_ARENA  # models' output, kept outside the repo
    render_dir: Path | None = None  # None = <arena_dir>/Renders
    zoo_dir: Path | None = None  # None = <arena_dir>/Zook Entries
    challenge: Path | None = None  # challenge TOML (None = challenges/default.toml)
    arena: dict = field(default_factory=dict)  # [arena] table for tools/arena.py
    source: Path | None = None  # config file that was read, if any

    def __post_init__(self):
        if self.zoo_dir is None:
            self.zoo_dir = self.arena_dir / "Zook Entries"
        if self.render_dir is None:
            self.render_dir = self.arena_dir / "Renders"

    @property
    def transcripts_dir(self) -> Path:
        return self.arena_dir / "Transcripts"


def _config_file() -> Path | None:
    if env := os.environ.get("BAMZOOKI_CONFIG"):
        return Path(env)
    for candidate in (Path.cwd() / CONFIG_NAME, REPO_ROOT / CONFIG_NAME):
        if candidate.is_file():
            return candidate
    return None


def _resolve(p: str, base: Path) -> Path:
    path = Path(os.path.expandvars(os.path.expanduser(p)))
    return path if path.is_absolute() else (base / path)


def load_config() -> Config:
    raw: dict = {}
    source = _config_file()
    if source is not None:
        raw = tomllib.loads(source.read_text(encoding="utf-8"))
    for key, env in _ENV.items():
        if env in os.environ and os.environ[env] != "":
            raw[key] = os.environ[env]

    base = source.parent if source else REPO_ROOT
    paths = {k: _resolve(raw[k], base) for k in ("install_dir", "save_dir", "cache_dir", "arena_dir",
                                                 "render_dir", "zoo_dir", "challenge") if raw.get(k)}
    cfg = Config(**paths, arena=dict(raw.get("arena", {})), source=source)
    if v := raw.get("wine_command"):
        cfg.wine_command = shlex.split(v) if isinstance(v, str) else list(v)
    return cfg
