"""The official BIP-374 DLEQ test vectors, run against spreceipt.crypto."""

import csv
import pathlib
import unittest

from spreceipt.crypto import GE, dleq_generate_proof, dleq_verify_proof

DATA = pathlib.Path(__file__).parent / "data"


def point(h: str) -> GE:
    return GE() if h == "INFINITY" else GE.from_bytes(bytes.fromhex(h))


class BIP374Vectors(unittest.TestCase):
    def test_generate(self):
        with open(DATA / "test_vectors_generate_proof.csv", newline="") as f:
            rows = list(csv.reader(f))[1:]
        for index, G_hex, a_hex, B_hex, aux_hex, msg_hex, result, comment in rows:
            with self.subTest(index=index, comment=comment):
                msg = bytes.fromhex(msg_hex) or None
                got = dleq_generate_proof(int(a_hex, 16), point(B_hex), bytes.fromhex(aux_hex), G=point(G_hex), m=msg)
                self.assertEqual(got, None if result == "INVALID" else bytes.fromhex(result))

    def test_verify(self):
        with open(DATA / "test_vectors_verify_proof.csv", newline="") as f:
            rows = list(csv.reader(f))[1:]
        for index, G_hex, A_hex, B_hex, C_hex, proof_hex, msg_hex, result, comment in rows:
            with self.subTest(index=index, comment=comment):
                msg = bytes.fromhex(msg_hex) or None
                got = dleq_verify_proof(point(A_hex), point(B_hex), point(C_hex), bytes.fromhex(proof_hex),
                                        G=point(G_hex), m=msg)
                self.assertEqual(got, result == "TRUE")


if __name__ == "__main__":
    unittest.main()
