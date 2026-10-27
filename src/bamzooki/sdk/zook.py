"""Zooks as model-friendly JSON specs (see schema.py) and back to genome documents.

    z = Zook.load("Ant/2.1.zook", key)        # from a .zook file
    spec = z.to_spec()                          # JSON-able dict, mirror twins folded
    problems = validate_spec(spec)              # [] when valid
    z2 = Zook.from_spec(spec, owner="claude")   # new genome document
    z2.save("MyZook/1.0.zook", key)

A spec built here is a *draft*: part positions on the parent's surface (posx/posy/posz),
passport stats and default IK paths are computed by the game's own Builder code when the
draft is finalised in the engine (bamzooki.runner.Engine.finalize_zook).
"""

from __future__ import annotations

import copy
import random
import re
import time
from dataclasses import dataclass
from pathlib import Path

from ..codec import archive
from . import genome
from .genome import Document, Element
from .schema import (COLOUR_ATTRS, LEGACY, MAX_NAME, MESHES, MOVEMENT_TYPES, TOP_LEVEL, Param, param,
                     params_for)

IK_TAGS = ("ik1_positions", "ik2_positions", "ik_positions")
GROUPS_ROOT = ("Shape", "Master IK Control", "Colour")
GROUPS_CHILD = ("Shape", "Position", "Joint", "Colour")


class SpecError(ValueError):
    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


# --- genome -> spec ---------------------------------------------------------------------

def _num(text: str | None, default=0.0) -> float:
    try:
        return float(text)
    except (TypeError, ValueError):
        return default


def _r(v):
    """Round floats for readable specs (4 decimals is far below what the sliders resolve)."""
    return round(v, 4) if isinstance(v, float) else v


# Older Zooks (Spider, Twigger, Wormthing...) store the engine's raw gait values instead of the
# Master IK Control sliders; Evo uses a raw value only when its slider is missing (Evo.sax.lua).
RAW_GAIT = {"zook_speed": ("IKspeed", lambda v: v / 0.03),
            "turn_sharpness": ("minAmplitude", lambda v: (1 - v) / 2),
            "turn_smoothness": ("minAngle", lambda v: v / 180)}


def _effective_attrs(el: Element, where: str) -> dict[str, str]:
    attrs = dict(el.attrs)
    if where == "root":
        for slider, (raw, to_slider) in RAW_GAIT.items():
            if slider not in attrs and raw in attrs:
                attrs[slider] = genome._fmt(min(1.0, max(0.0, to_slider(_num(attrs[raw])))))
    return attrs


def _part_to_spec(el: Element, where: str, compact: bool, specs: dict | None = None) -> dict:
    """specs collects id(element) -> spec for every part folded into this one (for twin overrides)."""
    specs = {} if specs is None else specs
    spec: dict = {}
    specs[id(el)] = spec
    if el.attrs.get("name"):
        spec["name"] = el.attrs["name"]
    spec["mesh"] = el.attrs.get("mesh", "Blob")
    attrs = _effective_attrs(el, where)
    for p in params_for(where):
        if p.attr not in attrs:
            continue
        value = _r(p.from_attr(attrs[p.attr]))
        if p.attr == "phi" and 0 < value <= 0.001:
            value = 0.0  # the builder's stand-in for 0 (see _Builder.part)
        if compact and value == p.default:
            continue
        spec.setdefault(p.group, {})[p.key] = value
    rgb = [_r(_num(el.attrs.get(a), 1.0)) for a in COLOUR_ATTRS]
    if not compact or rgb != [1.0, 1.0, 1.0]:
        spec.setdefault("look", {})["rgb"] = rgb
    ik = next((c for c in el.children if c.tag in IK_TAGS), None)
    if ik is not None and ik.children:
        spec.setdefault("movement", {})["ik_points"] = [
            [_r(_num(pt.attrs.get(a))) for a in ("x", "y", "z")] for pt in ik.findall("point")]
    legacy = {k: el.attrs[k] for k in LEGACY if k in el.attrs}
    if legacy:
        spec["legacy"] = legacy
    kids = [c for c in el.children if c.tag == "bodysolid"]
    children = []
    used = set()
    for i, k in enumerate(kids):
        if i in used:
            continue
        twin = _find_twin(kids, i, used)
        cs = _part_to_spec(k, "child", compact, specs)
        if twin is not None:
            used.add(twin)
            cs["mirror"] = True
            _twin_overrides(k, kids[twin], specs)
        children.append(cs)
    if children:
        spec["children"] = children
    return spec


