"""Shared fixtures. Anything derived from the game is built at test time from the
user's install and never written into the repo; those tests skip without one."""

import pytest

from bamzooki.install import Install, InstallNotFound


@pytest.fixture(scope="session")
def install() -> Install:
    try:
        return Install.discover()
    except InstallNotFound as e:
        pytest.skip(f"no BAMZOOKi install configured ({e.__class__.__name__})")
