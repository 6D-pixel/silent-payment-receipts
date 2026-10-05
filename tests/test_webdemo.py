"""The browser demo story: honest flow, cheats, and the simulated chain's own rules."""

import json
import unittest

from spreceipt.crypto import G, random_scalar
from spreceipt.tx import OutPoint, Tx, TxIn, TxOut
from webdemo import DemoSession
from webdemo.bridge import call
from webdemo.simchain import ChainError
from webdemo.wallet import ecdsa_sign, ecdsa_verify


class Story(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s = DemoSession()
        cls.inv = cls.s.create_invoice("Phone", 30_000_000)
        cls.paid = cls.s.pay(cls.inv["order"])
        cls.receipt = cls.s.make_receipt(cls.inv["order"])["receipt"]

    def test_payment_matches_the_regtest_demo(self):
        tx = self.paid["tx"]
        self.assertEqual(len(tx["inputs"]), 2)
        self.assertEqual([o["amount_sat"] for o in tx["outputs"]], [30_000_000, 14_980_000])
        self.assertEqual(tx["confirmations"], 1)
        self.assertTrue(tx["outputs"][0]["address"].startswith("bcrt1p"))

    def test_shop_wallet_finds_the_payment(self):
        found = self.s.shop_scan(self.inv["order"])["found"]
        self.assertEqual(found, [{"vout": 0, "amount_sat": 30_000_000}])

    def test_receipt_verifies(self):
        res = self.s.verify(self.receipt)
        self.assertTrue(res["valid"], res["reason"])
        self.assertEqual(res["total_sat"], 30_000_000)
        self.assertTrue(all(c["status"] == "pass" for c in res["checks"]))

    def test_receipt_verifies_offline_from_its_bundle(self):
        res = self.s.verify(self.receipt, offline=True)
        self.assertTrue(res["valid"], res["reason"])
        self.assertEqual(res["checks"][-1]["status"], "unchecked")

    def test_reuse_for_another_order(self):
        res = self.s.cheat_reuse(self.receipt)["result"]
        self.assertFalse(res["valid"])
        self.assertEqual([c["status"] for c in res["checks"]], ["pass", "pass", "pass", "fail", "skip", "skip"])

    def test_tampered_proof(self):
        res = self.s.cheat_tamper(self.receipt)["result"]
        self.assertFalse(res["valid"])
        self.assertIn("DLEQ", res["reason"])

    def test_backdoor(self):
        out = self.s.cheat_backdoor(self.inv["order"])
        self.assertTrue(out["tweak_check_passes"])
        self.assertFalse(out["shop_wallet_finds_it"])
        self.assertFalse(out["receipt_made"])

    def test_receiver_proof(self):
        res = self.s.receiver_proof(self.inv["order"])["result"]
        self.assertTrue(res["valid"], res["reason"])
        self.assertIn("ownersig", [c["key"] for c in res["checks"]])

    def test_real_regtest_samples(self):
        for name in ("sender-receipt", "receiver-proof"):
            res = self.s.verify_sample(name)["result"]
            self.assertTrue(res["valid"], res["reason"])

    def test_unknown_txid(self):
        bad = dict(self.receipt, txid="11" * 32)
        res = self.s.verify(bad)
        self.assertFalse(res["valid"])
        self.assertEqual(res["checks"][0]["status"], "fail")


class Chain(unittest.TestCase):
    def test_rejects_bad_signature_and_double_spend(self):
        s = DemoSession()
        coin = s.alice.coins[0]
        tx = Tx(vin=[TxIn(coin.outpoint)], vout=[TxOut(1000, coin.script_pubkey)])
        tx.vin[0].witness = [bytes(71) + b"\x01", (coin.key * G).to_bytes_compressed()]
        with self.assertRaises(ChainError):
            s.chain.broadcast(tx)
        chosen, change = s.alice.select(1_000_000)
        s.alice.send(Tx(vin=[TxIn(c.outpoint) for c in chosen], vout=[TxOut(1_000_000, coin.script_pubkey)]), chosen, change)
        again = Tx(vin=[TxIn(chosen[0].outpoint)], vout=[TxOut(1000, coin.script_pubkey)])
        with self.assertRaises(ChainError):
            s.chain.broadcast(again)

    def test_ecdsa_round_trip(self):
        d, z = random_scalar(), bytes(range(32))
        sig = ecdsa_sign(d, z)
        self.assertTrue(ecdsa_verify(d * G, z, sig))
        self.assertFalse(ecdsa_verify(d * G, bytes(32), sig))


class Bridge(unittest.TestCase):
    def test_json_round_trip(self):
        call("reset")
        inv = json.loads(call("create_invoice", json.dumps({"item": "Phone", "amount_sat": 30_000_000})))
        self.assertTrue(inv["ok"])
        order = inv["result"]["order"]
        self.assertTrue(json.loads(call("pay", json.dumps({"order": order})))["ok"])
        receipt = json.loads(call("make_receipt", json.dumps({"order": order})))["result"]["receipt"]
        res = json.loads(call("verify", json.dumps({"receipt": receipt})))["result"]
        self.assertTrue(res["valid"])
        self.assertFalse(json.loads(call("nope"))["ok"])
        self.assertFalse(json.loads(call("pay", json.dumps({"order": 999})))["ok"])


if __name__ == "__main__":
    unittest.main()
