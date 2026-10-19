"""Zook file naming: <major>.<minor>.zook (the harness writes <n>.0.zook)."""

from __future__ import annotations

import re
from pathlib import Path

VERSION_RE = re.compile(r"^(\d+)\.(\d+)$")


def version_of(path: Path) -> int | None:
    m = VERSION_RE.match(Path(path).stem)
    return int(m.group(1)) if m and m.group(2) == "0" else None
