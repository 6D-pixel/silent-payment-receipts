"""The shop's payment method (sppay) and the marketplace together, on an in-memory chain.

Payments are real signed transactions from the buyer wallet; only the network is fake.
Needs the service dependencies: .venv/bin/python -m unittest tests.test_services
"""

import json
import os
import struct
import tempfile
import unittest

try:
    from fastapi.testclient import TestClient
except ImportError:  # the core tests run without the service dependencies
    TestClient = None

from spreceipt.crypto import G, random_scalar
from spreceipt.sources import SourceError
from spreceipt.tx import OutPoint, Tx, TxIn, TxOut, is_p2tr, txid_to_hex
from webdemo.wallet import check_p2wpkh


class FakeChain:
    """Just enough of services.chain.EsploraChain, with blocks mined on demand."""

    def __init__(self, height: int = 1000):
        self.height = height
        self.txs: dict[str, Tx] = {}
        self.heights: dict[str, int | None] = {}
        self.blocks: dict[int, list[str]] = {}

    def _add(self, tx: Tx) -> str:
        self.txs[tx.txid_hex] = tx
        self.heights[tx.txid_hex] = None
        return tx.txid_hex

    def faucet(self, script_pubkey: bytes, value: int) -> None:
        n = len(self.txs)
        self._add(Tx(vin=[TxIn(OutPoint(struct.pack("<Q", n) * 4, 0))], vout=[TxOut(value, script_pubkey)]))
        self.mine()

    def mine(self) -> int:
        self.height += 1
        pending = [t for t, h in self.heights.items() if h is None]
        for t in pending:
            self.heights[t] = self.height
        self.blocks[self.height] = pending
        return self.height

    def _spks(self, tx: Tx) -> list[bytes]:
        return [self.txs[txid_to_hex(i.prevout.txid)].vout[i.prevout.vout].script_pubkey for i in tx.vin]

    # -- the EsploraChain interface
    def tip_height(self) -> int:
        return self.height

    def block_hash(self, height: int) -> str:
        return str(height)

    def block_txs(self, block_hash: str):
        txs = [self.txs[t] for t in self.blocks.get(int(block_hash), [])]
        return [(tx, self._spks(tx)) for tx in txs
                if any(is_p2tr(o.script_pubkey) for o in tx.vout) and txid_to_hex(tx.vin[0].prevout.txid) in self.txs]

    def get_tx(self, txid: str) -> Tx:
        if txid not in self.txs:
            raise SourceError(f"no transaction {txid}")
        return self.txs[txid]

    def tx_with_prevouts(self, txid: str):
        tx = self.get_tx(txid)
        return tx, self._spks(tx)

    def confirmations(self, txid: str) -> int:
        h = self.heights[txid]
        return 0 if h is None else self.height - h + 1

    def block_height(self, txid: str) -> int | None:
        return self.heights[txid]

    def utxos(self, address: str) -> list[dict]:
        from spreceipt._vendor import bech32m
        _, prog = bech32m.decode("tb", address)
        spk = bytes([0, 20]) + bytes(prog)
        spent = {(i.prevout.txid, i.prevout.vout) for tx in self.txs.values() for i in tx.vin}
        return [{"txid": t, "vout": n, "value": o.value}
                for t, tx in self.txs.items() for n, o in enumerate(tx.vout)
                if o.script_pubkey == spk and (tx.txid, n) not in spent]

    def fee_rate(self) -> float:
        return 2.0

    def broadcast(self, raw_hex: str) -> str:
        tx = Tx.from_hex(raw_hex)
        for n, i in enumerate(tx.vin):
            prev = self.txs[txid_to_hex(i.prevout.txid)].vout[i.prevout.vout]
            err = check_p2wpkh(tx, n, prev.script_pubkey, prev.value)
            if err:
                raise SourceError(f"input {n}: {err}")
        return self._add(tx)


