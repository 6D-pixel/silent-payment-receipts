"""The receipts saved by demo/regtest_demo.py still verify offline."""

import pathlib
import unittest

from spreceipt import Receipt, verify_receipt
from spreceipt.sources import BundleSource, resolve

SAMPLES = pathlib.Path(__file__).parent.parent / "demo" / "samples"


class Samples(unittest.TestCase):
    def test_samples_verify_offline(self):
        files = sorted(SAMPLES.glob("*.json"))
        self.assertTrue(files)
        for path in files:
            with self.subTest(path.name):
                receipt = Receipt.from_json(path.read_text())
                tx, spks, conf = resolve(BundleSource(receipt.bundle), receipt.txid)
                result = verify_receipt(receipt, tx, spks, conf)
                self.assertTrue(result.valid, result.reason)
                self.assertIsNone(result.confirmations)


if __name__ == "__main__":
    unittest.main()
