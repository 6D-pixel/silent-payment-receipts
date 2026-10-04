"""The parts of BIP-352 (silent payments) that receipts need.

Input-eligibility rules follow the BIP-352 reference implementation
(bip-0352/reference.py, BSD-2-Clause).
"""

from dataclasses import dataclass
import struct

from ._vendor import bech32m
from .crypto import G, GE, Scalar, hash160, tagged_hash
from .tx import OutPoint, Tx, is_p2pkh, is_p2sh, is_p2tr, is_p2wpkh, witness_version

K_MAX = 2323  # BIP-352 per-group recipient limit
NUMS_H = bytes.fromhex("50929b74c1a04954b78b4b6035e97a5e078a5a0f28ec96d547bfee9ace803ac0")

SP_HRP = {"main": "sp", "test": "tsp", "signet": "tsp", "regtest": "tsp"}
SEGWIT_HRP = {"main": "bc", "test": "tb", "signet": "tb", "regtest": "bcrt"}


class NotEligible(ValueError):
    """The transaction cannot carry a silent payment under BIP-352 v0 rules."""


# ---------------------------------------------------------------- addresses

def encode_sp_address(B_scan: GE, B_m: GE, network: str = "main") -> str:
    data = bech32m.convertbits(B_scan.to_bytes_compressed() + B_m.to_bytes_compressed(), 8, 5)
    return bech32m.bech32_encode(SP_HRP[network], [0] + data, bech32m.Encoding.BECH32M)


def decode_sp_address(address: str) -> tuple[str, GE, GE]:
    """Return (hrp, B_scan, B_m). Only v0 addresses are accepted."""
    hrp, data, spec = bech32m.bech32_decode(address)
    if hrp is None or spec != bech32m.Encoding.BECH32M or hrp not in ("sp", "tsp"):
        raise ValueError("not a silent-payment address")
    if not data or data[0] != 0:
        raise ValueError("only v0 silent-payment addresses are supported")
    payload = bech32m.convertbits(data[1:], 5, 8, False)
    if payload is None or len(payload) != 66:
        raise ValueError("bad silent-payment address payload")
    payload = bytes(payload)
    return hrp, GE.from_bytes_compressed(payload[:33]), GE.from_bytes_compressed(payload[33:])


def label_tweak(b_scan: Scalar, m: int) -> Scalar:
    return Scalar.from_bytes_checked(tagged_hash("BIP0352/Label", b_scan.to_bytes() + struct.pack(">I", m)))


def labeled_spend_key(b_scan: Scalar, B_spend: GE, m: int | None) -> GE:
    return B_spend if m is None else B_spend + label_tweak(b_scan, m) * G


def encode_p2tr_address(xonly: bytes, network: str) -> str:
    return bech32m.encode(SEGWIT_HRP[network], 1, xonly)


def encode_p2wpkh_address(pubkey: GE, network: str) -> str:
    return bech32m.encode(SEGWIT_HRP[network], 0, hash160(pubkey.to_bytes_compressed()))


# ----------------------------------------------------------- input rules

def input_pubkey(script_sig: bytes, witness: list[bytes], prevout_spk: bytes) -> GE | None:
    """Public key an input contributes to the shared secret, or None if the input is not eligible."""
    if is_p2pkh(prevout_spk):
        spk_hash = prevout_spk[3:23]
        # BIP-352: parse the scriptSig for the key even when it is malleated.
        for i in range(len(script_sig), 32, -1):
            candidate = script_sig[i - 33:i]
            if hash160(candidate) == spk_hash:
                try:
                    return GE.from_bytes_compressed(candidate)
                except ValueError:
                    pass
        return None
    if is_p2sh(prevout_spk):
        redeem_script = script_sig[1:]
        if is_p2wpkh(redeem_script) and witness:
            try:
                return GE.from_bytes_compressed(witness[-1])
            except (ValueError, AssertionError):
                return None
        return None
    if is_p2wpkh(prevout_spk):
        if witness:
            try:
                return GE.from_bytes_compressed(witness[-1])
            except (ValueError, AssertionError):
                return None
        return None
    if is_p2tr(prevout_spk):
        stack = list(witness)
        if not stack:
            return None
        if len(stack) > 1 and stack[-1][:1] == b"\x50":
            stack.pop()  # annex
        if len(stack) > 1:
            control_block = stack[-1]
            if control_block[1:33] == NUMS_H:
                return None  # script-path spend with the NUMS internal key
        try:
            return GE.from_bytes_xonly(prevout_spk[2:])
        except ValueError:
            return None
    return None


@dataclass
class EligibleInput:
    index: int
    pubkey: GE
    is_taproot: bool


