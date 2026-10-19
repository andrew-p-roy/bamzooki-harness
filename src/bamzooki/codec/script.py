"""Encrypted Lua scripts (.ssx, .sax).

The file is a sequence of RSA-OAEP (SHA-1, MGF1-SHA-1) ciphertext blocks. Their
concatenated plaintext is a zlib stream of the Lua source. With the game's
512-bit key each 64-byte block carries 22 bytes; sizes are derived from the key
so tests can use any key size.
"""

from __future__ import annotations

import zlib

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.serialization import load_der_private_key

OAEP = padding.OAEP(mgf=padding.MGF1(hashes.SHA1()), algorithm=hashes.SHA1(), label=None)
_SHA1_LEN = 20


class ScriptError(ValueError):
    pass


def load_private_key(der: bytes) -> rsa.RSAPrivateKey:
    """Load a PKCS#8 DER RSA private key (the format of the install's he.skx)."""
    key = load_der_private_key(der, password=None)
    if not isinstance(key, rsa.RSAPrivateKey):
        raise ScriptError("script key is not an RSA private key")
    return key


def block_sizes(key: rsa.RSAPrivateKey | rsa.RSAPublicKey) -> tuple[int, int]:
    """Return (ciphertext block size, max plaintext chunk size)."""
    n = (key.key_size + 7) // 8
    return n, n - 2 * _SHA1_LEN - 2


def decode(data: bytes, key: rsa.RSAPrivateKey) -> bytes:
    block, _ = block_sizes(key)
    if len(data) % block:
        raise ScriptError(f"script size {len(data)} is not a multiple of {block}")
    try:
        raw = b"".join(key.decrypt(data[i:i + block], OAEP) for i in range(0, len(data), block))
    except ValueError as e:
        raise ScriptError(f"RSA decrypt failed (wrong key?): {e}") from None
    return zlib.decompress(raw)


def encode(source: bytes, key: rsa.RSAPrivateKey | rsa.RSAPublicKey, level: int = 9) -> bytes:
    pub = key.public_key() if isinstance(key, rsa.RSAPrivateKey) else key
    _, chunk = block_sizes(pub)
    comp = zlib.compress(source, level)
    return b"".join(pub.encrypt(comp[i:i + chunk], OAEP) for i in range(0, len(comp), chunk))
