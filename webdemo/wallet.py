"""P2WPKH signing and checking (BIP-143 sighash, ECDSA), so demo payments carry real signatures."""

import os
import struct

from spreceipt.crypto import G, GE, Scalar, hash160
from spreceipt.tx import Tx, dsha256, ser_bytes

N = GE.ORDER
SIGHASH_ALL = 1


def bip143_sighash(tx: Tx, n: int, pubkey_hash: bytes, amount: int) -> bytes:
    """Signature hash for input n spending a P2WPKH output worth `amount` (SIGHASH_ALL)."""
    hash_prevouts = dsha256(b"".join(i.prevout.serialize() for i in tx.vin))
    hash_sequence = dsha256(b"".join(struct.pack("<I", i.sequence) for i in tx.vin))
    hash_outputs = dsha256(b"".join(struct.pack("<q", o.value) + ser_bytes(o.script_pubkey) for o in tx.vout))
    script_code = b"\x19\x76\xa9\x14" + pubkey_hash + b"\x88\xac"
    txin = tx.vin[n]
    return dsha256(struct.pack("<i", tx.version) + hash_prevouts + hash_sequence + txin.prevout.serialize()
                   + script_code + struct.pack("<q", amount) + struct.pack("<I", txin.sequence)
                   + hash_outputs + struct.pack("<I", tx.locktime) + struct.pack("<I", SIGHASH_ALL))


def _der_int(x: int) -> bytes:
    b = x.to_bytes(32, "big").lstrip(b"\x00")
    if b[0] & 0x80:
        b = b"\x00" + b
    return b"\x02" + bytes([len(b)]) + b


def _parse_der(sig: bytes) -> tuple[int, int]:
    if len(sig) < 8 or sig[0] != 0x30 or sig[1] != len(sig) - 2:
        raise ValueError("bad DER signature")
    i, vals = 2, []
    for _ in range(2):
        if i + 2 > len(sig) or sig[i] != 0x02:
            raise ValueError("bad DER signature")
        length = sig[i + 1]
        v = sig[i + 2:i + 2 + length]
        if length == 0 or len(v) != length:
            raise ValueError("bad DER signature")
        vals.append(int.from_bytes(v, "big"))
        i += 2 + length
    if i != len(sig):
        raise ValueError("bad DER signature")
    return vals[0], vals[1]


def ecdsa_sign(d: Scalar, z: bytes) -> bytes:
    """DER signature with low s, as Bitcoin Core requires."""
    zi = int.from_bytes(z, "big")
    while True:
        k = int.from_bytes(os.urandom(32), "big") % N
        if k == 0:
            continue
        r = int((k * G).x) % N
        s = pow(k, -1, N) * (zi + r * int(d)) % N
        if r and s:
            if s > N // 2:
                s = N - s
            body = _der_int(r) + _der_int(s)
            return b"\x30" + bytes([len(body)]) + body


def ecdsa_verify(pubkey: GE, z: bytes, der: bytes) -> bool:
    try:
        r, s = _parse_der(der)
    except ValueError:
        return False
    if not (0 < r < N and 0 < s <= N // 2):
        return False
    w = pow(s, -1, N)
    R = (int.from_bytes(z, "big") * w % N) * G + (r * w % N) * pubkey
    return not R.infinity and int(R.x) % N == r


def sign_p2wpkh(tx: Tx, n: int, d: Scalar, amount: int) -> None:
    pubkey = (d * G).to_bytes_compressed()
    z = bip143_sighash(tx, n, hash160(pubkey), amount)
    tx.vin[n].witness = [ecdsa_sign(d, z) + bytes([SIGHASH_ALL]), pubkey]


def check_p2wpkh(tx: Tx, n: int, prevout_spk: bytes, amount: int) -> str | None:
    """None if input n validly spends the P2WPKH output, else why not."""
    witness = tx.vin[n].witness
    if len(witness) != 2 or not witness[0] or witness[0][-1] != SIGHASH_ALL:
        return "needs a [signature, public key] witness with SIGHASH_ALL"
    if hash160(witness[1]) != prevout_spk[2:]:
        return "public key does not match the coin being spent"
    try:
        pubkey = GE.from_bytes_compressed(witness[1])
    except ValueError:
        return "invalid public key"
    if not ecdsa_verify(pubkey, bip143_sighash(tx, n, prevout_spk[2:], amount), witness[0][:-1]):
        return "signature does not verify"
    return None
