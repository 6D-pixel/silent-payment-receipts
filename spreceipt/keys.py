"""Private-key parsing: 64-char hex or WIF."""

from .crypto import GE, Scalar, sha256

B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
WIF_PREFIX = {"main": 0x80, "test": 0xEF, "signet": 0xEF, "regtest": 0xEF}


def b58encode_check(payload: bytes) -> str:
    data = payload + sha256(sha256(payload))[:4]
    n = int.from_bytes(data, "big")
    out = ""
    while n:
        n, r = divmod(n, 58)
        out = B58[r] + out
    return "1" * (len(data) - len(data.lstrip(b"\x00"))) + out


def b58decode_check(s: str) -> bytes:
    n = 0
    for c in s:
        if c not in B58:
            raise ValueError("invalid base58 character")
        n = n * 58 + B58.index(c)
    data = n.to_bytes((n.bit_length() + 7) // 8, "big")
    data = b"\x00" * (len(s) - len(s.lstrip("1"))) + data
    payload, checksum = data[:-4], data[-4:]
    if sha256(sha256(payload))[:4] != checksum:
        raise ValueError("bad base58 checksum")
    return payload


def encode_wif(d: Scalar, network: str) -> str:
    return b58encode_check(bytes([WIF_PREFIX[network]]) + d.to_bytes() + b"\x01")


def parse_private_key(text: str) -> Scalar:
    text = text.strip()
    if len(text) == 64:
        try:
            d = int(text, 16)
        except ValueError:
            d = None
        if d is not None:
            if not 0 < d < GE.ORDER:
                raise ValueError("private key out of range")
            return Scalar(d)
    payload = b58decode_check(text)
    if payload[0] not in (0x80, 0xEF) or len(payload) not in (33, 34):
        raise ValueError("not a WIF private key")
    d = int.from_bytes(payload[1:33], "big")
    if not 0 < d < GE.ORDER:
        raise ValueError("private key out of range")
    return Scalar(d)