def _twin_overrides(original: Element, twin: Element, specs: dict) -> None:
    """Movement Cycle and Movement Type are per part in the Zook Kit, not shared with the mirror:
    record where each twin part differs as mirror_phase / mirror_side on the original's spec."""
    for o, t in zip(original.iter("bodysolid"), twin.iter("bodysolid")):
        s = specs.get(id(o))
        if s is None:  # a nested twin, folded into its own original
            continue
        tp = _r(_num(t.attrs.get("leg_phase")))
        if tp != _r(_num(o.attrs.get("leg_phase"))):
            s["mirror_phase"] = tp
        side = t.attrs.get("ik_side", "Auto")
        if side != _mirror_value("ik_side", o.attrs.get("ik_side", "Auto")):
            s["mirror_side"] = side


def _shape_signature(el: Element) -> tuple:
    return tuple(_shape_signature(c) for c in el.children if c.tag == "bodysolid")


def _find_twin(kids: list[Element], i: int, used: set) -> int | None:
    """A sibling in the same mirror group on the other side with the same sub-structure."""
    a = kids[i]
    group = a.attrs.get("mirror_group")
    for j in range(i + 1, len(kids)):
        b = kids[j]
        if j in used or b.attrs.get("mirror_group") != group or group is None:
            continue
        if _num(a.attrs.get("phi")) * _num(b.attrs.get("phi")) < 0 and _shape_signature(a) == _shape_signature(b):
            return j
    return None


# --- spec -> genome ---------------------------------------------------------------------

def _mirror_value(attr: str, value: str) -> str:
    if attr in ("phi", "yaw", "roll"):
        v = -_num(value)
        return genome._fmt(v)
    if attr == "ik_side":
        return {"Left side": "Right side", "Right side": "Left side"}.get(value, value)
    return value


class _Builder:
    def __init__(self):
        self.next_group = 1

    def part(self, spec: dict, where: str, mirror_of: Element | None = None) -> Element:
        el = Element("bodysolid")
        el.set("name", spec.get("name", ""))
        el.set("mesh", spec.get("mesh", "Blob"))
        for p in params_for(where):
            value = spec.get(p.group, {}).get(p.key, p.default)
            el.set(p.attr, p.to_attr(value) if not isinstance(value, str) or p.choices else value)
        for attr, v in zip(COLOUR_ATTRS, spec.get("look", {}).get("rgb", [1, 1, 1])):
            el.set(attr, float(v))
        el.set("colour", 0)
        for k, v in spec.get("legacy", {}).items():
            el.set(k, v)
        if where == "child":
            # The Zook Kit never stores phi = 0 ("it will screw up mirroring stuff"): a twin's
            # single-part path is mirrored only when its phi is negative.
            if abs(_num(el.attrs["phi"])) < 0.001:
                el.set("phi", 0.001)
            el.set("powered", "true")
            el.add("cardanconnector")
        else:
            el.set("moveable", "false")
        groups = el.add("attribute_groups")
        for g in (GROUPS_CHILD if where == "child" else GROUPS_ROOT):
            groups.add("attribute_group", {"name": g})
        mv = spec.get("movement", {})
        if where == "child" and mv.get("type", "none") in ("single", "double") and mv.get("ik_points"):
            ik = el.add("ik1_positions" if mv["type"] == "single" else "ik2_positions", {"gait": "gait_1"})
            for x, y, z in mv["ik_points"]:
                pt = ik.add("point")
                pt.set("x", float(x)), pt.set("y", float(y)), pt.set("z", float(z))
        el.set("mirror_group", self.next_group)
        self.next_group += 1
        for c in spec.get("children", []):
            child = self.part(c, "child")
            el.children.append(child)
            if c.get("mirror"):
                el.children.append(self.twin(child, c))
        return el

    def twin(self, original: Element, spec: dict) -> Element:
        """Mirror copy: same mirror groups, around/twist/left_right negated, and each part's own
        mirror_phase / mirror_side (anywhere in the subtree) as the twin part's phase / side."""
        tw = copy.deepcopy(original)
        for node in tw.iter("bodysolid"):
            for attr in ("phi", "yaw", "roll", "ik_side"):
                if attr in node.attrs:
                    node.attrs[attr] = _mirror_value(attr, node.attrs[attr])
        _apply_twin_overrides(spec, tw)
        return tw


