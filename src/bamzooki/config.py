"""Harness configuration: install path, save path, Wine command, cache dir.

Lookup order for the config file:
  1. $BAMZOOKI_CONFIG
  2. ./bamzooki.toml (current directory)
  3. <repo root>/bamzooki.toml

Each value can be overridden by an environment variable. Missing values are
left as None; `bamzooki.install` does auto-discovery.
"""

from __future__ import annotations

import os
import shlex
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_NAME = "bamzooki.toml"

_ENV = {
    "install_dir": "BAMZOOKI_INSTALL",
    "save_dir": "BAMZOOKI_SAVE_DIR",
    "wine_command": "BAMZOOKI_WINE",
    "cache_dir": "BAMZOOKI_CACHE_DIR",
    "render_dir": "BAMZOOKI_RENDER_DIR",
    "zoo_dir": "BAMZOOKI_ZOO_DIR",
}


@dataclass
class Config:
    install_dir: Path | None = None
    save_dir: Path | None = None
    wine_command: list[str] = field(default_factory=list)
    cache_dir: Path = REPO_ROOT / ".cache" / "bamzooki"
    render_dir: Path = REPO_ROOT / "renders"  # MP4s: <render_dir>/<model>/<timestamp>_....mp4
    zoo_dir: Path = REPO_ROOT / "zooks"  # model designs: <zoo_dir>/<model>/<Name>/<n>.0.zook
    source: Path | None = None  # config file that was read, if any


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
    cfg = Config()
    raw: dict = {}
    if (path := _config_file()) is not None:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
        cfg.source = path
    for key, env in _ENV.items():
        if env in os.environ and os.environ[env] != "":
            raw[key] = os.environ[env]

    base = cfg.source.parent if cfg.source else REPO_ROOT
    if v := raw.get("install_dir"):
        cfg.install_dir = _resolve(v, base)
    if v := raw.get("save_dir"):
        cfg.save_dir = _resolve(v, base)
    if v := raw.get("cache_dir"):
        cfg.cache_dir = _resolve(v, base)
    if v := raw.get("render_dir"):
        cfg.render_dir = _resolve(v, base)
    if v := raw.get("zoo_dir"):
        cfg.zoo_dir = _resolve(v, base)
    if v := raw.get("wine_command"):
        cfg.wine_command = shlex.split(v) if isinstance(v, str) else list(v)
    return cfg
