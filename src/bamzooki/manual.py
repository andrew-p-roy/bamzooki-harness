"""The game's own manual (the user's copy, "Model Instructions/BAMZOOKi Manual.pdf"), as text.

The PDF is BBC material, so it is never committed: it is gitignored and refused by the asset
guard. Its text layer has two quirks we clean up: headings are drawn three times
("HHH ooo www" -> "How") and some paragraphs are emitted twice.
"""

from __future__ import annotations

import logging
import re
from functools import lru_cache
from pathlib import Path

from .config import REPO_ROOT

MODEL_INSTRUCTIONS = REPO_ROOT / "Model Instructions"
MANUAL_PATH = MODEL_INSTRUCTIONS / "BAMZOOKi Manual.pdf"

NOTE = ("The manual describes the Zook Kit's own editor (buttons, dragging IK points, menus). In this "
        "harness you edit the same settings through JSON specs: see get_design_guide for how each "
        "Zook Kit option maps to spec fields.")


def _untriple(line: str) -> str:
    words = line.split()
    if words and all(len(w) % 3 == 0 and all(w[i] == w[i + 1] == w[i + 2] for i in range(0, len(w), 3))
                     for w in words):
        return " ".join(w[::3] for w in words)
    return line


def clean(text: str) -> str:
    lines = [_untriple(l.rstrip()) for l in text.splitlines()]
    paras, cur = [], []
    for line in lines:
        if line.strip():
            cur.append(line.strip())
        elif cur:
            paras.append(" ".join(cur))
            cur = []
    if cur:
        paras.append(" ".join(cur))
    out = []
    for p in paras:
        norm = re.sub(r"\s+", " ", p)
        if out and re.sub(r"\s+", " ", out[-1]) == norm:
            continue  # the text layer repeats some paragraphs
        out.append(p)
    return "\n\n".join(out)


@lru_cache(maxsize=1)
def pages(path: str = str(MANUAL_PATH)) -> tuple[str, ...]:
    from pypdf import PdfReader

    logging.getLogger("pypdf").setLevel(logging.ERROR)
    return tuple(clean(p.extract_text() or "") for p in PdfReader(path).pages)


def available() -> bool:
    return MANUAL_PATH.is_file()


def _parse_range(spec: str, n: int) -> list[int]:
    out: list[int] = []
    for part in spec.replace(" ", "").split(","):
        if not part:
            continue
        a, _, b = part.partition("-")
        lo, hi = int(a), int(b or a)
        out += [p for p in range(lo, hi + 1) if 1 <= p <= n]
    return out


def read(page_spec: str = "", search: str = "", max_chars: int = 30000) -> dict:
    """Contents (no arguments), pages ("17-22", "3,25"), or pages matching a search term."""
    if not available():
        return {"error": "The game manual isn't available (the operator can put it in 'Model Instructions')."}
    ps = pages()
    if search:
        hits = [i + 1 for i, t in enumerate(ps) if search.lower() in t.lower()]
        return {"search": search, "pages": hits,
                "snippets": {p: _snippet(ps[p - 1], search) for p in hits[:15]}, "note": NOTE}
    if not page_spec:
        return {"pages_total": len(ps), "contents (page 3)": ps[2] if len(ps) > 2 else "", "note": NOTE,
                "how": "read_manual(pages='17-26') for movement; pages='28-29' for test environments and trials"}
    sel = _parse_range(page_spec, len(ps))
    text, used = "", []
    for p in sel:
        chunk = f"--- page {p} ---\n{ps[p - 1]}\n\n"
        if len(text) + len(chunk) > max_chars:
            break
        text += chunk
        used.append(p)
    return {"pages": used, "text": text, "note": NOTE,
            **({"truncated": f"stopped before page {sel[len(used)]}"} if len(used) < len(sel) else {})}


def _snippet(text: str, term: str, width: int = 160) -> str:
    i = text.lower().find(term.lower())
    return ("…" if i > width else "") + text[max(0, i - width): i + len(term) + width].replace("\n", " ") + "…"