def _apply_twin_overrides(spec: dict, el: Element) -> None:
    if "mirror_phase" in spec:
        el.set("leg_phase", float(spec["mirror_phase"]))
    if "mirror_side" in spec:
        el.set("ik_side", spec["mirror_side"])
    kids = [c for c in el.children if c.tag == "bodysolid"]
    i = 0
    for c in spec.get("children", []):
        _apply_twin_overrides(c, kids[i])
        i += 2 if c.get("mirror") else 1  # skip a nested twin: it keeps its own values


def _uid(n: int = 8) -> str:
    return "".join(random.choice("0123456789ABCDEF") for _ in range(n))


@dataclass
class Zook:
    doc: Document
    header: bytes = archive.ZOOK_HEADER

    # --- io ---
    @classmethod
    def load(cls, path: str | Path, key: bytes) -> "Zook":
        header, plain = archive.decode(Path(path).read_bytes(), key)
        return cls(genome.loads(plain.decode("latin-1")), header)

    def save(self, path: str | Path, key: bytes) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(archive.encode(self.header, genome.dumps(self.doc).encode("latin-1"), key))
        return path

    # --- accessors ---
    @property
    def root_part(self) -> Element:
        g = self.doc.root.find("genome")
        root = g.find("bodysolid") if g is not None else None
        if root is None:
            raise genome.GenomeError("no genome/bodysolid")
        return root

    @property
    def name(self) -> str:
        owners = list(self.doc.root.iter("owner"))
        return owners[-1].attrs.get("zookname", "") if owners else ""

    def details(self) -> dict[str, str]:
        """Passport stats (Height, Length, Width, Weight, Components, trial results...)."""
        return {d.attrs.get("name", ""): f"{d.attrs.get('data', '').strip()} {d.attrs.get('comment', '')}".strip()
                for d in self.doc.root.iter("detail")}

    def part_count(self) -> int:
        return sum(1 for _ in self.root_part.iter("bodysolid"))

    # --- spec ---
    def to_spec(self, compact: bool = True) -> dict:
        spec = _part_to_spec(self.root_part, "root", compact)
        return {"name": self.name, "body": spec}

    @classmethod
    def from_spec(cls, spec: dict, owner: str = "bamzooki-harness", notes: str = "") -> "Zook":
        problems = validate_spec(spec)
        if problems:
            raise SpecError(problems)
        root = Element("zook", {"version": "2"})
        passport = root.add("passport")
        ownership = passport.add("ownership")
        o = ownership.add("owner")
        for k, v in sorted({"adoption_date": time.strftime("%a %b %d %H:%M:%S %Y (GMT)", time.gmtime()),
                            "moniker": _uid(16), "notes": notes, "uid": _uid(), "username": owner,
                            "zookname": spec["name"]}.items()):
            o.set(k, v)
        passport.add("details")
        root.add("parameters")
        g = root.add("genome")
        g.children.append(_Builder().part(spec["body"], "root"))
        return cls(Document(root))


