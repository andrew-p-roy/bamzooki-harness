"""Bonsai archive container (.zook, .bmi, Registry.sdx, Components files).

Layout::

    MAGIC                     "Bonsai Engine - Archived information file. Version: 000000001. \\n"
    <user header> "\\n"
    MARKER                    00 01 00 00 00
    <payload>                 zlib stream, Blowfish-ECB encrypted (zero padded to 8) if a key is used

Keys are always passed in by the caller; see `bamzooki.install` for deriving
them from the user's install.
"""

from __future__ import annotations

import zlib

from cryptography.hazmat.decrepit.ciphers.algorithms import Blowfish
from cryptography.hazmat.primitives.ciphers import Cipher, modes

MAGIC = b"Bonsai Engine - Archived information file. Version: 000000001. \n"
MARKER = b"\x00\x01\x00\x00\x00"
ZOOK_HEADER = b"\nThis zook file is not compatible with this software. Please upgrade your software."
IMAGE_HEADER = b"Image Version 1.0"
BLOCK = 8


class ArchiveError(ValueError):
    pass


def is_archive(data: bytes) -> bool:
    return data.startswith(MAGIC)


def split(data: bytes) -> tuple[bytes, bytes]:
    """Return (user_header, raw_payload) without decrypting."""
    if not is_archive(data):
        raise ArchiveError("not a Bonsai archive (bad magic)")
    # Assumes the header text itself never contains "\n" followed by MARKER.
    i = data.find(b"\n" + MARKER, len(MAGIC))
    if i < 0:
        raise ArchiveError("archive marker not found")
    return data[len(MAGIC):i], data[i + 1 + len(MARKER):]


def _blowfish(key: bytes) -> Cipher:
    return Cipher(Blowfish(key), modes.ECB())


def decode(data: bytes, key: bytes) -> tuple[bytes, bytes]:
    """Return (user_header, plaintext). Use key=b"" for unencrypted archives."""
    header, body = split(data)
    if key:
        if len(body) % BLOCK:
            raise ArchiveError("payload is not encryption aligned")
        body = _blowfish(key).decryptor().update(body)
    d = zlib.decompressobj()
    try:
        plain = d.decompress(body)
    except zlib.error as e:
        raise ArchiveError(f"payload does not inflate (wrong key?): {e}") from None
    if not d.eof:
        raise ArchiveError("truncated zlib stream")
    # Anything after the zlib stream is block padding; the engine ignores it too.
    return header, plain


def encode(header: bytes, plaintext: bytes, key: bytes, level: int = 9) -> bytes:
    body = zlib.compress(plaintext, level)
    if key:
        body += b"\0" * (-len(body) % BLOCK)
        body = _blowfish(key).encryptor().update(body)
    return MAGIC + header + b"\n" + MARKER + body


def decode_zook(data: bytes, key: bytes) -> bytes:
    _, plain = decode(data, key)
    return plain


def encode_zook(plaintext: bytes, key: bytes) -> bytes:
    return encode(ZOOK_HEADER, plaintext, key)
