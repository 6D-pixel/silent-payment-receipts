"""Marketplace rules: a receipt must prove that this order, not just this address, was paid."""

import copy
import unittest

from spreceipt import make_sender_receipt
from spreceipt.crypto import G, random_scalar
from spreceipt.policy import ChainStatus, OrderTerms, check_double_claim, check_payment
from spreceipt.tx import TxOut, p2tr_script

from tests.helpers import Receiver, p2wpkh_input, pay

NET = "regtest"
CONFIRMED = ChainStatus(confirmations=3, block_height=105)


class Policy(unittest.TestCase):
    def setUp(self):
        self.shop = Receiver.new()
        self.address = self.shop.address()  # one static address, no labels
        self.keys = [random_scalar()]
        self.inputs = [(*p2wpkh_input(d, n), d, False) for n, d in enumerate(self.keys)]
        self.tx, self.spks = pay(self.inputs, [(self.shop, None)], amount=150_000)
        self.order = OrderTerms(self.address, "spr1:mkt:77", 150_000, NET, created_height=100, expires_height=110)
        self.receipt = make_sender_receipt(self.tx, self.spks, self.address, self.order.memo, self.keys, NET)

    def check(self, receipt=None, order=None, status=CONFIRMED, tx=None, spks=None):
        return check_payment(receipt or self.receipt, tx or self.tx, spks or self.spks,
                             order or self.order, status)

    def test_ok(self):
        v = self.check()
        self.assertTrue(v.ok, v.reason)
        self.assertEqual(v.outpoints, [(self.tx.txid_hex, 0)])
        self.assertEqual(v.amount_sat, 150_000)

    def test_other_shop(self):
        other = copy.replace(self.order, shop_address=Receiver.new().address())
        self.assertEqual(self.check(order=other).code, "wrong_address")

    def test_other_order(self):
        other = copy.replace(self.order, memo="spr1:mkt:78")
        self.assertEqual(self.check(order=other).code, "wrong_memo")

    def test_double_claim_is_proven_by_the_two_receipts(self):
        # The payer signs a second receipt for the same payment, naming another order.
        # Each verifies on its own; together they prove the payment was claimed twice.
        other = make_sender_receipt(self.tx, self.spks, self.address, "spr1:other-mkt:9", self.keys, NET)
        self.assertTrue(self.check(receipt=other, order=copy.replace(self.order, memo="spr1:other-mkt:9")).ok)
        v = check_double_claim(self.receipt, other, self.tx, self.spks, NET)
        self.assertTrue(v.ok, v.reason)
        self.assertEqual((v.code, v.outpoints), ("claimed_twice", [(self.tx.txid_hex, 0)]))

    def test_double_claim_needs_two_valid_receipts_with_different_orders(self):
        same = make_sender_receipt(self.tx, self.spks, self.address, self.order.memo, self.keys, NET)
        self.assertFalse(check_double_claim(self.receipt, same, self.tx, self.spks, NET).ok)
        forged = copy.deepcopy(self.receipt)
        forged.memo = "spr1:other-mkt:9"  # edited, not signed by the payer
        self.assertFalse(check_double_claim(self.receipt, forged, self.tx, self.spks, NET).ok)

    def test_tampered_proof(self):
        bad = copy.deepcopy(self.receipt)
        bad.shares[0].proof = bytes(64)
        self.assertEqual(self.check(receipt=bad).code, "bad_proof")

    def test_unknown_or_missing_confirmations(self):
        for status in (ChainStatus(None, None), ChainStatus(0, None), ChainStatus(2, None)):
            self.assertEqual(self.check(status=status).code, "unconfirmed")

    def test_paid_outside_invoice_window(self):
        self.assertEqual(self.check(status=ChainStatus(9, 100)).code, "paid_before_invoice")
        self.assertEqual(self.check(status=ChainStatus(1, 111)).code, "paid_after_expiry")

    def test_amount(self):
        self.assertEqual(self.check(order=copy.replace(self.order, amount_sat=150_001)).code, "underpaid")
        self.assertTrue(self.check(order=copy.replace(self.order, amount_sat=149_999)).ok)  # overpaid is fine

    def test_receipt_cannot_pick_the_network(self):
        # test, signet and regtest share the tsp prefix: the order's network wins.
        r = copy.replace(self.receipt, network="signet")
        self.assertTrue(self.check(receipt=r).ok)
        self.assertEqual(self.check(order=copy.replace(self.order, network="main")).code, "bad_proof")

    def test_listed_amount_is_ignored(self):
        r = copy.replace(self.receipt, outputs=[{"vout": 0, "k": 0, "amount_sat": 10**9}])
        v = self.check(receipt=r)
        self.assertTrue(v.ok, v.reason)
        self.assertEqual(v.amount_sat, 150_000)

    def test_k_gap(self):
        # Two outputs to the shop (k = 0, 1), then k = 0 swapped for a stranger's
        # key: the receipt verifies, but the shop's wallet would never find k = 1.
        tx, spks = pay(self.inputs, [(self.shop, None), (self.shop, None)], amount=75_000)
        tx.vout[0] = TxOut(75_000, p2tr_script((random_scalar() * G).to_bytes_xonly()))
        r = make_sender_receipt(tx, spks, self.address, self.order.memo, self.keys, NET)
        order = copy.replace(self.order, amount_sat=75_000)
        self.assertEqual(self.check(receipt=r, order=order, tx=tx, spks=spks).code, "k_gap")


if __name__ == "__main__":
    unittest.main()
