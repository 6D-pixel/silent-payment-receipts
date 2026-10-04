"""Making receipts: the sender's receipt and the receiver's proof."""

import os

from .bip352 import (
    K_MAX, NotEligible, SP_HRP, decode_sp_address, eligible_inputs, encode_sp_address,
    even_y_key, input_hash, label_tweak, labeled_spend_key, output_key, shared_secret_tweak,
)
from .crypto import G, GE, Scalar, dleq_generate_proof, schnorr_sign
from .receipt import Receipt, Share, output_sig_message, receipt_message
from .tx import OutPoint, Tx, is_p2tr


class ReceiptError(ValueError):
    pass


def find_paid_outputs(tx: Tx, S: GE, B_m: GE) -> list[dict]:
    """Every output of tx that is P_k = B_m + hash(S||k)*G for some k."""
    p2tr = {o.script_pubkey[2:]: i for i, o in enumerate(tx.vout) if is_p2tr(o.script_pubkey)}
    found = []
    for k in range(min(len(p2tr), K_MAX)):
        xonly = output_key(S, B_m, k).to_bytes_xonly()
        if xonly in p2tr:
            vout = p2tr[xonly]
            found.append({"vout": vout, "k": k, "amount_sat": tx.vout[vout].value})
    return sorted(found, key=lambda o: o["vout"])


def _check_network(address: str, network: str) -> tuple[GE, GE]:
    hrp, B_scan, B_m = decode_sp_address(address)
    if hrp != SP_HRP[network]:
        raise ReceiptError(f"address prefix {hrp!r} does not match network {network!r}")
    return B_scan, B_m


def make_sender_receipt(tx: Tx, prevout_spks: list[bytes], address: str, memo: str,
                        keys: list[Scalar], network: str, bundle: dict | None = None) -> Receipt:
    """Sender side: prove that the inputs you control paid `address`.

    If you control every eligible input the receipt is complete. If not (for
    example a coinjoin), it holds a share for your inputs only; the other
    parties make their own with the same memo and you merge them with combine().
    """
    B_scan, B_m = _check_network(address, network)
    try:
        inputs = eligible_inputs(tx, prevout_spks)
    except NotEligible as e:
        raise ReceiptError(str(e)) from e

    covered, secrets = [], []
    for inp in inputs:
        for d in keys:
            d_eff = even_y_key(d, inp.is_taproot)
            if d_eff * G == inp.pubkey:
                covered.append(inp.index)
                secrets.append(d_eff)
                break
    if not covered:
        raise ReceiptError("none of the given keys belongs to an eligible input of this transaction")
    a = Scalar.sum(*secrets)
    if a == 0:
        raise ReceiptError("your input keys sum to zero")

    m = receipt_message("sender", tx.txid, B_scan, B_m, memo)
    C = a * B_scan
    proof = dleq_generate_proof(int(a), B_scan, os.urandom(32), m=m)
    if proof is None:
        raise ReceiptError("could not create the DLEQ proof")

    outputs = []
    if len(covered) == len(inputs):
        A = GE.sum(*[i.pubkey for i in inputs])
        S = input_hash([i.prevout for i in tx.vin], A) * C
        outputs = find_paid_outputs(tx, S, B_m)
        if not outputs:
            raise ReceiptError("this transaction does not pay that silent-payment address")

    return Receipt(role="sender", network=network, txid=tx.txid_hex, address=address, memo=memo,
                   shares=[Share(covered, C, proof)], outputs=outputs, bundle=bundle)


def combine(receipts: list[Receipt], tx: Tx | None = None, prevout_spks: list[bytes] | None = None) -> Receipt:
    """Merge partial sender receipts (one per party) into one receipt."""
    if not receipts:
        raise ReceiptError("nothing to combine")
    first = receipts[0]
    for r in receipts:
        if r.role != "sender":
            raise ReceiptError("only sender receipts can be combined")
        if (r.txid, r.address, r.memo, r.network) != (first.txid, first.address, first.memo, first.network):
            raise ReceiptError("receipts are for different transactions, addresses, memos or networks")
    shares, seen = [], set()
    for r in receipts:
        for s in r.shares:
            if seen & set(s.inputs):
                raise ReceiptError(f"inputs {sorted(seen & set(s.inputs))} are covered twice")
            seen |= set(s.inputs)
            shares.append(s)
    bundle = next((r.bundle for r in receipts if r.bundle), None)
    merged = Receipt(role="sender", network=first.network, txid=first.txid, address=first.address,
                     memo=first.memo, shares=shares, bundle=bundle)
    if tx is not None and prevout_spks is not None:
        _, B_m = _check_network(first.address, first.network)
        inputs = eligible_inputs(tx, prevout_spks)
        if seen == {i.index for i in inputs}:
            A = GE.sum(*[i.pubkey for i in inputs])
            C = GE.sum(*[s.share for s in shares])
            merged.outputs = find_paid_outputs(tx, input_hash([i.prevout for i in tx.vin], A) * C, B_m)
    return merged


def make_receiver_proof(tx: Tx, prevout_spks: list[bytes], b_scan: Scalar, b_spend: Scalar,
                        label: int | None, memo: str, network: str, bundle: dict | None = None) -> Receipt:
    """Receiver side: prove that outputs of tx belong to your silent-payment address.

    Two proofs are needed. The DLEQ shows the payment was made to your scan key,
    and a BIP-340 signature by each output key shows you can spend it. A party
    that only holds the scan key (a scanning server) cannot make the signature.

    The points come from the transaction itself, never from the caller, so this
    function cannot be used as a Diffie-Hellman oracle on b_scan.
    """
    B_scan = b_scan * G
    B_spend = b_spend * G
    B_m = labeled_spend_key(b_scan, B_spend, label)
    address = encode_sp_address(B_scan, B_m, network)
    try:
        inputs = eligible_inputs(tx, prevout_spks)
    except NotEligible as e:
        raise ReceiptError(str(e)) from e
    A = GE.sum(*[i.pubkey for i in inputs])
    if A.infinity:
        raise ReceiptError("input keys sum to the point at infinity")

    m = receipt_message("receiver", tx.txid, B_scan, B_m, memo)
    C = b_scan * A
    proof = dleq_generate_proof(int(b_scan), A, os.urandom(32), m=m)
    if proof is None:
        raise ReceiptError("could not create the DLEQ proof")

    S = input_hash([i.prevout for i in tx.vin], A) * C
    outputs = find_paid_outputs(tx, S, B_m)
    if not outputs:
        raise ReceiptError("this transaction does not pay that silent-payment address")

    spend_base = b_spend + (label_tweak(b_scan, label) if label is not None else Scalar(0))
    sigs = {}
    for o in outputs:
        d = spend_base + shared_secret_tweak(S, o["k"])
        assert (d * G).to_bytes_xonly() == tx.vout[o["vout"]].script_pubkey[2:]
        msg = output_sig_message(m, OutPoint(tx.txid, o["vout"]))
        sigs[o["vout"]] = schnorr_sign(msg, d.to_bytes(), os.urandom(32))

    return Receipt(role="receiver", network=network, txid=tx.txid_hex, address=address, memo=memo,
                   shares=[Share([i.index for i in inputs], C, proof)], outputs=outputs,
                   output_sigs=sigs, bundle=bundle)
