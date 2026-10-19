"""The zoo: where models' Zook designs live, versioned per model.

    <zoo_dir>/<model>/<Name>/<n>.0.zook        finalised Zook (playable in the game and the harness)
    <zoo_dir>/<model>/<Name>/<n>.0.json        spec + finalise report (stats, clamps, previews, notes)
    <zoo_dir>/<model>/<Name>/<n>.0_<view>.png  previews

References used by tools: "Ant" / "examples/Ant" (the install's example Zooks), "<Name>" (the
current model's latest version), "<model>/<Name>" and "<model>/<Name>@<n>" (a given version).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from ..install import Install
from .schema import MAX_NAME
from .store_paths import VERSION_RE, version_of  # noqa: F401  (re-exported)


@dataclass
class ZookRef:
    owner: str  # model name, or "examples"
    name: str
    version: int | None  # None = example Zook / latest
    path: Path  # the .zook file
    info: Path | None = None  # the .json beside it (zoo only)

    @property
    def label(self) -> str:
        if self.owner == "examples":
            return f"examples/{self.name}"
        return f"{self.owner}/{self.name}@{self.version}"


_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def entrant_folder(model: str) -> str:
    """Folder name for an entrant: the full model name (e.g. "anthropic/claude-sonnet-4.5" ->
    "anthropic_claude-sonnet-4.5"); only characters illegal in file names are replaced."""
    name = _ILLEGAL.sub("_", model.strip()).strip(" .")
    if not name or len(name) > 120 or name in ("examples",):
        raise ValueError(f"model name {model!r}: 1-120 characters, not 'examples'")
    return name


def safe_name(name: str, what: str = "name") -> str:
    name = name.strip()
    if not name or len(name) > MAX_NAME or not re.fullmatch(r"[A-Za-z0-9 _\-.]+", name) or name.startswith("."):
        raise ValueError(f"{what} {name!r}: 1-{MAX_NAME} letters, digits, spaces, '_', '-', '.'")
    return name


class Zoo:
    def __init__(self, install: Install, examples_root: Path | None = None):
        self.install = install
        self.root = install.config.zoo_dir
        # Example Zooks: read from the sandbox copy when present (same files as the install).
        self.examples = examples_root or install.path("Teams/examples/Creatures")

    # --- listing ---
    def example_names(self) -> list[str]:
        return sorted(p.name for p in self.examples.iterdir() if p.is_dir() and any(p.glob("*.zook")))

    def models(self) -> list[str]:
        return sorted(p.name for p in self.root.iterdir() if p.is_dir()) if self.root.is_dir() else []

    def designs(self, model: str) -> dict[str, list[int]]:
        base = self.root / model
        if not base.is_dir():
            return {}
        out = {}
        for d in sorted(p for p in base.iterdir() if p.is_dir()):
            vs = sorted(v for f in d.glob("*.zook") if (v := version_of(f)) is not None)
            if vs:
                out[d.name] = vs
        return out

    # --- refs ---
    def resolve(self, ref: str, model: str | None = None) -> ZookRef:
        ref = ref.strip()
        version = None
        if "@" in ref:
            ref, v = ref.rsplit("@", 1)
            version = int(v)
        if "/" in ref:
            owner, name = ref.split("/", 1)
        else:
            owner, name = (model, ref) if model and (self.root / model / ref).is_dir() else ("examples", ref)
        if owner == "examples":
            d = self.examples / name
            files = sorted(d.glob("*.zook"), key=lambda p: [int(x) for x in re.findall(r"\d+", p.stem)]) if d.is_dir() else []
            if not files:
                raise KeyError(f"no example Zook {name!r}; examples: {', '.join(self.example_names())}")
            return ZookRef("examples", name, None, files[-1])
        versions = self.designs(owner).get(name)
        if not versions:
            raise KeyError(f"no Zook {owner}/{name}")
        v = version or versions[-1]
        if v not in versions:
            raise KeyError(f"{owner}/{name} has versions {versions}")
        d = self.root / owner / name
        return ZookRef(owner, name, v, d / f"{v}.0.zook", d / f"{v}.0.json")

    def new_version(self, model: str, name: str) -> tuple[int, Path]:
        entrant_folder(model)
        safe_name(name)
        d = self.root / model / name
        d.mkdir(parents=True, exist_ok=True)
        v = max(self.designs(model).get(name, [0])) + 1
        return v, d / f"{v}.0.zook"

    def read_info(self, ref: ZookRef) -> dict:
        if ref.info and ref.info.exists():
            return json.loads(ref.info.read_text(encoding="utf-8"))
        return {}

    def write_info(self, zook_path: Path, info: dict) -> Path:
        p = zook_path.with_suffix(".json")
        p.write_text(json.dumps(info, indent=2, default=str), encoding="utf-8")
        return p
