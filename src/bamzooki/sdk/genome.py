"""Zook genome documents: the XML-like text inside a decoded .zook.

    <zook version = "2" >
    \t<passport >
    \t\t<ownership >
    ...
    \t</passport>\x20
    </zook>\x20

Every element is on its own lines: an indented open tag ``<tag k = "v" ... >`` and a matching
close tag ``</tag> `` (note the trailing space). There is no text content. Attributes are kept
in file order (the engine writes them in ASCII order). ``dumps(loads(text)) == text`` for the
game's own files.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterator

_TAG = re.compile(r'<(/?)([A-Za-z_][\w]*)((?:\s+[A-Za-z_][\w]*\s*=\s*"[^"]*")*)(\s*)>')
_ATTR = re.compile(r'([A-Za-z_][\w]*)\s*=\s*"([^"]*)"')


class GenomeError(ValueError):
    pass


@dataclass
class Element:
    tag: str
    attrs: dict[str, str] = field(default_factory=dict)
    children: list["Element"] = field(default_factory=list)
    tight: bool = False  # open tag written as <tag> rather than <tag >
    close_space: bool = True  # close tag followed by a space (always, in engine-written files)

    def find(self, tag: str) -> "Element | None":
        return next((c for c in self.children if c.tag == tag), None)

    def findall(self, tag: str) -> list["Element"]:
        return [c for c in self.children if c.tag == tag]

    def iter(self, tag: str | None = None) -> Iterator["Element"]:
        if tag is None or self.tag == tag:
            yield self
        for c in self.children:
            yield from c.iter(tag)

    def add(self, tag: str, attrs: dict | None = None) -> "Element":
        child = Element(tag, dict(attrs or {}))
        self.children.append(child)
        return child

    def set(self, name: str, value) -> None:
        """Set an attribute (stringified). New attributes are inserted in ASCII order."""
        text = _fmt(value)
        if name in self.attrs:
            self.attrs[name] = text
            return
        items = list(self.attrs.items()) + [(name, text)]
        self.attrs = dict(sorted(items, key=lambda kv: kv[0]))


def _fmt(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return repr(value) if value != int(value) else str(int(value))
    return str(value)


@dataclass
class Document:
    root: Element
    newline: str = "\n"


def loads(text: str) -> Document:
    newline = "\r\n" if "\r\n" in text else "\n"
    stack: list[Element] = []
    root: Element | None = None
    pos = 0
    for m in _TAG.finditer(text):
        between = text[pos:m.start()]
        if between.strip():
            raise GenomeError(f"unexpected text {between.strip()[:40]!r}")
        pos = m.end()
        closing, tag, attrs, space = m.groups()
        if closing:
            if not stack or stack[-1].tag != tag:
                raise GenomeError(f"unbalanced </{tag}>")
            stack.pop().close_space = text[m.end():m.end() + 1] == " "
            if text[m.end():m.end() + 1] == " ":
                pos += 1
            continue
        el = Element(tag, dict(_ATTR.findall(attrs)), tight=(space == ""))
        if stack:
            stack[-1].children.append(el)
        elif root is None:
            root = el
        else:
            raise GenomeError("more than one root element")
        stack.append(el)
    if text[pos:].strip():
        raise GenomeError("trailing text after the root element")
    if root is None or stack:
        raise GenomeError("missing or unclosed root element")
    return Document(root, newline)


def dumps(doc: Document) -> str:
    out: list[str] = []
    nl = doc.newline

    def emit(el: Element, depth: int) -> None:
        ind = "\t" * depth
        attrs = "".join(f' {k} = "{v}"' for k, v in el.attrs.items())
        out.append(f"{ind}<{el.tag}{attrs}{'' if el.tight else ' '}>{nl}")
        for c in el.children:
            emit(c, depth + 1)
        out.append(f"{ind}</{el.tag}>{' ' if el.close_space else ''}{nl}")

    emit(doc.root, 0)
    return "".join(out)
