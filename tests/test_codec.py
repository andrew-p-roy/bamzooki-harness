"""Codec tests on synthetic data (no install needed)."""

import os
import zlib

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from bamzooki.codec import ArchiveError, ScriptError, archive, script


@pytest.fixture(scope="module")
def rsa_key():
    # The game uses RSA-512, e=17, which `cryptography` refuses to generate;
    # the codec derives block sizes from the key, so any size exercises it.
    return rsa.generate_private_key(65537, 1024)


# --- archives ---------------------------------------------------------------

@pytest.mark.parametrize("key", [b"", b"synthetic test key", b"k" * 56])
@pytest.mark.parametrize("plain", [b"", b"<zook version = \"2\" >\n", os.urandom(5000)])
def test_archive_roundtrip(key, plain):
    data = archive.encode(archive.ZOOK_HEADER, plain, key)
    assert data.startswith(archive.MAGIC)
    if key:
        assert len(archive.split(data)[1]) % archive.BLOCK == 0
    header, out = archive.decode(data, key)
    assert header == archive.ZOOK_HEADER
    assert out == plain


def test_archive_layout():
    data = archive.encode(archive.IMAGE_HEADER, b"x", b"")
    prefix = archive.MAGIC + archive.IMAGE_HEADER + b"\n" + archive.MARKER
    assert data.startswith(prefix)
    assert zlib.decompress(data[len(prefix):]) == b"x"


def test_archive_empty_header():
    data = archive.encode(b"", b"payload", b"")
    assert archive.decode(data, b"") == (b"", b"payload")


def test_archive_ignores_trailing_padding():
    key = b"another key"
    data = archive.encode(b"h", b"hello" * 100, key)
    # Re-encrypt with an extra block of zero padding: still decodes.
    _, body = archive.split(data)
    from cryptography.hazmat.decrepit.ciphers.algorithms import Blowfish
    from cryptography.hazmat.primitives.ciphers import Cipher, modes

    c = Cipher(Blowfish(key), modes.ECB())
    plain = c.decryptor().update(body) + b"\0" * 8
    padded = archive.MAGIC + b"h\n" + archive.MARKER + c.encryptor().update(plain)
    assert archive.decode(padded, key)[1] == b"hello" * 100


def test_archive_wrong_key():
    data = archive.encode(archive.ZOOK_HEADER, b"secret stuff" * 50, b"right key")
    with pytest.raises(ArchiveError):
        archive.decode(data, b"wrong key")


def test_archive_errors():
    with pytest.raises(ArchiveError):
        archive.decode(b"not an archive", b"")
    with pytest.raises(ArchiveError):
        archive.decode(archive.MAGIC + b"no marker", b"")
    with pytest.raises(ArchiveError):  # misaligned encrypted payload
        archive.decode(archive.MAGIC + b"\n" + archive.MARKER + b"123", b"key")


# --- scripts ----------------------------------------------------------------

@pytest.mark.parametrize("source", [b"", b"Trace(\"hi\")\n", os.urandom(3000)])
def test_script_roundtrip(rsa_key, source):
    block, chunk = script.block_sizes(rsa_key)
    data = script.encode(source, rsa_key.public_key())
    assert len(data) % block == 0
    assert len(data) // block == -(-len(zlib.compress(source, 9)) // chunk)
    assert script.decode(data, rsa_key) == source


def test_script_block_sizes_game_key_size():
    # RSA-512 + OAEP-SHA1: 64-byte blocks carrying 22 bytes (Bamzooki_Harness.md §4.3).
    class Fake:
        key_size = 512
    assert script.block_sizes(Fake()) == (64, 22)


def test_script_key_der_roundtrip(rsa_key):
    der = rsa_key.private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption())
    k = script.load_private_key(der)
    assert script.decode(script.encode(b"x = 1", k), k) == b"x = 1"


def test_script_errors(rsa_key):
    with pytest.raises(ScriptError):
        script.decode(b"\0" * 10, rsa_key)
    other = rsa.generate_private_key(65537, 1024)
    with pytest.raises(ScriptError):
        script.decode(script.encode(b"x", other), rsa_key)
