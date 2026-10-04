"""Curve helpers and the BIP-374 DLEQ proof.

The DLEQ functions are adapted from the BIP-374 reference implementation
(bip-0374/reference.py, BSD-2-Clause). Only the argument types changed.
"""

import hashlib
import os

from ._vendor.ripemd160 import ripemd160
from ._vendor.secp256k1lab.bip340 import schnorr_sign, schnorr_verify
from ._vendor.secp256k1lab.secp256k1 import G, GE, Scalar
from ._vendor.secp256k1lab.util import tagged_hash, xor_bytes

__all__ = [
    "G", "GE", "Scalar", "tagged_hash", "sha256", "hash160",
    "schnorr_sign", "schnorr_verify",
    "dleq_generate_proof", "dleq_verify_proof", "random_scalar",
]

DLEQ_TAG_AUX = "BIP0374/aux"
DLEQ_TAG_NONCE = "BIP0374/nonce"
DLEQ_TAG_CHALLENGE = "BIP0374/challenge"


def sha256(b: bytes) -> bytes:
    return hashlib.sha256(b).digest()


def hash160(b: bytes) -> bytes:
    return ripemd160(sha256(b))


def random_scalar() -> Scalar:
    while True:
        k = int.from_bytes(os.urandom(32), "big")
        if 0 < k < GE.ORDER:
            return Scalar(k)


def _dleq_challenge(A: GE, B: GE, C: GE, R1: GE, R2: GE, m: bytes | None, G: GE) -> int:
    if m is not None:
        assert len(m) == 32
    m = b"" if m is None else m
    return int.from_bytes(
        tagged_hash(
            DLEQ_TAG_CHALLENGE,
            A.to_bytes_compressed()
            + B.to_bytes_compressed()
            + C.to_bytes_compressed()
            + G.to_bytes_compressed()
            + R1.to_bytes_compressed()
            + R2.to_bytes_compressed()
            + m,
        ),
        "big",
    ) % GE.ORDER


def dleq_generate_proof(a: int, B: GE, r: bytes, G: GE = G, m: bytes | None = None) -> bytes | None:
    """Prove that A = a*G and C = a*B use the same secret a (BIP-374 GenerateProof)."""
    assert len(r) == 32
    a = int(a)
    if not (0 < a < GE.ORDER):
        return None
    if B.infinity:
        return None
    if m is not None:
        assert len(m) == 32
    A = a * G
    C = a * B
    t = xor_bytes(a.to_bytes(32, "big"), tagged_hash(DLEQ_TAG_AUX, r))
    m_prime = b"" if m is None else m
    rand = tagged_hash(DLEQ_TAG_NONCE, t + A.to_bytes_compressed() + C.to_bytes_compressed() + m_prime)
    k = int.from_bytes(rand, "big") % GE.ORDER
    if k == 0:
        return None
    R1 = k * G
    R2 = k * B
    e = _dleq_challenge(A, B, C, R1, R2, m, G)
    s = (k + e * a) % GE.ORDER
    proof = e.to_bytes(32, "big") + s.to_bytes(32, "big")
    if not dleq_verify_proof(A, B, C, proof, G=G, m=m):
        return None
    return proof


def dleq_verify_proof(A: GE, B: GE, C: GE, proof: bytes, G: GE = G, m: bytes | None = None) -> bool:
    """Check a BIP-374 DLEQ proof (BIP-374 VerifyProof)."""
    if A.infinity or B.infinity or C.infinity or G.infinity:
        return False
    if len(proof) != 64:
        return False
    e = int.from_bytes(proof[:32], "big")
    if e >= GE.ORDER:
        return False
    s = int.from_bytes(proof[32:], "big")
    if s >= GE.ORDER:
        return False
    R1 = s * G - e * A
    if R1.infinity:
        return False
    R2 = s * B - e * C
    if R2.infinity:
        return False
    return e == _dleq_challenge(A, B, C, R1, R2, m, G)
