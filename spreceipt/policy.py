"""Marketplace rules on top of verify_receipt: does this receipt pay this order?

verify_receipt answers "did this transaction pay this address, and did the payer sign
this memo". A marketplace settling "you never paid me" needs more:

* The shop has one static address, so the memo alone cannot tie a payment to an
  order (the payer can sign any memo for the same output). Each invoice gets an
  exact amount that no other open invoice of the shop has, and the payment must
  confirm while the invoice was open. Amount and time window bind the payment to
  the order. The caller must also accept each outpoint for one order only.
* Everything comes from the order and the chain, never from the receipt's own
  claims: the address is compared as keys, the network is the order's, the amount
  is what the verifier found on chain, and unknown confirmations fail.
"""

from dataclasses import dataclass, field, replace

from .bip352 import decode_sp_address
from .receipt import Receipt
from .tx import Tx
from .verify import verify_receipt


@dataclass(frozen=True)
class OrderTerms:
    """What the shop's payment method issued for one order."""
    shop_address: str
    memo: str
    amount_sat: int
    network: str
    created_height: int  # chain tip when the invoice was made
    expires_height: int  # last block the payment may confirm in


@dataclass
class ChainStatus:
    """The transaction's status from the marketplace's own node or explorer."""
    confirmations: int | None
    block_height: int | None  # None while unconfirmed


@dataclass
class Verdict:
    ok: bool
    code: str
    reason: str
    outpoints: list[tuple[str, int]] = field(default_factory=list)
    amount_sat: int = 0
    warnings: list[str] = field(default_factory=list)


def _keys(address: str) -> tuple[str, bytes, bytes]:
    hrp, B_scan, B_m = decode_sp_address(address)
    return hrp, B_scan.to_bytes_compressed(), B_m.to_bytes_compressed()


def check_payment(receipt: Receipt, tx: Tx, prevout_spks: list[bytes], order: OrderTerms,
                  status: ChainStatus, min_conf: int = 1) -> Verdict:
    """Decide whether `receipt` proves that `order` was paid. Codes:

    ok, wrong_address, wrong_memo, bad_proof, unconfirmed, paid_before_invoice,
    paid_after_expiry, k_gap, wrong_amount.
    """
    def no(code: str, reason: str) -> Verdict:
        return Verdict(False, code, reason)

    try:
        if _keys(receipt.address) != _keys(order.shop_address):
            return no("wrong_address", "the receipt pays a different address than the shop's")
    except ValueError as e:
        return no("wrong_address", f"bad address: {e}")
    if receipt.memo != order.memo:
        return no("wrong_memo", "the receipt names a different order")

    # The order decides the network; a receipt cannot pick a friendlier one.
    if receipt.network != order.network:
        receipt = replace(receipt, network=order.network)
    result = verify_receipt(receipt, tx, prevout_spks)
    if not result.valid:
        return no("bad_proof", result.reason)

    if status.confirmations is None or status.block_height is None or status.confirmations < min_conf:
        have = "unknown" if status.confirmations is None else status.confirmations
        return no("unconfirmed", f"transaction has {have} confirmations, need {min_conf}")
    if status.block_height <= order.created_height:
        return no("paid_before_invoice", "the payment confirmed before the invoice was made")
    if status.block_height > order.expires_height:
        return no("paid_after_expiry", "the payment confirmed after the invoice expired")

    # The payer only knows the shop's public address, so an honest payment uses
    # k = 0, 1, 2 ... with no gaps. The shop's wallet stops scanning at the first
    # missing k, so it would never see an output past a gap.
    ks = sorted(o["k"] for o in result.paid_outputs)
    if ks != list(range(len(ks))):
        return no("k_gap", f"outputs use k = {ks}; the shop's wallet only finds k = 0, 1, 2 ... without gaps")

    if result.total_sat != order.amount_sat:
        return no("wrong_amount", f"paid {result.total_sat:,} sat, the invoice is for exactly {order.amount_sat:,} sat")

    return Verdict(True, "ok", "the receipt proves this order was paid",
                   [(tx.txid_hex, o["vout"]) for o in result.paid_outputs], result.total_sat)
