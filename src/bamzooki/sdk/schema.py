"""The model-facing Zook design schema: every part slider, its genome attribute and range.

Ranges and defaults are the Zook Kit's own (its Builder slider definitions). Part specs are
JSON objects grouped like the Zook Kit's panels:

    {
      "name": "Head",                     # optional label
      "mesh": "Blob",                     # only Blob works in contests (see MESHES)
      "size":   {"width": 1, "height": 1, "length": 1},
      "shape":  {"pointiness": 0.5, "flatten_end": 0, "flatten_side": 0, "squareness": 0},
      "attach": {"elevation": 0, "around": 90},              # where it sits on the parent (not root)
      "rotate": {"twist": 0, "up_down": 0, "left_right": 0},  # its own orientation (not root)
      "movement": {"type": "single", "phase": 0, ...},        # how it moves (not root)
      "look":   {"rgb": [1, 1, 1], "brightness": 1, "texture": "aawhite"},
      "mirror": true, "mirror_phase": 0.5,                    # add a mirrored twin (not root)
      "children": [ ...parts attached to this one... ]
    }

The root part also takes "gait": {"cycle_speed", "turn_sharpness", "turn_smoothness",
"targeting_angle"} (the Zook Kit's "Master IK Control").
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Param:
    group: str  # spec group ("size", "shape", ...)
    key: str  # spec key within the group
    attr: str  # genome attribute
    default: float | str
    min: float | None = None
    max: float | None = None
    choices: tuple[str, ...] | None = None  # spec values (for enums)
    attr_values: tuple[str, ...] | None = None  # matching genome values
    where: str = "all"  # "all", "root", "child"
    help: str = ""

    def to_attr(self, value):
        if self.choices:
            return self.attr_values[self.choices.index(value)] if self.attr_values else value
        return float(value)

    def from_attr(self, text: str):
        if self.choices:
            if self.attr_values and text in self.attr_values:
                return self.choices[self.attr_values.index(text)]
            return text
        try:
            return float(text)
        except ValueError:
            return text


# The Zook Kit also offers Cube and Sphere, but Zooks using them fail to load in contests (the
# engine errors in Evo.Initialize); every example Zook is all-Blob. Use Blob + "squareness" for boxes.
MESHES = ("Blob",)
MOVEMENT_TYPES = ("none", "single", "double")

PARAMS: tuple[Param, ...] = (
    # Shape panel
    Param("size", "width", "scalex", 1.0, 0.1, 3, help="part width (x)"),
    Param("size", "height", "scaley", 1.0, 0.1, 3, help="part height (y)"),
    Param("size", "length", "scalez", 1.0, 0.1, 3, help="part length (z, along the part)"),
    Param("shape", "pointiness", "bias", 0.5, 0, 1, help="Blob only: taper towards one end"),
    Param("shape", "flatten_end", "flatness", 0.0, 0, 1, help="Blob only: flatten the far end"),
    Param("shape", "flatten_side", "asymmetry", 0.0, 0, 1, help="Blob only: flatten one side"),
    Param("shape", "squareness", "cubosity", 0.0, 0, 1, help="Blob only: 0 rounded .. 1 box-like"),
    # Position panel
    Param("attach", "elevation", "theta", 0.0, -90, 90, where="child",
          help="degrees up(-)/down(+) on the parent's surface, measured from the parent's front"),
    Param("attach", "around", "phi", 0.0, -180, 180, where="child",
          help="degrees around the parent: 0 = front, +90 / -90 = the two sides, 180 = back"),
    Param("rotate", "twist", "roll", 0.0, -180, 180, where="child", help="degrees of twist about the part's axis"),
    Param("rotate", "up_down", "pitch", 0.0, -180, 180, where="child", help="degrees the part tilts up/down"),
    Param("rotate", "left_right", "yaw", 0.0, -180, 180, where="child", help="degrees the part swings left/right"),
    # Joint panel
    Param("movement", "type", "leg_type", "none", choices=MOVEMENT_TYPES, attr_values=("0", "1", "2"),
          where="child", help="none; single = this part swings through its IK path; "
                              "double = this part and its parent bend as a two-part limb (needs a grandparent)"),
    Param("movement", "side", "ik_side", "Auto", choices=("Auto", "Left side", "Right side", "Always"),
          where="child", help="which turning direction drives this limb"),
    Param("movement", "phase", "leg_phase", 0.0, 0, 1, where="child",
          help="where in the stride cycle this part is (offset limbs by 0.5 to alternate)"),
    Param("movement", "targeting", "spine", "Off", choices=("Normal", "Inverted", "Off"), where="child",
          help="part turns towards the target (Normal), away (Inverted) or not at all"),
    Param("movement", "targeting_angle", "max_spine_angle", 30.0, 0, 90, where="child",
          help="maximum targeting angle in degrees"),
    Param("movement", "target_angle", "min_spine_target_angle", 180.0, 0, 180, where="child",
          help="targeting cone in degrees"),
    Param("movement", "wave", "spine_amplitude", 0.0, 0, 1, where="child",
          help="body-wave amplitude: the joint sways side to side in time with the stride (phase = "
               "movement.phase); chain several waving parts with staggered phases for a slithering worm. "
               "Not in the Zook Kit's UI but supported by the engine (used by Series-1 Zooks)"),
    Param("movement", "stiffness", "muscle_stiffness", 1000.0, 1, 10000, where="child", help="joint muscle stiffness"),
    Param("movement", "damping", "muscle_damping", 1000.0, 1, 10000, where="child", help="joint muscle damping"),
    # Master IK Control (root only)
    Param("gait", "cycle_speed", "zook_speed", 0.5, 0, 1, where="root", help="stride cycle speed"),
    Param("gait", "turn_sharpness", "turn_sharpness", 0.25, 0, 1, where="root", help="how hard it turns"),
    Param("gait", "turn_smoothness", "turn_smoothness", 0.5, 0, 1, where="root", help="how smoothly it turns"),
    Param("gait", "targeting_angle", "max_spine_angle", 45.0, 0, 90, where="root",
          help="maximum angle for parts that target"),
    # Colour panel
    Param("look", "brightness", "brightness", 1.0, 0, 1, help="0 dark .. 1 full colour"),
    # The Zook Kit default "bare" has no texture file in the install, and Zooks using it fail to load
    # in contests; aawhite is the plain white texture.
    Param("look", "texture", "texture", "aawhite", help="texture name from the design guide's list (no extension)"),
)

COLOUR_ATTRS = ("colour_red", "colour_green", "colour_blue")  # look.rgb, each 0..1
TOP_LEVEL = ("name", "mesh", "size", "shape", "attach", "rotate", "movement", "gait", "look",
             "mirror", "mirror_phase", "children", "legacy")
# Attributes the engine recomputes or we manage ourselves.
MANAGED = {"posx", "posy", "posz", "partId", "segment", "mirror_group", "name", "mesh", "colour",
           *COLOUR_ATTRS, "powered", "moveable", "IKspeed", "minAmplitude", "minAngle"}
# Older attributes some Zooks carry; preserved untouched (spec key "legacy").
LEGACY = ("spine_interval", "spine_phase", "side", "damping")
MAX_NAME = 25  # Zook Kit limit for Zook names


def params_for(where: str) -> list[Param]:
    """Params that apply to a root ("root") or attached ("child") part."""
    return [p for p in PARAMS if p.where in ("all", where)]


def param(group: str, key: str, where: str) -> Param | None:
    return next((p for p in params_for(where) if p.group == group and p.key == key), None)


def describe() -> dict:
    """Machine-readable schema for tools/docs."""
    out: dict = {"meshes": list(MESHES), "groups": {}}
    for p in PARAMS:
        entry = {"attr": p.attr, "default": p.default, "applies_to": p.where, "help": p.help}
        if p.choices:
            entry["choices"] = list(p.choices)
        else:
            entry.update({"min": p.min, "max": p.max})
        out["groups"].setdefault(p.group, {})[p.key] = entry
    out["groups"].setdefault("look", {})["rgb"] = {"attr": "colour_red/green/blue", "default": [1, 1, 1],
                                                     "min": 0, "max": 1, "help": "[r, g, b], each 0..1"}
    out["mirror"] = ("true adds a mirrored twin on the other side (around/twist/left_right negated); "
                     "children are mirrored with it. Needs around != 0. mirror_phase sets the twin's "
                     "movement phase (default: same as phase; use phase+0.5 to alternate).")
    return out
