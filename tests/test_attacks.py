"""Receipts must fail whenever a claim is false. Each test is one way to cheat."""

import copy
import json
import os
import unittest

from spreceipt import (
    Receipt, ReceiptError, ReceiptFormatError, combine, make_receiver_proof,
    make_sender_receipt, verify_receipt,
)
from spreceipt.crypto import G, Scalar, dleq_generate_proof, random_scalar, tagged_hash
from spreceipt.receipt import Share, receipt_message
from spreceipt.sources import BundleSource, SourceError, resolve
from spreceipt.tx import OutPoint, Tx, TxIn, TxOut, p2tr_script, ser_bytes

from tests.helpers import Receiver, p2tr_input, p2wpkh_input, pay

NET = "regtest"


class Scenario(unittest.TestCase):
    def setUp(self):
        self.shop = Receiver.new()
        self.alice = [random_scalar(), random_scalar()]
        self.inputs = [(*p2wpkh_input(d, n), d, False) for n, d in enumerate(self.alice)]
        self.label = 1001  # one labelled address per invoice
        self.tx, self.spks = pay(self.inputs, [(self.shop, self.label)])
        self.address = self.shop.address(self.label)
        self.receipt = make_sender_receipt(self.tx, self.spks, self.address, "Order #1001", self.alice, NET)

    def check(self, receipt, tx=None, spks=None, **kw):
        return verify_receipt(receipt, tx or self.tx, spks or self.spks, **kw)


class HonestReceipts(Scenario):
    def test_valid(self):
        r = self.check(self.receipt)
        self.assertTrue(r.valid, r.reason)
        self.assertEqual([o["amount_sat"] for o in r.paid_outputs], [150_000])

    def test_json_round_trip(self):
        again = Receipt.from_json(self.receipt.to_json())
        self.assertTrue(self.check(again).valid)

    def test_taproot_input_with_odd_y(self):
        while True:  # a key whose point has odd y, so BIP-352 negates it
            d = random_scalar()
            if not (d * G).has_even_y():
                break
        inputs = [(*p2tr_input(d, 0), d, True), self.inputs[0]]
        tx, spks = pay(inputs, [(self.shop, None)])
        receipt = make_sender_receipt(tx, spks, self.shop.address(), "memo", [d, self.alice[0]], NET)
        self.assertTrue(verify_receipt(receipt, tx, spks).valid)

    def test_receiver_proof(self):
        proof = make_receiver_proof(self.tx, self.spks, self.shop.b_scan, self.shop.b_spend,
                                    self.label, "Income statement 2026", NET)
        r = self.check(proof)
        self.assertTrue(r.valid, r.reason)
        self.assertEqual(r.receipt.address, self.address)

    def test_confirmations(self):
        self.assertTrue(self.check(self.receipt, confirmations=3).valid)
        unchecked = self.check(self.receipt)
        self.assertTrue(unchecked.valid)
        self.assertTrue(unchecked.warnings)


