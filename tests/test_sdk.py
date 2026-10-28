"""Zook SDK: genome documents, specs, validation, mirroring, edits (synthetic + install-backed)."""

import pytest

from bamzooki.sdk import genome, schema
from bamzooki.sdk.zook import SpecError, Zook, apply_edits, validate_spec

DOC = ('<zook version = "2" >\n\t<genome >\n\t\t<bodysolid b = "2" a = "1" >\n\t\t</bodysolid> \n'
       '\t</genome> \n</zook> \n')


def leg(around=90, **extra):
    p = {"attach": {"around": around}, "size": {"length": 1.0}}
    p.update(extra)
    return p


def spec(*children, **body):
    return {"name": "Testy", "body": {"mesh": "Blob", **body, "children": list(children)}}


# --- genome ---

def test_genome_roundtrip_and_attr_order():
    d = genome.loads(DOC)
    assert genome.dumps(d) == DOC
    b = d.root.find("genome").find("bodysolid")
    assert list(b.attrs) == ["b", "a"]  # file order kept
    b.set("A", 3)  # new attributes are inserted in ASCII order
    assert list(b.attrs) == ["A", "a", "b"]


@pytest.mark.parametrize("bad", ['<zook >', '<a ></b> ', '<a ></a> <b ></b> ', '<a >text</a> '])
def test_genome_errors(bad):
    with pytest.raises(genome.GenomeError):
        genome.loads(bad)


# --- validation ---

def test_valid_minimal():
    assert validate_spec(spec(leg())) == []


@pytest.mark.parametrize("s, msg", [
    ({"body": {}}, "name: required"),
    ({"name": "x" * 26, "body": {}}, "at most 25"),
    (spec(size={"width": 5}), "outside 0.1..3"),
    (spec(mesh="Sphere"), "mesh: one of Blob"),
    (spec(attach={"around": 10}), "not valid on a root"),
    (spec(leg(around=0, mirror=True)), "needs attach.around != 0"),
    (spec(leg(movement={"type": "double"})), "double needs a parent that is not the root"),
    (spec(leg(movement={"type": "fly"})), "one of none, single, double"),
    (spec(leg(foo=1)), "unknown key"),
    (spec(look={"rgb": [2, 0, 0]}), "look.rgb"),
])
def test_validation_problems(s, msg):
    problems = validate_spec(s)
    assert any(msg in p for p in problems), problems


def test_double_movement_rules():
    ok = spec(leg(children=[leg(movement={"type": "double"})]))
    assert validate_spec(ok) == []
    moving_parent = spec(leg(movement={"type": "single"}, children=[leg(movement={"type": "double"})]))
    assert any("non-moving parent" in p for p in validate_spec(moving_parent))


# --- spec -> genome ---

def test_from_spec_mirrors_and_phases():
    s = spec(leg(around=60, rotate={"left_right": 20, "twist": 5}, movement={"type": "single", "phase": 0.0},
                 mirror=True, mirror_phase=0.5, children=[leg(around=30)]))
    z = Zook.from_spec(s, owner="test")
    root = z.root_part
    a, b = root.findall("bodysolid")
    assert a.attrs["mirror_group"] == b.attrs["mirror_group"]
    assert (a.attrs["phi"], b.attrs["phi"]) == ("60", "-60")
    assert (a.attrs["yaw"], b.attrs["yaw"]) == ("20", "-20")
    assert (a.attrs["leg_phase"], b.attrs["leg_phase"]) == ("0", "0.5")
    assert a.attrs["leg_type"] == "1"
    # the twin's child is mirrored too and shares the original child's mirror group
    (ac,), (bc,) = a.findall("bodysolid"), b.findall("bodysolid")
    assert (ac.attrs["phi"], bc.attrs["phi"]) == ("30", "-30")
    assert ac.attrs["mirror_group"] == bc.attrs["mirror_group"]
    # and folds back into one mirrored part
    back = z.to_spec()["body"]["children"]
    assert len(back) == 1 and back[0]["mirror"] is True and back[0]["mirror_phase"] == 0.5
    assert z.name == "Testy"


