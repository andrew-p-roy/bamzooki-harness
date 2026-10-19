"""Locate the user's BAMZOOKi install and derive the keys it needs at runtime.

No key material lives in this repo. The script key is the install's `he.skx`;
the Zook password and registry key are read from the install's own config
scripts after decrypting them with that key.
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric import rsa

from .codec import script
from .config import Config, load_config

SCRIPT_KEY_FILE = "he.skx"
ENGINE_EXE = "Bonsai.exe"
GENOME_CONFIG = "Scripts/GenomeConfiguration.ssx"
BUILDER_BOOT = "Scripts/Boot/Builder.ssx"
ZOOK_PASSWORD_SETTING = "genome_rhesus_macaque"
REGISTRY_KEY_SETTING = "registry_key"
SCRIPT_EXTENSIONS = (".ssx", ".sax")


class InstallNotFound(RuntimeError):
    pass


def find_config_string(source: bytes, name: str) -> bytes | None:
    """Return the string literal passed to Config.Set(name, "...") on an uncommented line."""
    pat = re.compile(rb'Config\.Set\(\s*"' + re.escape(name.encode()) + rb'"\s*,\s*"([^"]*)"')
    for line in source.splitlines():
        if line.lstrip().startswith(b"--"):
            continue
        if m := pat.search(line):
            return m.group(1)
    return None


def _default_install_candidates() -> list[Path]:
    rel = ["Program Files (x86)/BAMZOOKi", "Program Files/BAMZOOKi"]
    if sys.platform == "win32":
        roots = [Path(os.environ.get(v, "")) for v in ("ProgramFiles(x86)", "ProgramFiles")]
        return [r / "BAMZOOKi" for r in roots if str(r)] + [Path("C:/") / r for r in rel]
    prefix = Path(os.environ.get("WINEPREFIX", Path.home() / ".wine"))
    return [prefix / "drive_c" / r for r in rel]


def _default_save_candidates() -> list[Path]:
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        return [Path(appdata) / "Bamzooki"] if appdata else []
    prefix = Path(os.environ.get("WINEPREFIX", Path.home() / ".wine"))
    users = prefix / "drive_c" / "users"
    out = []
    for user in (os.environ.get("USER", ""), "Public"):
        if user:
            out += [users / user / "AppData/Roaming/Bamzooki", users / user / "Application Data/Bamzooki"]
    return out


def is_install(path: Path) -> bool:
    return (path / ENGINE_EXE).is_file() and (path / SCRIPT_KEY_FILE).is_file()


def resolve_path(root: Path, rel: str) -> Path:
    """Join `rel` onto `root`, matching each component case-insensitively if needed (Wine on
    case-sensitive filesystems)."""
    p = root / rel
    if p.exists():
        return p
    cur = root
    for part in Path(rel).parts:
        nxt = cur / part
        if not nxt.exists() and cur.is_dir():
            match = [c for c in cur.iterdir() if c.name.lower() == part.lower()]
            nxt = match[0] if match else nxt
        cur = nxt
    return cur


@dataclass
class Install:
    root: Path
    save_dir: Path | None = None
    config: Config = field(default_factory=Config)

    @classmethod
    def discover(cls, config: Config | None = None) -> "Install":
        config = config or load_config()
        candidates = [config.install_dir] if config.install_dir else _default_install_candidates()
        for c in candidates:
            if c and is_install(c):
                save = config.save_dir or next((s for s in _default_save_candidates() if s.is_dir()), None)
                return cls(root=c, save_dir=save, config=config)
        raise InstallNotFound(
            "BAMZOOKi install not found. Set install_dir in bamzooki.toml or BAMZOOKI_INSTALL. "
            f"Tried: {', '.join(str(c) for c in candidates if c)}"
        )

    def path(self, rel: str) -> Path:
        return resolve_path(self.root, rel)

    @property
    def exe(self) -> Path:
        return self.path(ENGINE_EXE)

    # --- keys -------------------------------------------------------------
    @cached_property
    def script_key(self) -> rsa.RSAPrivateKey:
        return script.load_private_key(self.path(SCRIPT_KEY_FILE).read_bytes())

    def read_script(self, rel: str) -> bytes:
        """Decrypt an install script (.ssx/.sax) to Lua source."""
        return script.decode(self.path(rel).read_bytes(), self.script_key)

    @cached_property
    def zook_key(self) -> bytes:
        key = find_config_string(self.read_script(GENOME_CONFIG), ZOOK_PASSWORD_SETTING)
        if not key:
            raise RuntimeError(f"{ZOOK_PASSWORD_SETTING} not found in {GENOME_CONFIG}")
        return key

    @cached_property
    def registry_key(self) -> bytes | None:
        return find_config_string(self.read_script(BUILDER_BOOT), REGISTRY_KEY_SETTING)

    def secrets(self) -> list[bytes]:
        """All derived secrets (for leak checks). Never print these."""
        return [k for k in (self.zook_key, self.registry_key) if k]

    # --- enumeration -------------------------------------------------------
    def scripts(self) -> list[Path]:
        return sorted(p for p in self.root.rglob("*") if p.suffix.lower() in SCRIPT_EXTENSIONS)

    def example_zooks(self) -> list[Path]:
        return sorted(self.root.rglob("*.zook"))