# --- validation -------------------------------------------------------------------------

def validate_spec(spec: dict) -> list[str]:
    """Problems with a design (empty list = OK). Mirrors the Zook Kit's own rules."""
    problems: list[str] = []
    name = spec.get("name")
    if not isinstance(name, str) or not name.strip():
        problems.append("name: required")
    elif len(name) > MAX_NAME:
        problems.append(f"name: at most {MAX_NAME} characters")
    elif any(c in name for c in '\\/:*?"<>|'):
        problems.append("name: must be usable as a folder name")
    body = spec.get("body")
    if not isinstance(body, dict):
        return problems + ["body: required (the root part)"]
    _validate_part(body, "body", "root", None, None, problems, False)
    return problems


def spec_warnings(spec: dict) -> list[str]:
    """Valid but ineffective settings: a phase on a part whose phase nothing reads."""
    warnings: list[str] = []

    def walk(p, path):
        if not isinstance(p, dict):
            return
        mv = p.get("movement") if isinstance(p.get("movement"), dict) else {}
        if "mirror_phase" in p and mv.get("type", "none") == "none" and not mv.get("wave"):
            warnings.append(f"{path}.mirror_phase: no effect, this part doesn't move (put it on the moving "
                            "part, e.g. the lower part of a two-part leg)")
        for i, c in enumerate(p.get("children", []) if isinstance(p.get("children"), list) else []):
            walk(c, f"{path}.children[{i}]")

    walk(spec.get("body"), "body")
    return warnings


def _validate_part(p: dict, path: str, where: str, parent: dict | None, grand: dict | None,
                   problems: list[str], in_mirror: bool) -> None:
    for k in p:
        if k not in TOP_LEVEL:
            problems.append(f"{path}.{k}: unknown key")
    if p.get("mesh", "Blob") not in MESHES:
        problems.append(f"{path}.mesh: one of {', '.join(MESHES)}")
    for group in ("size", "shape", "attach", "rotate", "movement", "gait", "look"):
        values = p.get(group)
        if values is None:
            continue
        if not isinstance(values, dict):
            problems.append(f"{path}.{group}: must be an object")
            continue
        for key, v in values.items():
            if group == "look" and key == "rgb":
                if not (isinstance(v, list) and len(v) == 3 and all(isinstance(c, (int, float)) and 0 <= c <= 1 for c in v)):
                    problems.append(f"{path}.look.rgb: [r, g, b] with each 0..1")
                continue
            if group == "movement" and key == "ik_points":
                if not (isinstance(v, list) and all(isinstance(q, list) and len(q) == 3 for q in v)):
                    problems.append(f"{path}.movement.ik_points: list of [x, y, z]")
                continue
            prm: Param | None = param(group, key, where)
            if prm is None:
                problems.append(f"{path}.{group}.{key}: not valid on a {'root' if where == 'root' else 'child'} part")
                continue
            if prm.choices:
                if v not in prm.choices:
                    problems.append(f"{path}.{group}.{key}: one of {', '.join(prm.choices)}")
            elif prm.min is not None:
                if not isinstance(v, (int, float)) or isinstance(v, bool):
                    problems.append(f"{path}.{group}.{key}: must be a number")
                elif not prm.min <= v <= prm.max:
                    problems.append(f"{path}.{group}.{key}: {v} outside {prm.min}..{prm.max}")
    if where == "root":
        for bad in ("mirror", "mirror_phase", "mirror_side", "attach", "rotate", "movement"):
            if bad in p:
                problems.append(f"{path}.{bad}: not allowed on the root part")
    mv = p.get("movement", {}) if isinstance(p.get("movement"), dict) else {}
    kind = mv.get("type", "none")
    if p.get("mirror"):
        if abs(p.get("attach", {}).get("around", 0)) < 0.001:
            problems.append(f"{path}.mirror: needs attach.around != 0 (a part on the centre line has no other side)")
    in_mirror = in_mirror or bool(p.get("mirror"))
    for key in ("mirror_phase", "mirror_side"):
        if key in p and where == "child" and not in_mirror:
            problems.append(f"{path}.{key}: only for a mirrored part or a part inside one")
    if "mirror_phase" in p and not (isinstance(p["mirror_phase"], (int, float)) and 0 <= p["mirror_phase"] <= 1):
        problems.append(f"{path}.mirror_phase: 0..1")
    sides = param("movement", "side", "child").choices
    if "mirror_side" in p and p["mirror_side"] not in sides:
        problems.append(f"{path}.mirror_side: one of {', '.join(sides)}")
    children = p.get("children", [])
    if not isinstance(children, list):
        problems.append(f"{path}.children: must be a list")
        children = []
    if kind == "single" and any(c.get("movement", {}).get("type") == "double" for c in children):
        problems.append(f"{path}.movement: single movement can't have a double-movement child")
    if kind == "double":
        # Zook Kit rule (CanBeIK2): the parent must itself be attached (not the root) and not move.
        pk = (parent or {}).get("movement", {}).get("type", "none")
        if grand is None:
            problems.append(f"{path}.movement: double needs a parent that is not the root (two-part limb)")
        elif pk in ("single", "double"):
            problems.append(f"{path}.movement: double needs a non-moving parent")
        if any(c.get("movement", {}).get("type") == "double" for c in children):
            problems.append(f"{path}.movement: double can't have a double-movement child")
        siblings = (parent or {}).get("children", [])
        if sum(1 for s in siblings if s.get("movement", {}).get("type") == "double") > 1:
            problems.append(f"{path}.movement: only one double-movement part per parent")
    for i, c in enumerate(children):
        if not isinstance(c, dict):
            problems.append(f"{path}.children[{i}]: must be an object")
            continue
        _validate_part(c, f"{path}.children[{i}]", "child", p, parent, problems, in_mirror)