def test_twin_phase_and_side_inside_mirrored_subtree():
    # Movement Cycle / Type are per part in the Zook Kit: a two-part leg alternates on the shin.
    shin = leg(around=0, movement={"type": "double", "phase": 0.1, "side": "Left side"},
               mirror_phase=0.6, mirror_side="Always")
    z = Zook.from_spec(spec(leg(around=90, mirror=True, children=[shin])))
    a, b = z.root_part.findall("bodysolid")
    (sa,), (sb,) = a.findall("bodysolid"), b.findall("bodysolid")
    assert (sa.attrs["leg_phase"], sb.attrs["leg_phase"]) == ("0.1", "0.6")
    assert (sa.attrs["ik_side"], sb.attrs["ik_side"]) == ("Left side", "Always")
    assert a.attrs["leg_phase"] == b.attrs["leg_phase"]  # the thigh keeps its own
    # around 0 is stored like the Zook Kit's slider does, so the twin's single-part path is mirrored
    assert (sa.attrs["phi"], sb.attrs["phi"]) == ("0.001", "-0.001")
    back = z.to_spec()["body"]["children"][0]
    assert "mirror_phase" not in back and "attach" not in back["children"][0]
    assert back["children"][0]["mirror_phase"] == 0.6 and back["children"][0]["mirror_side"] == "Always"


def test_twin_side_swaps_by_default():
    z = Zook.from_spec(spec(leg(movement={"type": "single", "side": "Left side"}, mirror=True)))
    a, b = z.root_part.findall("bodysolid")
    assert (a.attrs["ik_side"], b.attrs["ik_side"]) == ("Left side", "Right side")
    assert "mirror_side" not in z.to_spec()["body"]["children"][0]


@pytest.mark.parametrize("s, msg", [
    (spec(leg(mirror_phase=0.5)), "only for a mirrored part or a part inside one"),
    (spec(leg(mirror=True, mirror_side="Up")), "mirror_side: one of Auto"),
    (spec(mirror_side="Always"), "not allowed on the root"),
])
def test_twin_override_validation(s, msg):
    assert any(msg in p for p in validate_spec(s)), validate_spec(s)


def test_mirror_phase_on_still_part_warns():
    from bamzooki.sdk.zook import spec_warnings

    thigh_phase = spec(leg(mirror=True, mirror_phase=0.5, children=[leg(movement={"type": "double"})]))
    assert validate_spec(thigh_phase) == []
    assert any("no effect" in w for w in spec_warnings(thigh_phase))
    assert spec_warnings(spec(leg(mirror=True, children=[leg(movement={"type": "double"}, mirror_phase=0.5)]))) == []


def test_raw_gait_values_read_as_sliders():
    # Older Zooks store Evo's raw values (IKspeed = 0.03 x Cycle Speed, ...) without the sliders.
    z = Zook.from_spec(spec())
    root = z.root_part
    for k in ("zook_speed", "turn_sharpness", "turn_smoothness"):
        del root.attrs[k]
    root.attrs.update({"IKspeed": "0.024", "minAmplitude": "-1", "minAngle": "36"})
    assert z.to_spec()["body"]["gait"] == {"cycle_speed": 0.8, "turn_sharpness": 1.0, "turn_smoothness": 0.2}
    root.attrs["zook_speed"] = "0.3"  # a slider, when present, is what Evo uses
    assert z.to_spec()["body"]["gait"]["cycle_speed"] == 0.3


def test_from_spec_rejects_invalid():
    with pytest.raises(SpecError):
        Zook.from_spec(spec(size={"width": 9}))


def test_default_texture_exists_in_game():
    # "bare" (the Zook Kit's default) has no texture file; Zooks using it fail in contests.
    assert schema.param("look", "texture", "root").default == "aawhite"


