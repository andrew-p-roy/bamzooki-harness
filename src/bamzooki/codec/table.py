"""Bonsai text tables: the format of System.ReadTable / System.WriteTable.

Used by .contest files, ContestPacks/*.dat, zook_list.tab, Video.cfg and by
the harness's job/result files::

    "name" = "Zook Sumo"
    "position" = (-4.2, 2, -0.052)          Vector  -> Vec
    "rotation" = <0, 1, 0, -1.62921e-007>   Quatn   -> Quat
    "movable" = false
    "rounds" =
    {
        1 =
        {
            "contest" = "CP_ZOOK_SUMO"
        }
    }

Keys are quoted strings or bare numbers. Strings are raw (no escapes): Windows
paths are written with literal backslashes.
"""

from __future__ import annotations

import re
from typing import Any, NamedTuple


class Vec(NamedTuple):
    x: float
    y: float
    z: float


class Quat(NamedTuple):
    x: float
    y: float
    z: float
    w: float


class TableError(ValueError):
    pass


_TOKEN = re.compile(
    r"""\s*(?:
        (?P<str>"[^"\n]*")
      | (?P<num>[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)
      | (?P<vec>\([^)]*\))
      | (?P<quat><[^>]*>)
      | (?P<word>[A-Za-z_]\w*)
      | (?P<sym>[={}])
    )""",
    re.X,
)


def _tokens(text: str):
    pos, n = 0, len(text)
    while True:
        while pos < n and text[pos].isspace():
            pos += 1
        if pos >= n:
            return
        m = _TOKEN.match(text, pos)
        if not m or m.end() == pos:
            raise TableError(f"unexpected input at offset {pos}: {text[pos:pos + 30]!r}")
        pos = m.end()
        kind = m.lastgroup
        yield kind, m.group(kind)


def _number(s: str) -> int | float:
    f = float(s)
    return int(f) if re.fullmatch(r"[-+]?\d+", s) else f


def _floats(s: str) -> list[float]:
    return [float(p) for p in s[1:-1].split(",")]


def _value(kind: str, tok: str) -> Any:
    if kind == "str":
        return tok[1:-1]
    if kind == "num":
        return _number(tok)
    if kind == "vec":
        return Vec(*_floats(tok))
    if kind == "quat":
        return Quat(*_floats(tok))
    if kind == "word" and tok in ("false", "nil"):
        return False if tok == "false" else None
    raise TableError(f"unexpected value {tok!r}")


def loads(text: str) -> dict:
    toks = list(_tokens(text))
    pos = 0

    def block(end: str | None) -> dict:
        nonlocal pos
        out: dict = {}
        while pos < len(toks):
            kind, tok = toks[pos]
            if kind == "sym" and tok == "}":
                if end != "}":
                    raise TableError("unbalanced '}'")
                pos += 1
                return out
            if kind == "str":
                key: Any = tok[1:-1]
            elif kind == "num":
                key = _number(tok)
            else:
                raise TableError(f"expected key, got {tok!r}")
            if pos + 2 >= len(toks) + 1 or toks[pos + 1] != ("sym", "="):
                raise TableError(f"expected '=' after key {key!r}")
            pos += 2
            if pos >= len(toks):
                raise TableError(f"missing value for {key!r}")
            kind, tok = toks[pos]
            if kind == "sym" and tok == "{":
                pos += 1
                out[key] = block("}")
            else:
                out[key] = _value(kind, tok)
                pos += 1
        if end is not None:
            raise TableError("missing '}'")
        return out

    return block(None)


def _fmt_num(v: float) -> str:
    return repr(v) if isinstance(v, float) else str(v)


def _fmt_value(v: Any, indent: int) -> str:
    if isinstance(v, bool):
        return "1" if v else "false"
    if isinstance(v, (int, float)):
        return _fmt_num(v)
    if isinstance(v, str):
        if '"' in v or "\n" in v:
            raise TableError(f"string not representable in a Bonsai table: {v!r}")
        return f'"{v}"'
    if isinstance(v, Quat):
        return "<" + ", ".join(_fmt_num(c) for c in v) + ">"
    if isinstance(v, (Vec, tuple)) and len(v) == 3:
        return "(" + ", ".join(_fmt_num(c) for c in v) + ")"
    if isinstance(v, dict):
        pad = "\t" * indent
        return f"\n{pad}{{\n{_fmt_block(v, indent + 1)}{pad}}}"
    raise TableError(f"unsupported value type {type(v).__name__}")


def _fmt_block(d: dict, indent: int) -> str:
    pad = "\t" * indent
    lines = []
    for k, v in d.items():
        key = str(k) if isinstance(k, int) else f'"{k}"'
        if v is None:
            continue
        lines.append(f"{pad}{key} = {_fmt_value(v, indent)}\n")
    return "".join(lines)


def dumps(d: dict) -> str:
    return _fmt_block(d, 0)
