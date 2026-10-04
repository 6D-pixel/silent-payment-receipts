"""Checking a receipt against the real transaction."""

from dataclasses import dataclass, field

from .bip352 import NotEligible, SP_HRP, decode_sp_address, eligible_inputs, input_hash
from .crypto import G, GE, dleq_verify_proof, schnorr_verify
from .prove import find_paid_outputs
from .receipt import Receipt, output_sig_message, receipt_message
from .tx import OutPoint, Tx


@dataclass
class VerifyResult:
    valid: bool
    reason: str
    receipt: Receipt
    paid_outputs: list[dict] = field(default_factory=list)
    confirmations: int | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def total_sat(self) -> int:
        return sum(o["amount_sat"] for o in self.paid_outputs)

    def summary(self) -> str:
        r = self.receipt
        lines = [f"Receipt:       {'VALID' if self.valid else 'INVALID'} ({self.reason})",
                 f"Claim by:      {r.role}",
                 f"Transaction:   {r.txid}",
                 f"Paid address:  {r.address}",
                 f"Memo:          {r.memo!r}"]
        if self.paid_outputs:
            outs = ", ".join(f"output {o['vout']}: {o['amount_sat']:,} sat" for o in self.paid_outputs)
            lines.append(f"Paid outputs:  {outs} (total {self.total_sat:,} sat)")
        lines.append("Confirmations: " + ("not checked" if self.confirmations is None else str(self.confirmations)))
        lines += [f"Warning:       {w}" for w in self.warnings]
        return "\n".join(lines)


def verify_receipt(receipt: Receipt, tx: Tx, prevout_spks: list[bytes],
                   confirmations: int | None = None, min_conf: int = 1) -> VerifyResult:
    """Check every claim in the receipt against tx and its real prevouts.

    Nothing in the receipt is trusted: A, input_hash and the outputs are all
    rebuilt from the transaction. `prevout_spks` must come from the funding
    transactions (sources.resolve checks their txids). Pass `confirmations` from
    your own node or explorer; None means confirmation was not checked.
    """
    def fail(reason: str) -> VerifyResult:
        return VerifyResult(False, reason, receipt, confirmations=confirmations)

    if tx.txid_hex != receipt.txid:
        return fail("transaction does not match the receipt's txid")
    try:
        hrp, B_scan, B_m = decode_sp_address(receipt.address)
    except ValueError as e:
        return fail(f"bad address: {e}")
    if hrp != SP_HRP[receipt.network]:
        return fail("address prefix does not match the network")

    try:
        inputs = eligible_inputs(tx, prevout_spks)
    except NotEligible as e:
        return fail(f"not a silent-payment transaction: {e}")
    pubkeys = {i.index: i.pubkey for i in inputs}

    # Every eligible input must be covered exactly once.
    covered: list[int] = [i for s in receipt.shares for i in s.inputs]
    if len(covered) != len(set(covered)):
        return fail("an input is covered by more than one share")
    if set(covered) - set(pubkeys):
        return fail(f"shares cover inputs that are not eligible: {sorted(set(covered) - set(pubkeys))}")
    if set(covered) != set(pubkeys):
        return fail(f"receipt is incomplete: inputs {sorted(set(pubkeys) - set(covered))} have no share")

    m = receipt_message(receipt.role, tx.txid, B_scan, B_m, receipt.memo)
    A = GE.sum(*pubkeys.values())
    if A.infinity:
        return fail("input keys sum to the point at infinity")

    if receipt.role == "sender":
        for n, s in enumerate(receipt.shares):
            A_i = GE.sum(*[pubkeys[i] for i in s.inputs])
            if not dleq_verify_proof(A_i, B_scan, s.share, s.proof, G=G, m=m):
                return fail(f"share {n}: DLEQ proof does not verify (wrong keys, memo or address)")
    else:
        if len(receipt.shares) != 1:
            return fail("a receiver proof must have exactly one share")
        s = receipt.shares[0]
        if not dleq_verify_proof(B_scan, A, s.share, s.proof, G=G, m=m):
            return fail("DLEQ proof does not verify (wrong scan key, memo or address)")

    C = GE.sum(*[s.share for s in receipt.shares])
    S = input_hash([i.prevout for i in tx.vin], A) * C
    paid = find_paid_outputs(tx, S, B_m)
    if not paid:
        return fail("the transaction does not pay this silent-payment address")
    if receipt.outputs and sorted(o["vout"] for o in receipt.outputs) != [o["vout"] for o in paid]:
        return fail("the receipt's listed outputs do not match what the transaction pays")

    if receipt.role == "receiver":
        for o in paid:
            sig = receipt.output_sigs.get(o["vout"])
            if sig is None:
                return fail(f"output {o['vout']}: no output-key signature (a scan key alone is not proof of ownership)")
            msg = output_sig_message(m, OutPoint(tx.txid, o["vout"]))
            if not schnorr_verify(msg, tx.vout[o["vout"]].script_pubkey[2:], sig):
                return fail(f"output {o['vout']}: output-key signature does not verify")

    result = VerifyResult(True, "all checks passed", receipt, paid, confirmations)
    if confirmations is None:
        result.warnings.append("confirmation was not checked; only trust this against a confirmed transaction")
    elif confirmations < min_conf:
        return VerifyResult(False, f"transaction has {confirmations} confirmations, need {min_conf}",
                            receipt, paid, confirmations)
    return result
