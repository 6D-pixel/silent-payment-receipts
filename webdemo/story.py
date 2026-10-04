"""The demo story: a shop, a payer (Alice) and a neutral verifier on a simulated chain.

Every method returns plain JSON-ready data for the web page. The receipt work
is done by the unchanged spreceipt package.
"""

import copy
import json
import pathlib
from dataclasses import dataclass

from spreceipt import Receipt, ReceiptError, ReceiptFormatError, make_receiver_proof, make_sender_receipt, verify_receipt
from spreceipt._vendor import bech32m
from spreceipt.bip352 import (
    SEGWIT_HRP, decode_sp_address, encode_p2tr_address, encode_sp_address, labeled_spend_key, scan,
    sender_output_keys,
)
from spreceipt.crypto import G, Scalar, hash160, random_scalar, tagged_hash
from spreceipt.sources import BundleSource, SourceError, make_bundle, resolve
from spreceipt.tx import OutPoint, Tx, TxIn, TxOut, is_p2tr, is_p2wpkh, p2tr_script, p2wpkh_script, ser_bytes

from .simchain import ChainError, SimChain
from .wallet import sign_p2wpkh

NET = "regtest"
FEE_SAT = 20_000
SAMPLES = pathlib.Path(__file__).resolve().parent.parent / "demo" / "samples"

# The checks verify_receipt makes, in order, and the start of each failure reason it can give.
CHECKS = [
    ("tx", "Fetched the transaction from the chain", ("transaction does not match", "fetch:")),
    ("address", "Address is a valid silent-payment address for this network", ("bad address", "address prefix")),
    ("inputs", "Every silent-payment input is covered by the receipt",
     ("not a silent-payment transaction", "an input is covered", "shares cover inputs", "receipt is incomplete",
      "input keys sum")),
    ("proof", "Proof matches the payer's keys, this address and this memo",
     ("share ", "DLEQ proof", "a receiver proof must have")),
    ("output", "Rebuilt output key is in the transaction",
     ("the transaction does not pay", "the receipt's listed outputs")),
    ("ownersig", "Receiver signed with the output key (receiver proofs only)", ("output ",)),
    ("conf", "Transaction is confirmed", ("transaction has",)),
]


@dataclass
class Coin:
    outpoint: OutPoint
    value: int
    key: Scalar

    @property
    def script_pubkey(self) -> bytes:
        return p2wpkh_script(hash160((self.key * G).to_bytes_compressed()))


class Wallet:
    """Alice's coins: plain P2WPKH outputs she holds the keys for."""

    def __init__(self, chain: SimChain):
        self.chain = chain
        self.coins: list[Coin] = []

    def receive(self, value: int) -> Coin:
        key = random_scalar()
        coin = Coin(OutPoint(bytes(32), 0), value, key)
        coin.outpoint = self.chain.faucet(coin.script_pubkey, value)
        self.coins.append(coin)
        return coin

    def balance(self) -> int:
        return sum(c.value for c in self.coins)

    def select(self, amount: int) -> tuple[list[Coin], Coin]:
        """Coins (oldest first) covering amount + fee, and the change coin to create."""
        need = amount + FEE_SAT
        while self.balance() < need:
            self.receive(50_000_000)
        chosen, total = [], 0
        for coin in self.coins:
            chosen.append(coin)
            total += coin.value
            if total >= need:
                break
        return chosen, Coin(OutPoint(bytes(32), 0), total - need, random_scalar())

    def send(self, tx: Tx, chosen: list[Coin], change: Coin) -> str:
        """Add change, sign every input, broadcast and update the coin list."""
        if change.value:
            tx.vout.append(TxOut(change.value, change.script_pubkey))
        for n, coin in enumerate(chosen):
            sign_p2wpkh(tx, n, coin.key, coin.value)
        txid = self.chain.broadcast(tx)
        self.coins = [c for c in self.coins if not any(c is x for x in chosen)]
        if change.value:
            change.outpoint = OutPoint(tx.txid, len(tx.vout) - 1)
            self.coins.append(change)
        return txid


