"""Finding the shop's payments on chain and matching them to invoices."""

import hashlib
import hmac
import json
import logging
import threading
import time
import urllib.request

from spreceipt.bip352 import scan
from spreceipt.sources import SourceError
from spreceipt.tx import Tx

from ..keys import WatchKeys
from .store import Store

log = logging.getLogger("sppay")


def sign_body(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


class Webhooks:
    """POST each invoice event to the shop's store, signed with HMAC-SHA256 (header SPPay-Sig)."""

    def __init__(self, url: str | None, secret: str | None):
        self.url, self.secret = url, secret

    def send(self, event: str, invoice: dict) -> None:
        if not self.url:
            return
        body = json.dumps({"type": event, "invoice": invoice}).encode()
        headers = {"Content-Type": "application/json", "SPPay-Sig": sign_body(self.secret or "", body)}
        threading.Thread(target=self._deliver, args=(body, headers), daemon=True).start()

    def _deliver(self, body: bytes, headers: dict) -> None:
        for attempt in range(4):
            try:
                with urllib.request.urlopen(urllib.request.Request(self.url, body, headers), timeout=10):
                    return
            except OSError as e:
                log.warning("webhook to %s failed (%s), attempt %d", self.url, e, attempt + 1)
                time.sleep(2 ** attempt)


class Scanner:
    def __init__(self, store: Store, chain, keys: WatchKeys, webhooks: Webhooks, start_height: int):
        self.store, self.chain, self.keys, self.webhooks = store, chain, keys, webhooks
        self.start_height = start_height

    def find(self, tx: Tx, spks: list[bytes]) -> list[dict]:
        """The outputs of tx that pay the shop."""
        return [{"vout": m.vout, "k": m.k, "amount_sat": tx.vout[m.vout].value}
                for m in scan(self.keys.b_scan, self.keys.B_spend, [], tx, spks)]

    def process(self, tx: Tx, spks: list[bytes], height: int | None, for_invoice: str | None = None) -> dict | None:
        """Record what tx pays the shop and settle the invoice it is for. Returns that invoice.

        for_invoice is the invoice the payer's wallet said it paid; otherwise the
        payment is matched only if exactly one open invoice fits it.
        """
        paid = self.find(tx, spks)
        if not paid:
            return None
        total = sum(o["amount_sat"] for o in paid)
        txid = tx.txid_hex
        known = [p for p in self.store.payments(txid) if p["invoice_id"]]
        if known:  # one transaction pays one invoice, whatever is claimed later
            inv = self.store.invoice(known[0]["invoice_id"])
        elif for_invoice:
            inv = self.store.invoice(for_invoice)
            if inv and (inv["status"] not in ("open", "seen") or total < inv["amount_sat"]):
                inv = None
        else:
            inv = self.store.match_open(total, height)
        if inv and height is not None and not (inv["created_height"] < height <= inv["expires_height"]):
            inv = None  # seen in time, but confirmed outside the invoice's window
        for o in paid:
            self.store.record_payment(txid, o["vout"], o["k"], o["amount_sat"], height, inv["id"] if inv else None)
        if inv is None:
            log.info("payment %s (%d sat) matches no open invoice", txid, total)
            return None
        if inv["status"] == "paid":
            return inv
        status = "paid" if height is not None else "seen"
        if status != inv["status"]:
            inv = self.store.mark(inv["id"], status, txid, [o["vout"] for o in paid], height)
            self.webhooks.send(f"invoice.{status}", inv)
        return inv

    def check_tx(self, txid: str, invoice_id: str) -> dict | None:
        """The payer says txid pays invoice_id: look at it now instead of waiting for the block."""
        tx, spks = self.chain.tx_with_prevouts(txid)
        return self.process(tx, spks, self.chain.block_height(txid), for_invoice=invoice_id)

    def poll(self) -> int:
        """Scan every block not yet scanned. Returns the tip height."""
        tip = self.chain.tip_height()
        done = int(self.store.get_state("scanned_height") or self.start_height - 1)
        for height in range(done + 1, tip + 1):
            for tx, spks in self.chain.block_txs(self.chain.block_hash(height)):
                self.process(tx, spks, height)
            self.store.set_state("scanned_height", str(height))
        for inv in self.store.expire(tip):
            self.webhooks.send("invoice.expired", inv)
        return tip

    def run_forever(self, interval: float) -> None:
        while True:
            try:
                self.poll()
            except (SourceError, OSError) as e:
                log.warning("scan failed: %s", e)
            time.sleep(interval)
