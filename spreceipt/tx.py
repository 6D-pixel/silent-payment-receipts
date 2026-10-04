"""Minimal Bitcoin transaction parsing and serialization."""

from dataclasses import dataclass, field
from io import BytesIO
import hashlib
import struct


def dsha256(b: bytes) -> bytes:
    return hashlib.sha256(hashlib.sha256(b).digest()).digest()


def ser_compact_size(n: int) -> bytes:
    if n < 253:
        return bytes([n])
    if n <= 0xFFFF:
        return b"\xfd" + struct.pack("<H", n)
    if n <= 0xFFFFFFFF:
        return b"\xfe" + struct.pack("<I", n)
    return b"\xff" + struct.pack("<Q", n)


def deser_compact_size(f: BytesIO) -> int:
    n = f.read(1)
    if len(n) != 1:
        raise ValueError("unexpected end of data")
    n = n[0]
    if n == 253:
        return struct.unpack("<H", _read(f, 2))[0]
    if n == 254:
        return struct.unpack("<I", _read(f, 4))[0]
    if n == 255:
        return struct.unpack("<Q", _read(f, 8))[0]
    return n


def _read(f: BytesIO, n: int) -> bytes:
    b = f.read(n)
    if len(b) != n:
        raise ValueError("unexpected end of data")
    return b


def ser_bytes(b: bytes) -> bytes:
    return ser_compact_size(len(b)) + b


def deser_bytes(f: BytesIO) -> bytes:
    return _read(f, deser_compact_size(f))


def ser_witness(stack: list[bytes]) -> bytes:
    return ser_compact_size(len(stack)) + b"".join(ser_bytes(x) for x in stack)


def deser_witness(f: BytesIO) -> list[bytes]:
    return [deser_bytes(f) for _ in range(deser_compact_size(f))]


def txid_to_bytes(txid_hex: str) -> bytes:
    """Display-order txid hex -> internal (little-endian) bytes."""
    b = bytes.fromhex(txid_hex)
    if len(b) != 32:
        raise ValueError("txid must be 32 bytes")
    return b[::-1]


def txid_to_hex(txid: bytes) -> str:
    return txid[::-1].hex()


@dataclass
class OutPoint:
    txid: bytes  # internal byte order
    vout: int

    def serialize(self) -> bytes:
        return self.txid + struct.pack("<I", self.vout)

    def __str__(self) -> str:
        return f"{txid_to_hex(self.txid)}:{self.vout}"


@dataclass
class TxIn:
    prevout: OutPoint
    script_sig: bytes = b""
    sequence: int = 0xFFFFFFFD
    witness: list[bytes] = field(default_factory=list)


@dataclass
class TxOut:
    value: int  # satoshis
    script_pubkey: bytes


@dataclass
class Tx:
    version: int = 2
    vin: list[TxIn] = field(default_factory=list)
    vout: list[TxOut] = field(default_factory=list)
    locktime: int = 0

    def serialize(self, with_witness: bool = True) -> bytes:
        has_witness = with_witness and any(i.witness for i in self.vin)
        r = struct.pack("<i", self.version)
        if has_witness:
            r += b"\x00\x01"
        r += ser_compact_size(len(self.vin))
        for i in self.vin:
            r += i.prevout.serialize() + ser_bytes(i.script_sig) + struct.pack("<I", i.sequence)
        r += ser_compact_size(len(self.vout))
        for o in self.vout:
            r += struct.pack("<q", o.value) + ser_bytes(o.script_pubkey)
        if has_witness:
            for i in self.vin:
                r += ser_witness(i.witness)
        r += struct.pack("<I", self.locktime)
        return r

    @property
    def txid(self) -> bytes:
        """Internal byte order."""
        return dsha256(self.serialize(with_witness=False))

    @property
    def txid_hex(self) -> str:
        return txid_to_hex(self.txid)

    @classmethod
    def from_bytes(cls, raw: bytes) -> "Tx":
        f = BytesIO(raw)
        tx = cls()
        tx.version = struct.unpack("<i", _read(f, 4))[0]
        n_in = deser_compact_size(f)
        has_witness = False
        if n_in == 0:
            flag = _read(f, 1)[0]
            if flag != 1:
                raise ValueError("bad segwit flag")
            has_witness = True
            n_in = deser_compact_size(f)
        for _ in range(n_in):
            txid = _read(f, 32)
            vout = struct.unpack("<I", _read(f, 4))[0]
            script_sig = deser_bytes(f)
            sequence = struct.unpack("<I", _read(f, 4))[0]
            tx.vin.append(TxIn(OutPoint(txid, vout), script_sig, sequence))
        for _ in range(deser_compact_size(f)):
            value = struct.unpack("<q", _read(f, 8))[0]
            tx.vout.append(TxOut(value, deser_bytes(f)))
        if has_witness:
            for i in tx.vin:
                i.witness = deser_witness(f)
        tx.locktime = struct.unpack("<I", _read(f, 4))[0]
        if f.read(1):
            raise ValueError("trailing data after transaction")
        return tx

    @classmethod
    def from_hex(cls, raw_hex: str) -> "Tx":
        return cls.from_bytes(bytes.fromhex(raw_hex.strip()))


def p2tr_script(xonly: bytes) -> bytes:
    assert len(xonly) == 32
    return b"\x51\x20" + xonly


def p2wpkh_script(pubkey_hash: bytes) -> bytes:
    assert len(pubkey_hash) == 20
    return b"\x00\x14" + pubkey_hash


def is_p2tr(spk: bytes) -> bool:
    return len(spk) == 34 and spk[0] == 0x51 and spk[1] == 0x20


def is_p2wpkh(spk: bytes) -> bool:
    return len(spk) == 22 and spk[0] == 0x00 and spk[1] == 0x14


def is_p2sh(spk: bytes) -> bool:
    return len(spk) == 23 and spk[0] == 0xA9 and spk[1] == 0x14 and spk[-1] == 0x87


def is_p2pkh(spk: bytes) -> bool:
    return (len(spk) == 25 and spk[0] == 0x76 and spk[1] == 0xA9 and spk[2] == 0x14
            and spk[-2] == 0x88 and spk[-1] == 0xAC)


def witness_version(spk: bytes) -> int | None:
    """Segwit version of a witness program scriptPubKey, or None."""
    if len(spk) < 4 or len(spk) > 42:
        return None
    if spk[1] != len(spk) - 2 or not (2 <= spk[1] <= 40):
        return None
    if spk[0] == 0x00:
        return 0
    if 0x51 <= spk[0] <= 0x60:
        return spk[0] - 0x50
    return None
