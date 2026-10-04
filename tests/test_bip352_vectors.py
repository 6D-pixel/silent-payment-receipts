"""Run the official BIP-352 test vectors through receipts.

Sending vectors: the sender makes a receipt for each recipient and it must verify.
Receiving vectors: our scan must find the expected outputs and tweaks, and the
receiver must be able to prove each one.
"""

from io import BytesIO
import json
import os
import pathlib
import unittest

from spreceipt import ReceiptError, make_receiver_proof, make_sender_receipt, verify_receipt
from spreceipt.bip352 import scan
from spreceipt.crypto import G, Scalar
from spreceipt.tx import OutPoint, Tx, TxIn, TxOut, deser_witness, p2tr_script, txid_to_bytes

DATA = pathlib.Path(__file__).parent / "data" / "bip352_send_and_receive_test_vectors.json"
VECTORS = json.loads(DATA.read_text())
SLOW = os.environ.get("SPRECEIPT_SLOW") == "1"


def build_tx(vin: list[dict], outputs_xonly: list[str]) -> tuple[Tx, list[bytes]]:
    tx = Tx()
    spks = []
    for i in vin:
        witness = deser_witness(BytesIO(bytes.fromhex(i["txinwitness"]))) if i["txinwitness"] else []
        tx.vin.append(TxIn(OutPoint(txid_to_bytes(i["txid"]), i["vout"]), bytes.fromhex(i["scriptSig"]), witness=witness))
        spks.append(bytes.fromhex(i["prevout"]["scriptPubKey"]["hex"]))
    for n, x in enumerate(outputs_xonly):
        tx.vout.append(TxOut(10_000 + n, p2tr_script(bytes.fromhex(x))))
    return tx, spks


def is_slow(case: dict) -> bool:
    return "K_max" in case["comment"]


class SendingVectors(unittest.TestCase):
    def test_sending(self):
        for case in VECTORS:
            if is_slow(case) and not SLOW:
                continue
            for test in case["sending"]:
                with self.subTest(case=case["comment"]):
                    given, expected = test["given"], test["expected"]
                    tx, spks = build_tx(given["vin"], expected["outputs"][0])
                    keys = [Scalar.from_bytes_checked(bytes.fromhex(i["private_key"])) for i in given["vin"]]
                    counts: dict[str, int] = {}
                    for r in given["recipients"]:
                        counts[r["address"]] = counts.get(r["address"], 0) + r.get("count", 1)
                    for address, count in counts.items():
                        if not expected["outputs"][0]:
                            with self.assertRaises(ReceiptError):
                                make_sender_receipt(tx, spks, address, "order 1", keys, "main")
                            continue
                        receipt = make_sender_receipt(tx, spks, address, "order 1", keys, "main")
                        result = verify_receipt(receipt, tx, spks)
                        self.assertTrue(result.valid, result.reason)
                        self.assertEqual(len(result.paid_outputs), count)


class ReceivingVectors(unittest.TestCase):
    def test_receiving(self):
        for case in VECTORS:
            if is_slow(case) and not SLOW:
                continue
            for test in case["receiving"]:
                with self.subTest(case=case["comment"]):
                    given, expected = test["given"], test["expected"]
                    tx, spks = build_tx(given["vin"], given["outputs"])
                    b_scan = Scalar.from_bytes_checked(bytes.fromhex(given["key_material"]["scan_priv_key"]))
                    b_spend = Scalar.from_bytes_checked(bytes.fromhex(given["key_material"]["spend_priv_key"]))
                    # 1. Our BIP-352 scan finds exactly the expected outputs and tweaks.
                    found = scan(b_scan, b_spend * G, given["labels"], tx, spks)
                    got = {tx.vout[m.vout].script_pubkey[2:].hex(): m.tweak.to_bytes().hex() for m in found}
                    if "outputs" in expected:
                        want = {o["pub_key"]: o["priv_key_tweak"] for o in expected["outputs"]}
                        self.assertEqual(got, want)
                    else:  # the K_max case lists only a count
                        self.assertEqual(len(got), expected["n_outputs"])
                        want = got

                    # 2. The receiver can prove every one of them, per address.
                    proven = set()
                    for label in [None, *given["labels"]]:
                        try:
                            proof = make_receiver_proof(tx, spks, b_scan, b_spend, label, "income 2026", "main")
                        except ReceiptError:
                            continue
                        result = verify_receipt(proof, tx, spks)
                        self.assertTrue(result.valid, result.reason)
                        proven |= {tx.vout[o["vout"]].script_pubkey[2:].hex() for o in result.paid_outputs}
                    self.assertEqual(proven, set(want))


if __name__ == "__main__":
    unittest.main()
