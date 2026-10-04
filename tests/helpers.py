"""Build synthetic silent-payment transactions for tests (signatures are placeholders:
receipts never look at them, only at the public keys BIP-352 reads)."""

from dataclasses import dataclass

from spreceipt.bip352 import encode_sp_address, labeled_spend_key, sender_output_keys
from spreceipt.crypto import G, GE, Scalar, hash160, random_scalar
from spreceipt.tx import OutPoint, Tx, TxIn, TxOut, p2tr_script, p2wpkh_script

DUMMY_ECDSA_SIG = bytes(71)
DUMMY_SCHNORR_SIG = bytes(64)


@dataclass
class Receiver:
    b_scan: Scalar
    b_spend: Scalar

    @classmethod
    def new(cls) -> "Receiver":
        return cls(random_scalar(), random_scalar())

    def B_m(self, label: int | None = None) -> GE:
        return labeled_spend_key(self.b_scan, self.b_spend * G, label)

    def address(self, label: int | None = None, network: str = "regtest") -> str:
        return encode_sp_address(self.b_scan * G, self.B_m(label), network)


def p2wpkh_input(d: Scalar, n: int) -> tuple[TxIn, bytes]:
    pk = (d * G).to_bytes_compressed()
    txin = TxIn(OutPoint(bytes([n + 1]) * 32, n), witness=[DUMMY_ECDSA_SIG, pk])
    return txin, p2wpkh_script(hash160(pk))


def p2tr_input(d: Scalar, n: int) -> tuple[TxIn, bytes]:
    txin = TxIn(OutPoint(bytes([n + 101]) * 32, n), witness=[DUMMY_SCHNORR_SIG])
    return txin, p2tr_script((d * G).to_bytes_xonly())


def pay(inputs: list[tuple[TxIn, bytes, Scalar, bool]], recipients: list[tuple[Receiver, int | None]],
        amount: int = 150_000, change: int = 40_000) -> tuple[Tx, list[bytes]]:
    """A tx spending `inputs` that pays each (receiver, label) `amount`, plus a change output."""
    tx = Tx(vin=[i[0] for i in inputs])
    spks = [i[1] for i in inputs]
    keys = [(i[2], i[3]) for i in inputs]
    targets = [(r.b_scan * G, r.B_m(label)) for r, label in recipients]
    for xonly in sender_output_keys(keys, [i.prevout for i in tx.vin], targets):
        tx.vout.append(TxOut(amount, p2tr_script(xonly)))
    tx.vout.append(TxOut(change, p2wpkh_script(bytes(20))))
    return tx, spks
