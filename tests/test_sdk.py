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
        assert Zook.from_spec(s).part_count() == z.part_count()


@pytest.mark.install
def test_genome_roundtrip_examples(install):
    from bamzooki.codec import archive

    for p in install.example_zooks():
        text = archive.decode(p.read_bytes(), install.zook_key)[1].decode("latin-1")
        assert genome.dumps(genome.loads(text)) == text, p
