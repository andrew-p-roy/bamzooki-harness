"""The repo must never contain game files, archives or keys derived from an install."""

import pytest

from bamzooki.codec import archive
from bamzooki.config import REPO_ROOT
from bamzooki.guard import check_files, install_secrets, repo_files


def test_repo_is_clean():
    derived = install_secrets() or ([], [])
    violations = check_files(repo_files(REPO_ROOT), *derived, root=REPO_ROOT)
    assert not violations, "\n".join(map(str, violations))


@pytest.mark.parametrize("name", ["a.ssx", "b.SAX", "he.skx", "1.0.zook", "m.dff", "x.bvz"])
def test_guard_flags_extensions(tmp_path, name):
    (tmp_path / name).write_bytes(b"")
    assert check_files([tmp_path / name])


def test_guard_flags_archive_content(tmp_path):
    p = tmp_path / "stomp 1"
    p.write_bytes(archive.encode(b"", b"data", b""))
    assert check_files([p])


def test_guard_flags_secrets(tmp_path):
    secret = b"synthetic secret phrase"
    p = tmp_path / "notes.md"
    p.write_bytes(b"the key is SYNTHETIC SECRET PHRASE ok")
    assert check_files([p], secrets=[secret])
    p.write_bytes(b"hex " + secret.hex().encode())
    assert check_files([p], secrets=[secret])
    p.write_bytes(b"nothing here")
    assert not check_files([p], secrets=[secret])