class Cheats(Scenario):
    def test_changed_memo(self):
        bad = copy.deepcopy(self.receipt)
        bad.memo = "Order #9999"  # reuse one payment for another invoice
        self.assertFalse(self.check(bad).valid)

    def test_other_label_of_same_receiver(self):
        bad = copy.deepcopy(self.receipt)
        bad.address = self.shop.address(2002)  # same scan key, other invoice
        self.assertFalse(self.check(bad).valid)

    def test_other_receiver(self):
        bad = copy.deepcopy(self.receipt)
        bad.address = Receiver.new().address()
        self.assertFalse(self.check(bad).valid)

    def test_tampered_share_or_proof(self):
        bad = copy.deepcopy(self.receipt)
        bad.shares[0].share = bad.shares[0].share + G
        self.assertFalse(self.check(bad).valid)
        bad = copy.deepcopy(self.receipt)
        p = bytearray(bad.shares[0].proof)
        p[40] ^= 1
        bad.shares[0].proof = bytes(p)
        self.assertFalse(self.check(bad).valid)

    def test_replaced_transaction(self):
        # Same inputs, same payment, different change: an RBF replacement.
        tx2, spks2 = pay(self.inputs, [(self.shop, self.label)], change=30_000)
        self.assertEqual(tx2.vout[0].script_pubkey, self.tx.vout[0].script_pubkey)
        self.assertNotEqual(tx2.txid, self.tx.txid)
        bad = copy.deepcopy(self.receipt)
        bad.txid = tx2.txid_hex  # the message binds the txid, so this fails
        self.assertFalse(verify_receipt(bad, tx2, spks2).valid)

    def test_unconfirmed(self):
        r = self.check(self.receipt, confirmations=0)
        self.assertFalse(r.valid)

    def test_hidden_script_path(self):
        """The fake that a bare 'P = B_m + t*G' check (BIP-352 out-of-band notice) accepts.

        Alice builds an output that looks like a payment to the shop, but hides a
        taproot script path that lets her take the money back.
        """
        B_m = self.shop.B_m(self.label)
        while True:
            r = random_scalar()
            Q = B_m + r * G
            if Q.has_even_y():
                break
        alice_xonly = (self.alice[0] * G).to_bytes_xonly()
        leaf = tagged_hash("TapLeaf", b"\xc0" + ser_bytes(b"\x20" + alice_xonly + b"\xac"))
        h = Scalar.from_bytes_checked(tagged_hash("TapTweak", Q.to_bytes_xonly() + leaf))
        P = Q + h * G
        fake_tx = Tx(vin=[i[0] for i in self.inputs], vout=[TxOut(150_000, p2tr_script(P.to_bytes_xonly()))])

        t_claimed = r + h
        bare_check = (B_m + t_claimed * G).to_bytes_xonly() == P.to_bytes_xonly()
        self.assertTrue(bare_check, "the bare tweak check is fooled")

        with self.assertRaises(ReceiptError):  # an honest receipt cannot be made
            make_sender_receipt(fake_tx, self.spks, self.address, "Order #1001", self.alice, NET)
        forged = copy.deepcopy(self.receipt)  # nor can the real one be moved over
        forged.txid = fake_tx.txid_hex
        self.assertFalse(verify_receipt(forged, fake_tx, self.spks).valid)

    def test_forged_share_without_keys(self):
        # Mallory has no input keys and proves a share for a secret of her own.
        x = random_scalar()
        B_scan = self.shop.b_scan * G
        m = receipt_message("sender", self.tx.txid, B_scan, self.shop.B_m(self.label), "Order #1001")
        forged = copy.deepcopy(self.receipt)
        forged.shares = [Share([0, 1], x * B_scan, dleq_generate_proof(int(x), B_scan, os.urandom(32), m=m))]
        self.assertFalse(self.check(forged).valid)

    def test_role_swap(self):
        proof = make_receiver_proof(self.tx, self.spks, self.shop.b_scan, self.shop.b_spend,
                                    self.label, "Order #1001", NET)
        as_sender = copy.deepcopy(proof)
        as_sender.role = "sender"  # the shop pretending the customer made this claim
        self.assertFalse(self.check(as_sender).valid)
        as_receiver = copy.deepcopy(self.receipt)
        as_receiver.role = "receiver"
        self.assertFalse(self.check(as_receiver).valid)

    def test_scan_key_alone_cannot_prove_ownership(self):
        # A scanning server knows b_scan, so it could make the DLEQ part, but it
        # does not know b_spend and cannot sign with the output key.
        proof = make_receiver_proof(self.tx, self.spks, self.shop.b_scan, self.shop.b_spend,
                                    self.label, "Income", NET)
        no_sigs = copy.deepcopy(proof)
        no_sigs.output_sigs = {}
        self.assertFalse(self.check(no_sigs).valid)
        wrong = copy.deepcopy(proof)
        wrong.output_sigs = {v: bytes(64) for v in wrong.output_sigs}
        self.assertFalse(self.check(wrong).valid)

    def test_fake_funding_transaction_in_bundle(self):
        # A bundle whose funding tx was edited to change what an input spent.
        coinbase_like = [TxIn(OutPoint(bytes(32), 0))]
        funding = Tx(vin=coinbase_like, vout=[TxOut(100_000, self.spks[0])])
        self.tx.vin[0].prevout.txid = funding.txid
        self.tx.vin[0].prevout.vout = 0
        edited = Tx(vin=coinbase_like, vout=[TxOut(100_000, p2tr_script(bytes(32)))])
        bundle = {"tx": self.tx.serialize().hex(), "prevout_txs": [edited.serialize().hex()]}
        with self.assertRaises(SourceError):
            resolve(BundleSource(bundle), self.tx.txid_hex)


class MultiParty(Scenario):
    def test_coinjoin_style_combine(self):
        # Alice owns input 0, Bob owns input 1; both are needed for the shared secret.
        a_part = make_sender_receipt(self.tx, self.spks, self.address, "Order #1001", [self.alice[0]], NET)
        b_part = make_sender_receipt(self.tx, self.spks, self.address, "Order #1001", [self.alice[1]], NET)
        partial = self.check(a_part)
        self.assertFalse(partial.valid)
        self.assertIn("incomplete", partial.reason)
        merged = combine([a_part, b_part], self.tx, self.spks)
        r = self.check(merged)
        self.assertTrue(r.valid, r.reason)
        with self.assertRaises(ReceiptError):
            combine([a_part, a_part])


class Format(Scenario):
    def test_rejects_malformed_receipts(self):
        for bad in ['{}', '{"spreceipt": 1}', 'not json',
                    '{"spreceipt":0,"role":"sender","network":"regtest","txid":"00","address":"x","memo":"","shares":[]}']:
            with self.assertRaises(ReceiptFormatError):
                Receipt.from_json(bad)
        good = json.loads(self.receipt.to_json())
        for key, value in [("outputs", [{"k": 0}]), ("role", "arbiter"), ("memo", "x" * 2000)]:
            with self.assertRaises(ReceiptFormatError):
                Receipt.from_dict(dict(good, **{key: value}))


if __name__ == "__main__":
    unittest.main()