# --- edits ------------------------------------------------------------------------------

_STEP = re.compile(r"([A-Za-z_]\w*)|\[(\d+)\]")


def _path(path: str) -> list:
    steps, pos = [], 0
    for part in path.split("."):
        for m in _STEP.finditer(part):
            steps.append(int(m.group(2)) if m.group(2) is not None else m.group(1))
    if not steps:
        raise SpecError([f"bad path {path!r}"])
    return steps


def apply_edits(spec: dict, edits: list[dict]) -> dict:
    """Return a copy of spec with edits applied. Each edit is one of
    {"op": "set", "path": "body.children[0].size.width", "value": 1.2}
    {"op": "add", "path": "body.children", "value": {...part...}}       (append; or "...children[1]" to insert)
    {"op": "remove", "path": "body.children[2]"}                           (or a key, e.g. "body.look.texture")
    Paths use dots for keys and [i] for list items, starting at "name" or "body"."""
    out = copy.deepcopy(spec)
    for n, e in enumerate(edits):
        op, steps = e.get("op"), _path(str(e.get("path", "")))
        try:
            parent = out
            for st in steps[:-1]:
                if isinstance(st, int):
                    parent = parent[st]
                else:
                    parent = parent.setdefault(st, {}) if op == "set" else parent[st]
            last = steps[-1]
            if op == "set":
                parent[last] = e["value"]
            elif op == "remove":
                del parent[last]
            elif op == "add":
                if isinstance(last, int):
                    parent.insert(last, e["value"])
                else:
                    parent.setdefault(last, []).append(e["value"])
            else:
                raise SpecError([f"edit {n}: op must be set, add or remove"])
        except (KeyError, IndexError, TypeError) as exc:
            raise SpecError([f"edit {n} ({e.get('path')}): {exc!r}"]) from None
    return out
