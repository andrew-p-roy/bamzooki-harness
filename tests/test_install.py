"""Codec tests against the user's own install. Skipped when none is configured.
Fixtures are generated in memory / tmp_path from the install, never committed."""

import pytest

from bamzooki.codec import archive, script
from bamzooki.install import find_config_string

pytestmark = pytest.mark.install


def test_script_key_shape(install):
    k = install.script_key
    assert k.key_size == 512
    assert k.public_key().public_numbers().e == 17


def test_all_scripts_decrypt(install):
    paths = install.scripts()
    assert len(paths) > 50
    for p in paths:
        src = script.decode(p.read_bytes(), install.script_key)
        assert src.isascii() or b"\0" not in src, p


def test_script_reencrypt_roundtrip(install):
    src = install.read_script("Scripts/Boot/ContestRunner.ssx")
    data = script.encode(src, install.script_key.public_key())
    assert script.decode(data, install.script_key) == src


def test_zook_key_derived(install):
    assert len(install.zook_key) > 0


def test_example_zooks_roundtrip(install):
    zooks = install.example_zooks()
    assert zooks
    for p in zooks:
        header, plain = archive.decode(p.read_bytes(), install.zook_key)
        # Team Zooks carry the decoy header; NewCreature/1.0.zook (format 1.0) uses "New Zook".
        if "Teams" in p.parts:
            assert header == archive.ZOOK_HEADER
        assert plain.lstrip().startswith(b"<zook"), p
        again = archive.encode(header, plain, install.zook_key)
        assert archive.decode(again, install.zook_key) == (header, plain)


def test_find_config_string_skips_comments():
    src = b'--Config.Set("a", "old")\n  Config.Set( "a" , "new" ) -- "x"\n'
    assert find_config_string(src, "a") == b"new"
    assert find_config_string(src, "b") is None
