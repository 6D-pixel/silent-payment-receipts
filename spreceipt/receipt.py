"""Receipt format (v0) and the message every proof in it signs.

See SPEC.md for the full rules.
"""

from dataclasses import dataclass, field
import json

from .crypto import GE, sha256, tagged_hash
from .tx import OutPoint, txid_to_bytes

FORMAT_VERSION = 0
ROLES = {"sender": 0x00, "receiver": 0x01}
NETWORKS = ("main", "test", "signet", "regtest")
MAX_MEMO_BYTES = 1024

TAG_MESSAGE = "SPReceipt/v0/message"
TAG_OUTPUT_SIG = "SPReceipt/v0/output"


class ReceiptFormatError(ValueError):
    pass


def receipt_message(role: str, txid: bytes, B_scan: GE, B_m: GE, memo: str) -> bytes:
    """The 32-byte message m bound into every DLEQ proof (BIP-374's optional message field).

    m = tagged_hash("SPReceipt/v0/message",
                    role || txid || B_scan || B_m || sha256(memo))
    The role byte stops a receiver-made proof from passing as a sender claim, and
    the memo hash ties the proof to one order, invoice or statement.
    """
    return tagged_hash(
        TAG_MESSAGE,
        bytes([ROLES[role]]) + txid + B_scan.to_bytes_compressed() + B_m.to_bytes_compressed()
        + sha256(memo.encode("utf-8")),
    )


def output_sig_message(m: bytes, outpoint: OutPoint) -> bytes:
    """What the receiver's output key signs (BIP-340) in a receiver proof."""
    return tagged_hash(TAG_OUTPUT_SIG, m + outpoint.serialize())


@dataclass
class Share:
    inputs: list[int]   # indexes of the eligible inputs this share covers
    share: GE           # C_i = a_i * B_scan (sender) or b_scan * A (receiver)
    proof: bytes        # 64-byte BIP-374 DLEQ proof


@dataclass
class Receipt:
    role: str
    network: str
    txid: str           # display (big-endian) hex
    address: str        # the silent-payment address that was paid
    memo: str
    shares: list[Share]
    outputs: list[dict] = field(default_factory=list)       # claims: {"vout", "k", "amount_sat"}
    output_sigs: dict[int, bytes] = field(default_factory=dict)  # receiver role only
    bundle: dict | None = None  # {"tx": hex, "prevout_txs": [hex, ...]}
    version: int = FORMAT_VERSION

    @property
    def txid_bytes(self) -> bytes:
        return txid_to_bytes(self.txid)

    # ------------------------------------------------------------ JSON

    def to_dict(self) -> dict:
        d = {
            "spreceipt": self.version,
            "role": self.role,
            "network": self.network,
            "txid": self.txid,
            "address": self.address,
            "memo": self.memo,
            "shares": [
                {"inputs": s.inputs, "share": s.share.to_bytes_compressed().hex(), "proof": s.proof.hex()}
                for s in self.shares
            ],
        }
        if self.outputs:
            d["outputs"] = self.outputs
        if self.output_sigs:
            d["output_sigs"] = {str(v): sig.hex() for v, sig in sorted(self.output_sigs.items())}
        if self.bundle:
            d["bundle"] = self.bundle
        return d

    def to_json(self, indent: int | None = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    @classmethod
    def from_dict(cls, d: dict) -> "Receipt":
        try:
            version = d["spreceipt"]
            if version != FORMAT_VERSION:
                raise ReceiptFormatError(f"unsupported receipt version {version!r}")
            role = d["role"]
            if role not in ROLES:
                raise ReceiptFormatError(f"unknown role {role!r}")
            network = d["network"]
            if network not in NETWORKS:
                raise ReceiptFormatError(f"unknown network {network!r}")
            txid = d["txid"]
            txid_to_bytes(txid)
            memo = d["memo"]
            if not isinstance(memo, str) or len(memo.encode("utf-8")) > MAX_MEMO_BYTES:
                raise ReceiptFormatError("memo must be a string of at most 1024 bytes")
            shares = []
            for s in d["shares"]:
                inputs = s["inputs"]
                if not inputs or not all(isinstance(i, int) and i >= 0 for i in inputs):
                    raise ReceiptFormatError("share inputs must be non-negative integers")
                proof = bytes.fromhex(s["proof"])
                if len(proof) != 64:
                    raise ReceiptFormatError("proof must be 64 bytes")
                shares.append(Share(list(inputs), GE.from_bytes_compressed(bytes.fromhex(s["share"])), proof))
            if not shares:
                raise ReceiptFormatError("receipt has no shares")
            outputs = list(d.get("outputs", []))
            for o in outputs:
                if not isinstance(o, dict) or not isinstance(o.get("vout"), int):
                    raise ReceiptFormatError("each listed output needs an integer vout")
            output_sigs = {}
            for v, sig in d.get("output_sigs", {}).items():
                sig_b = bytes.fromhex(sig)
                if len(sig_b) != 64:
                    raise ReceiptFormatError("output signature must be 64 bytes")
                output_sigs[int(v)] = sig_b
            return cls(
                role=role, network=network, txid=txid, address=d["address"], memo=memo,
                shares=shares, outputs=outputs, output_sigs=output_sigs,
                bundle=d.get("bundle"), version=version,
            )
        except ReceiptFormatError:
            raise
        except (KeyError, TypeError, ValueError, AssertionError) as e:
            raise ReceiptFormatError(f"malformed receipt: {e}") from e

    @classmethod
    def from_json(cls, text: str) -> "Receipt":
        try:
            return cls.from_dict(json.loads(text))
        except json.JSONDecodeError as e:
            raise ReceiptFormatError(f"not JSON: {e}") from e
