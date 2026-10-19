"""Zoo refs/versioning (synthetic) and the MCP tool surface (install-backed, no engine launch)."""

import asyncio
from pathlib import Path

import pytest

from bamzooki.config import Config
from bamzooki.install import Install
from bamzooki.sdk.store import Zoo, safe_name


def make_zoo(tmp_path: Path) -> Zoo:
    ex = tmp_path / "examples"
    (ex / "Ant").mkdir(parents=True)
    (ex / "Ant" / "2.1.zook").write_bytes(b"")
    inst = Install(root=tmp_path, config=Config(zoo_dir=tmp_path / "zoo"))
    return Zoo(inst, examples_root=ex)


def test_zoo_versions_and_refs(tmp_path):
    zoo = make_zoo(tmp_path)
    assert zoo.resolve("Ant").label == "examples/Ant"
    v1, p1 = zoo.new_version("claude", "Stomper")
    p1.write_bytes(b"x")
    v2, p2 = zoo.new_version("claude", "Stomper")
    p2.write_bytes(b"x")
    assert (v1, v2) == (1, 2) and p2.name == "2.0.zook"
    assert zoo.designs("claude") == {"Stomper": [1, 2]}
    assert zoo.resolve("Stomper", model="claude").label == "claude/Stomper@2"
    assert zoo.resolve("claude/Stomper@1").path == p1
    with pytest.raises(KeyError):
        zoo.resolve("claude/Stomper@7")
    with pytest.raises(KeyError):
        zoo.resolve("Nope")


@pytest.mark.parametrize("bad", ["", "a/b", "..", "x" * 26, "semi;colon"])
def test_safe_name(bad):
    with pytest.raises(ValueError):
        safe_name(bad)


@pytest.mark.install
def test_mcp_tools(install):
    from bamzooki.mcp_server import build_server
    from bamzooki.studio import Studio

    srv = build_server(Studio("pytest"))
    names = {t.name for t in asyncio.run(srv.list_tools())}
    assert {"get_design_guide", "list_contests", "get_zook", "save_zook", "edit_zook", "preview_zook",
            "run_contest", "list_results"} <= names
    res = asyncio.run(srv.call_tool("get_contest", {"contest": "Zook Sumo"}))
    assert "raised round table" in res.content[0].text