def eligible_inputs(tx: Tx, prevout_spks: list[bytes]) -> list[EligibleInput]:
    """Apply the BIP-352 v0 rules and return the inputs that count toward A."""
    if len(prevout_spks) != len(tx.vin):
        raise ValueError("need one prevout scriptPubKey per input")
    for spk in prevout_spks:
        v = witness_version(spk)
        if v is not None and v > 1:
            raise NotEligible("transaction spends a segwit v2+ output")
    found = []
    for i, (txin, spk) in enumerate(zip(tx.vin, prevout_spks)):
        pk = input_pubkey(txin.script_sig, txin.witness, spk)
        if pk is not None and not pk.infinity:
            found.append(EligibleInput(i, pk, is_p2tr(spk)))
    if not found:
        raise NotEligible("transaction has no input that BIP-352 can use")
    return found


def input_hash(outpoints: list[OutPoint], A: GE) -> Scalar:
    lowest = min(o.serialize() for o in outpoints)
    return Scalar.from_bytes_checked(tagged_hash("BIP0352/Inputs", lowest + A.to_bytes_compressed()))


def output_key(S: GE, B_m: GE, k: int) -> GE:
    """P_k = B_m + hash(S || k)*G, where S is the ECDH shared secret."""
    t_k = Scalar.from_bytes_checked(tagged_hash("BIP0352/SharedSecret", S.to_bytes_compressed() + struct.pack(">I", k)))
    return B_m + t_k * G


def shared_secret_tweak(S: GE, k: int) -> Scalar:
    return Scalar.from_bytes_checked(tagged_hash("BIP0352/SharedSecret", S.to_bytes_compressed() + struct.pack(">I", k)))


def even_y_key(d: Scalar, is_taproot: bool) -> Scalar:
    """BIP-352: taproot input keys are negated when their point has odd y."""
    if is_taproot and not (d * G).has_even_y():
        return -d
    return d


# ---------------------------------------------------------------- sending

def sender_output_keys(input_keys: list[tuple[Scalar, bool]], outpoints: list[OutPoint],
                       recipients: list[tuple[GE, GE]]) -> list[bytes]:
    """x-only output keys, one per recipient (B_scan, B_m), in the given order."""
    a = Scalar.sum(*[even_y_key(d, tr) for d, tr in input_keys])
    if a == 0:
        raise NotEligible("input private keys sum to zero")
    ih = input_hash(outpoints, a * G)
    counters: dict[bytes, int] = {}
    out = []
    for B_scan, B_m in recipients:
        key = B_scan.to_bytes_compressed()
        k = counters.get(key, 0)
        counters[key] = k + 1
        S = (ih * a) * B_scan
        out.append(output_key(S, B_m, k).to_bytes_xonly())
    return out


# -------------------------------------------------------------- receiving

@dataclass
class ScanMatch:
    vout: int
    k: int
    tweak: Scalar       # add to b_spend (label tweak included) to get the output key
    label: int | None


def scan(b_scan: Scalar, B_spend: GE, labels: list[int], tx: Tx, prevout_spks: list[bytes]) -> list[ScanMatch]:
    """Find the outputs of tx that pay this receiver (the normal BIP-352 wallet scan)."""
    try:
        inputs = eligible_inputs(tx, prevout_spks)
    except NotEligible:
        return []
    A = GE.sum(*[i.pubkey for i in inputs])
    if A.infinity:
        return []
    ih = input_hash([i.prevout for i in tx.vin], A)
    S = (ih * b_scan) * A
    label_points = {}
    for m in labels:
        lt = label_tweak(b_scan, m)
        label_points[(lt * G).to_bytes_compressed()] = (m, lt)
    remaining = {i: o.script_pubkey[2:] for i, o in enumerate(tx.vout) if is_p2tr(o.script_pubkey)}
    matches = []
    k = 0
    while k < K_MAX and remaining:
        t_k = shared_secret_tweak(S, k)
        P_k = B_spend + t_k * G
        hit = None
        for vout, xonly in remaining.items():
            if P_k.to_bytes_xonly() == xonly:
                hit = ScanMatch(vout, k, t_k, None)
                break
            out_pt = GE.from_bytes_xonly(xonly)
            for cand in (out_pt - P_k, -out_pt - P_k):
                if not cand.infinity and cand.to_bytes_compressed() in label_points:
                    m, lt = label_points[cand.to_bytes_compressed()]
                    hit = ScanMatch(vout, k, t_k + lt, m)
                    break
            if hit:
                break
        if hit is None:
            break
        matches.append(hit)
        del remaining[hit.vout]
        k += 1
    return matches