class DemoSession:
    def __init__(self, shop_name: str = "Dana's Phone Shop"):
        self.chain = SimChain()
        self.shop_name = shop_name
        self.b_scan, self.b_spend = random_scalar(), random_scalar()
        self.alice = Wallet(self.chain)
        self.alice.receive(25_000_000)
        self.alice.receive(20_000_000)
        self.next_order = 1001
        self.orders: dict[int, dict] = {}
        self.payer_keys: dict[str, list[Scalar]] = {}

    # ------------------------------------------------------------- views

    def _address_of(self, spk: bytes) -> str:
        if is_p2tr(spk):
            return encode_p2tr_address(spk[2:], NET)
        if is_p2wpkh(spk):
            return bech32m.encode(SEGWIT_HRP[NET], 0, spk[2:])
        return spk.hex()

    def tx_view(self, txid: str) -> dict:
        tx = self.chain.get_tx(txid)
        inputs = []
        for txin in tx.vin:
            prev = self.chain.get_tx(txin.prevout.txid[::-1].hex())
            out = prev.vout[txin.prevout.vout]
            inputs.append({"outpoint": str(txin.prevout), "amount_sat": out.value,
                           "address": self._address_of(out.script_pubkey)})
        return {
            "txid": txid,
            "height": self.chain.height_of.get(txid),
            "confirmations": self.chain.confirmations(txid),
            "inputs": inputs,
            "outputs": [{"vout": n, "amount_sat": o.value, "address": self._address_of(o.script_pubkey),
                         "type": "taproot" if is_p2tr(o.script_pubkey) else "segwit v0",
                         "key": o.script_pubkey[2:].hex()} for n, o in enumerate(tx.vout)],
            "raw_hex": tx.serialize().hex(),
        }

    def state(self) -> dict:
        return {"shop_name": self.shop_name, "height": self.chain.tip, "alice_balance_sat": self.alice.balance(),
                "orders": list(self.orders.values())}

    # ------------------------------------------------------------- the story

    def create_invoice(self, item: str = "Phone", amount_sat: int = 30_000_000) -> dict:
        if not 10_000 <= amount_sat <= 100_000_000:
            raise ValueError("amount must be between 10,000 sat and 1 BTC")
        order = self.next_order
        self.next_order += 1
        B_scan, B_m = self.b_scan * G, labeled_spend_key(self.b_scan, self.b_spend * G, order)
        invoice = {"order": order, "memo": f"Order #{order}", "item": item[:60], "amount_sat": amount_sat,
                   "address": encode_sp_address(B_scan, B_m, NET), "label": order,
                   "math": {"B_scan": B_scan.to_bytes_compressed().hex(), "B_m": B_m.to_bytes_compressed().hex()},
                   "txid": None}
        self.orders[order] = invoice
        return invoice

    def _order(self, order: int) -> dict:
        if order not in self.orders:
            raise ValueError(f"no order #{order}")
        return self.orders[order]

    def pay(self, order: int) -> dict:
        inv = self._order(order)
        if inv["txid"]:
            raise ValueError(f"order #{order} is already paid")
        _, B_scan, B_m = decode_sp_address(inv["address"])
        chosen, change = self.alice.select(inv["amount_sat"])
        xonly = sender_output_keys([(c.key, False) for c in chosen], [c.outpoint for c in chosen], [(B_scan, B_m)])[0]
        tx = Tx(vin=[TxIn(c.outpoint) for c in chosen], vout=[TxOut(inv["amount_sat"], p2tr_script(xonly))])
        txid = self.alice.send(tx, chosen, change)
        self.chain.mine()
        inv["txid"] = txid
        self.payer_keys[txid] = [c.key for c in chosen]
        return {"invoice": inv, "tx": self.tx_view(txid)}

    def shop_scan(self, order: int) -> dict:
        """What the shop's own silent-payment wallet sees (it can find the payment)."""
        inv = self._order(order)
        if not inv["txid"]:
            return {"found": []}
        tx, spks, _ = resolve(self.chain, inv["txid"])
        found = scan(self.b_scan, self.b_spend * G, list(self.orders), tx, spks)
        return {"found": [{"vout": m.vout, "amount_sat": tx.vout[m.vout].value, "order": m.label} for m in found]}

    def make_receipt(self, order: int) -> dict:
        inv = self._order(order)
        if not inv["txid"]:
            raise ValueError(f"order #{order} is not paid yet")
        tx, spks, _ = resolve(self.chain, inv["txid"])
        receipt = make_sender_receipt(tx, spks, inv["address"], inv["memo"], self.payer_keys[inv["txid"]], NET,
                                      make_bundle(self.chain, inv["txid"]))
        d = receipt.to_dict()
        core = {k: d[k] for k in ("spreceipt", "role", "network", "txid", "address", "memo", "shares")}
        return {"receipt": d, "proof_bytes": 33 + 64, "json_bytes_without_bundle": len(json.dumps(core, separators=(",", ":")))}

    def verify(self, receipt: dict, offline: bool = False) -> dict:
        """Check a receipt against this chain (or, offline, against the transactions bundled in it)."""
        try:
            r = Receipt.from_dict(receipt)
        except ReceiptFormatError as e:
            return _result(False, f"not a valid receipt: {e}", None, receipt)
        try:
            source = BundleSource(r.bundle) if offline else self.chain
            tx, spks, confirmations = resolve(source, r.txid)
        except (ChainError, SourceError, KeyError, ValueError) as e:
            return _result(False, f"fetch: {e}", r, receipt)
        res = verify_receipt(r, tx, spks, confirmations)
        out = _result(res.valid, res.reason, r, receipt, confirmations)
        out["paid_outputs"] = [dict(o, key=tx.vout[o["vout"]].script_pubkey[2:].hex()) for o in res.paid_outputs]
        out["total_sat"] = res.total_sat
        out["warnings"] = res.warnings
        return out

    # ------------------------------------------------------------- cheats

    def cheat_reuse(self, receipt: dict, memo: str = "Order #2002") -> dict:
        bad = copy.deepcopy(receipt)
        bad["memo"] = memo
        return {"changed": f"memo: {receipt['memo']!r} → {memo!r}", "result": self.verify(bad)}

    def cheat_tamper(self, receipt: dict) -> dict:
        bad = copy.deepcopy(receipt)
        proof = bad["shares"][0]["proof"]
        flipped = proof[:-1] + ("0" if proof[-1] != "0" else "1")
        bad["shares"][0]["proof"] = flipped
        return {"changed": f"last character of the proof: …{proof[-6:]} → …{flipped[-6:]}", "result": self.verify(bad)}

    def cheat_backdoor(self, order: int, amount_sat: int = 10_000_000) -> dict:
        """Pay an output that passes a bare 'P = B_m + t*G' check but hides a script path Alice can spend."""
        inv = self._order(order)
        _, _, B_m = decode_sp_address(inv["address"])
        while True:
            r = random_scalar()
            Q = B_m + r * G
            if Q.has_even_y():
                break
        chosen, change = self.alice.select(amount_sat)
        alice_key = chosen[0].key
        leaf = tagged_hash("TapLeaf", b"\xc0" + ser_bytes(b"\x20" + (alice_key * G).to_bytes_xonly() + b"\xac"))
        h = Scalar.from_bytes_checked(tagged_hash("TapTweak", Q.to_bytes_xonly() + leaf))
        P = Q + h * G
        tx = Tx(vin=[TxIn(c.outpoint) for c in chosen], vout=[TxOut(amount_sat, p2tr_script(P.to_bytes_xonly()))])
        txid = self.alice.send(tx, chosen, change)
        self.chain.mine()
        tweak = r + h
        tweak_check = (B_m + tweak * G).to_bytes_xonly() == P.to_bytes_xonly()
        ftx, spks, _ = resolve(self.chain, txid)
        shop_finds = bool(scan(self.b_scan, self.b_spend * G, list(self.orders), ftx, spks))
        try:
            make_sender_receipt(ftx, spks, inv["address"], inv["memo"], [c.key for c in chosen], NET)
            receipt_error = None
        except ReceiptError as e:
            receipt_error = str(e)
        return {"tx": self.tx_view(txid), "tweak": tweak.to_bytes().hex(), "tweak_check_passes": tweak_check,
                "shop_wallet_finds_it": shop_finds, "receipt_made": receipt_error is None, "receipt_error": receipt_error}

    # ------------------------------------------------------------- extras

    def receiver_proof(self, order: int, memo: str = "Income statement 2026") -> dict:
        inv = self._order(order)
        if not inv["txid"]:
            raise ValueError(f"order #{order} is not paid yet")
        tx, spks, _ = resolve(self.chain, inv["txid"])
        proof = make_receiver_proof(tx, spks, self.b_scan, self.b_spend, order, memo, NET,
                                    make_bundle(self.chain, inv["txid"]))
        d = proof.to_dict()
        return {"proof": d, "result": self.verify(d)}

    def verify_sample(self, name: str = "sender-receipt") -> dict:
        """Check a receipt made on a real Bitcoin Core regtest chain (demo/samples), from its bundle."""
        if name not in ("sender-receipt", "receiver-proof"):
            raise ValueError("unknown sample")
        receipt = json.loads((SAMPLES / f"{name}.json").read_text())
        return {"receipt": receipt, "result": self.verify(receipt, offline=True)}


def _result(valid: bool, reason: str, r: Receipt | None, raw: dict, confirmations: int | None = None) -> dict:
    """A verify result plus the checklist the page shows, marking where it stopped."""
    role = r.role if r else raw.get("role")
    failed_at = None
    if not valid:
        failed_at = next((i for i, (_, _, prefixes) in enumerate(CHECKS)
                          if any(reason.startswith(p) for p in prefixes)), 0)
    checks = []
    for i, (key, label, _) in enumerate(CHECKS):
        if key == "ownersig" and role != "receiver":
            continue
        status = "pass" if failed_at is None or i < failed_at else ("fail" if i == failed_at else "skip")
        if key == "conf" and status == "pass" and confirmations is None:
            status = "unchecked"
        checks.append({"key": key, "label": label, "status": status})
    return {
        "valid": valid,
        "reason": reason[len("fetch: "):] if reason.startswith("fetch: ") else reason,
        "role": role,
        "memo": raw.get("memo"),
        "txid": raw.get("txid"),
        "address": raw.get("address"),
        "confirmations": confirmations,
        "checks": checks,
        "paid_outputs": [],
        "total_sat": 0,
        "warnings": [],
    }