@unittest.skipIf(TestClient is None, "service dependencies not installed (pip install fastapi httpx segno)")
class Checkout(unittest.TestCase):
    def setUp(self):
        from services.buyer import Wallet
        from services.keys import WatchKeys
        from services.market.app import create_app as market_app
        from services.market.config import Config as MarketConfig, Shop
        from services.sppay.app import create_app as sppay_app
        from services.sppay.config import Config as SppayConfig

        self.tmp = tempfile.TemporaryDirectory()
        d = self.tmp.name
        self.chain = FakeChain()
        self.keys = WatchKeys(random_scalar(), random_scalar() * G)
        cfg = SppayConfig(keys=self.keys, api_key="k", db_path=os.path.join(d, "sppay.sqlite"),
                          public_url="http://sppay", expiry_blocks=6)
        self.sppay = TestClient(sppay_app(cfg, self.chain, scan_in_background=False))
        self.scanner = self.sppay.app.state.scanner

        def call_sppay(shop, method, path, body=None):
            r = self.sppay.request(method, path, json=body, headers={"Authorization": f"Bearer {shop.api_key}"})
            r.raise_for_status()
            return r.json()

        shop = Shop("dana", "Dana's Phones", cfg.address, "http://sppay", "k", "secret")
        mcfg = MarketConfig(shops=[shop], db_path=os.path.join(d, "market.sqlite"))
        self.market = TestClient(market_app(mcfg, self.chain, sppay=call_sppay))

        self.buyer = Wallet([random_scalar()], "signet")
        self.chain.faucet(self.buyer.spk(self.buyer.keys[0]), 1_000_000)

    def tearDown(self):
        self.tmp.cleanup()

    def order(self, price=30_000) -> dict:
        r = self.market.post("/api/orders", json={"shop_id": "dana", "item": "Phone", "price_sat": price})
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()

    def pay(self, order, amount=None, memo=None):
        return self.buyer.pay(self.chain, order["address"], amount or order["amount_sat"], memo or order["memo"])

    def receipt(self, order, receipt) -> dict:
        return self.market.post(f"/api/orders/{order['id']}/receipt", json={"receipt": receipt.to_dict()}).json()

    def dispute(self, order) -> dict:
        return self.market.post(f"/api/orders/{order['id']}/dispute").json()

    def invoice(self, order) -> dict:
        return self.sppay.get(f"/api/invoices/{order['invoice_id']}").json()

    def test_paid_and_proven(self):
        o = self.order()
        self.assertEqual(o["address"], self.keys.address("signet"))
        self.assertTrue(30_001 <= o["amount_sat"] <= 30_999)

        txid, receipt = self.pay(o)
        r = self.sppay.post(f"/api/invoices/{o['invoice_id']}/tx", json={"txid": txid})
        self.assertEqual(r.json()["status"], "seen")
        self.assertEqual(self.receipt(o, receipt)["verdict"]["code"], "unconfirmed")
        self.assertEqual(self.dispute(o)["ruling"], "waiting")

        self.chain.mine()
        self.scanner.poll()
        inv = self.invoice(o)
        self.assertEqual((inv["status"], inv["txid"]), ("paid", txid))

        d = self.dispute(o)
        self.assertEqual(d["ruling"], "paid", d)
        self.assertEqual(d["verdict"]["amount_sat"], o["amount_sat"])

    def test_scanner_finds_payment_without_being_told(self):
        o = self.order()
        txid, _ = self.pay(o)
        self.chain.mine()
        self.scanner.poll()
        self.assertEqual(self.invoice(o)["status"], "paid")

    def test_never_paid(self):
        o = self.order()
        d = self.dispute(o)
        self.assertEqual((d["ruling"], d["verdict"]["code"]), ("not_paid", "no_receipt"))

    def test_one_payment_cannot_settle_two_orders(self):
        first, second = self.order(), self.order()
        self.assertNotEqual(first["amount_sat"], second["amount_sat"])
        txid, _ = self.pay(first)
        self.chain.mine()
        # The buyer signs a second receipt for the same payment, naming the other order.
        from spreceipt import make_sender_receipt
        tx, spks = self.chain.tx_with_prevouts(txid)
        keys = self.buyer.keys[:1]
        fake = make_sender_receipt(tx, spks, second["address"], second["memo"], keys, "signet")
        self.receipt(second, fake)
        self.assertEqual(self.dispute(second)["verdict"]["code"], "wrong_amount")

    def test_underpaid(self):
        o = self.order()
        txid, receipt = self.pay(o, amount=o["amount_sat"] - 1)
        self.chain.mine()
        self.scanner.poll()
        self.assertEqual(self.invoice(o)["status"], "open")
        self.receipt(o, receipt)
        self.assertEqual(self.dispute(o)["verdict"]["code"], "wrong_amount")

    def test_old_payment_is_not_accepted(self):
        early = self.order()
        txid, _ = self.pay(early)
        self.chain.mine()
        later = self.order(price=30_000)
        # Even if the amounts matched, a payment from before the invoice cannot count.
        from spreceipt import make_sender_receipt
        tx, spks = self.chain.tx_with_prevouts(txid)
        r = make_sender_receipt(tx, spks, later["address"], later["memo"], self.buyer.keys[:1], "signet")
        self.receipt(later, r)
        self.assertIn(self.dispute(later)["verdict"]["code"], ("paid_before_invoice", "wrong_amount"))

    def test_webhook_needs_the_shop_signature(self):
        from services.sppay.scanner import sign_body
        o = self.order()
        body = json.dumps({"type": "invoice.paid", "invoice": {"id": o["invoice_id"], "status": "paid"}}).encode()
        bad = self.market.post("/webhooks/sppay", content=body, headers={"SPPay-Sig": "sha256=00"})
        self.assertEqual(bad.status_code, 401)
        ok = self.market.post("/webhooks/sppay", content=body, headers={"SPPay-Sig": sign_body("secret", body)})
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(self.market.get(f"/api/orders/{o['id']}").json()["shop_status"], "paid")


if __name__ == "__main__":
    unittest.main()
