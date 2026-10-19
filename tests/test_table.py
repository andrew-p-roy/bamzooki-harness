"""Bonsai text table codec: synthetic tests plus a parse of every table file in the install."""

import pytest

from bamzooki.codec import table
from bamzooki.codec.table import Quat, Vec

SAMPLE = """"name" = "Zook Sumo"
"path" = "C:\\Users\\me\\x.zook"
"n" = 3
"f" = -1.62921e-007
"off" = false
"position" = (-4.2, 2, -0.052)
"rotation" = <0, 1, 0, 1>
"rounds" =
{
\t1 =
\t{
\t\t"contest" = "CP_ZOOK_SUMO"
\t\t"contestants" =
\t\t{
\t\t}
\t}
}
"""


def test_loads_sample():
    t = table.loads(SAMPLE)
    assert t["name"] == "Zook Sumo"
    assert t["path"] == "C:\\Users\\me\\x.zook"
    assert t["n"] == 3 and isinstance(t["n"], int)
    assert t["f"] == pytest.approx(-1.62921e-7)
    assert t["off"] is False
    assert t["position"] == Vec(-4.2, 2, -0.052)
    assert t["rotation"] == Quat(0, 1, 0, 1)
    assert t["rounds"][1] == {"contest": "CP_ZOOK_SUMO", "contestants": {}}


def test_roundtrip():
    t = table.loads(SAMPLE)
    assert table.loads(table.dumps(t)) == t


def test_dumps_types():
    out = table.dumps({"a": 1, "b": 0.5, "c": "x", "d": Vec(1, 2, 3), "e": {2: "y"}, "skip": None})
    assert table.loads(out) == {"a": 1, "b": 0.5, "c": "x", "d": Vec(1, 2, 3), "e": {2: "y"}}
    with pytest.raises(table.TableError):
        table.dumps({"bad": 'has "quote"'})


@pytest.mark.parametrize("bad", ['"a" = {', '"a" 1', '"a" = }', '= 1'])
def test_errors(bad):
    with pytest.raises(table.TableError):
        table.loads(bad)


@pytest.mark.install
def test_install_tables_parse(install):
    files = [p for p in install.root.rglob("*")
             if p.suffix.lower() in (".contest", ".dat", ".tab") and p.is_file()
             and not p.name.lower().startswith("unins")]  # uninstaller data, not a table
    assert len(files) >= 13
    for p in files:
        t = table.loads(p.read_text("latin-1"))
        assert t, p