def test_apply_edits():
    s = spec(leg())
    out = apply_edits(s, [{"op": "set", "path": "body.children[0].size.width", "value": 1.5},
                          {"op": "add", "path": "body.children", "value": leg(around=-90)},
                          {"op": "remove", "path": "body.mesh"}])
    assert out["body"]["children"][0]["size"] == {"length": 1.0, "width": 1.5}
    assert len(out["body"]["children"]) == 2 and "mesh" not in out["body"]
    assert s["body"]["mesh"] == "Blob"  # original untouched
    with pytest.raises(SpecError):
        apply_edits(s, [{"op": "remove", "path": "body.children[5]"}])


# --- install-backed ---

@pytest.mark.install
def test_examples_through_spec(install):
    for p in install.example_zooks():
        if "Teams" not in p.parts:
            continue
        z = Zook.load(p, install.zook_key)
        s = z.to_spec()
        assert validate_spec(s) == [], p
        back = Zook.from_spec(s)
        assert back.part_count() == z.part_count()
        # every part's Movement Cycle survives, twins inside mirrored subtrees included
        phases = lambda zk: sorted(round(float(e.attrs.get("leg_phase", 0)), 2) for e in zk.root_part.iter("bodysolid"))
        assert phases(back) == phases(z), p


@pytest.mark.install
def test_genome_roundtrip_examples(install):
    from bamzooki.codec import archive

    for p in install.example_zooks():
        text = archive.decode(p.read_bytes(), install.zook_key)[1].decode("latin-1")
        assert genome.dumps(genome.loads(text)) == text, p


@pytest.mark.install
def test_example_likeness(install):
    from bamzooki.sdk.zook import copied_example, likeness

    examples = {p.parent.name: Zook.load(p, install.zook_key) for p in install.example_zooks() if "Teams" in p.parts}
    for name, z in examples.items():
        assert copied_example(z, examples) == name
        others = {n: e for n, e in examples.items() if n != name}
        assert copied_example(z, others) is None, name  # the examples are all different designs
        # a copy with extra parts bolted on is still a copy
        s = z.to_spec()
        s["body"].setdefault("children", []).extend([leg(around=180) for _ in range(5)])
        assert likeness(Zook.from_spec(s), z) >= 0.9, name
    # a design from scratch in the guide's style isn't
    own = spec(*[leg(around=a, mirror=True, rotate={"up_down": 50}, movement={"type": "single"}) for a in (45, 90, 135)],
               size={"width": 1.5, "height": 0.8, "length": 2})
    assert copied_example(Zook.from_spec(own), examples) is None


def test_wave_param_roundtrip():
    s = spec(leg(around=180, movement={"wave": 0.3, "phase": 0.2}))
    z = Zook.from_spec(s)
    (child,) = z.root_part.findall("bodysolid")
    assert child.attrs["spine_amplitude"] == "0.3"
    assert z.to_spec()["body"]["children"][0]["movement"]["wave"] == 0.3


@pytest.mark.install
def test_own_zooks_must_differ_more_than_from_examples(install):
    from bamzooki.sdk.zook import COPY_THRESHOLD, DISTINCT_THRESHOLD, resemblance, too_like

    assert DISTINCT_THRESHOLD < COPY_THRESHOLD  # stricter between a model's own Zooks
    ex = {p.parent.name: Zook.load(p, install.zook_key) for p in install.example_zooks() if "Teams" in p.parts}
    names = sorted(ex)
    for i, a in enumerate(names):  # different designs pass, both ways
        for b in names[i + 1:]:
            assert resemblance(ex[a], ex[b]) < DISTINCT_THRESHOLD, (a, b)
    s = ex["Spider"].to_spec()
    for p in s["body"]["children"][:4]:  # half the legs restyled: still too close to count as another Zook
        p.setdefault("size", {})["width"] = 0.5
    assert too_like(Zook.from_spec(s), {"Spider": ex["Spider"]}) == "Spider"
