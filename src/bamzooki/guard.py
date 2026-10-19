"""Repo hygiene guard: no game files, archives or derived keys may be committed.

Checks every candidate file for
  * a forbidden extension (game scripts, keys, Zooks, meshes, recordings, ...),
  * Bonsai archive content (catches extension-less files such as Components),
  * the install's derived secrets (Zook password, registry key) and he.skx bytes,
    when an install is available. Secrets are never printed.
"""

from __future__ import annotations

import base64
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .codec.archive import MAGIC

FORBIDDEN_EXTENSIONS = frozenset(
    {".ssx", ".sax", ".skx", ".zook", ".dff", ".bmi", ".bmv", ".bvz", ".sdx", ".contest"}
)


@dataclass(frozen=True)
class Violation:
    path: str
    reason: str

    def __str__(self) -> str:
        return f"{self.path}: {self.reason}"


def repo_files(root: Path) -> list[Path]:
    """Files git would commit: tracked plus untracked-but-not-ignored."""
    out = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=root, capture_output=True, check=True,
    ).stdout
    return [root / p for p in out.decode("utf-8").split("\0") if p]


def _needles(secrets: list[bytes], blobs: list[bytes]) -> list[tuple[bytes, str]]:
    needles = []
    for s in secrets:
        needles += [(s, "derived secret"), (s.lower(), "derived secret"), (s.hex().encode(), "derived secret (hex)"),
                    (base64.b64encode(s), "derived secret (base64)")]
    for b in blobs:
        needles += [(b, "script key bytes"), (base64.b64encode(b), "script key (base64)"),
                    (b.hex().encode(), "script key (hex)")]
    return [(n, r) for n, r in needles if len(n) >= 6]


def check_files(paths: list[Path], secrets: list[bytes] = (), key_blobs: list[bytes] = (),
                root: Path | None = None) -> list[Violation]:
    needles = _needles(list(secrets), list(key_blobs))
    violations = []
    for p in paths:
        name = str(p.relative_to(root)) if root and p.is_relative_to(root) else str(p)
        if p.suffix.lower() in FORBIDDEN_EXTENSIONS:
            violations.append(Violation(name, f"forbidden file type {p.suffix}"))
            continue
        if not p.is_file():
            continue
        data = p.read_bytes()
        if data.startswith(MAGIC):
            violations.append(Violation(name, "Bonsai archive content"))
        hay_lower = data.lower()
        for needle, reason in needles:
            if needle in data or needle.lower() in hay_lower:
                violations.append(Violation(name, f"contains {reason}"))
                break
    return violations


def install_secrets() -> tuple[list[bytes], list[bytes]] | None:
    """(secrets, key blobs) from the configured install, or None if there is no install."""
    from .install import SCRIPT_KEY_FILE, Install, InstallNotFound

    try:
        inst = Install.discover()
    except InstallNotFound:
        return None
    return inst.secrets(), [inst.path(SCRIPT_KEY_FILE).read_bytes()]


def main(argv: list[str] | None = None) -> int:
    import argparse

    from .config import REPO_ROOT

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="*", type=Path, help="files to check (default: all files git would commit)")
    ap.add_argument("--require-install", action="store_true", help="fail if secrets cannot be derived")
    args = ap.parse_args(argv)

    files = [f.resolve() for f in args.files] if args.files else repo_files(REPO_ROOT)
    derived = install_secrets()
    if derived is None:
        msg = "asset guard: no BAMZOOKi install configured; secret scan skipped (file-type checks still ran)"
        if args.require_install:
            print(msg.replace("skipped", "REQUIRED"))
            return 2
        print(msg)
        derived = ([], [])

    violations = check_files(files, *derived, root=REPO_ROOT)
    for v in violations:
        print(f"asset guard: {v}")
    if violations:
        print(f"asset guard: {len(violations)} problem(s). Game files and keys must never be committed.")
        return 1
    return 0
