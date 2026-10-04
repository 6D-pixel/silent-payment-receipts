# Third-party code

Copied from the [bitcoin/bips](https://github.com/bitcoin/bips) repository at commit
`7c7cb232c228b258616ef64a3a079aa82996da8c`.

| Path | Source | Licence |
| --- | --- | --- |
| `spreceipt/_vendor/secp256k1lab/` | `bip-0352/secp256k1lab/src/secp256k1lab` (secp256k1lab) | MIT, see `COPYING` in that folder |
| `spreceipt/_vendor/bech32m.py` | `bip-0352/bech32m.py` (Pieter Wuille) | MIT, see file header |
| `spreceipt/_vendor/ripemd160.py` | `bip-0352/ripemd160.py` (Pieter Wuille) | MIT, see file header |
| `spreceipt/crypto.py` (DLEQ functions) | adapted from `bip-0374/reference.py` | BSD-2-Clause (BIP-374) |
| `spreceipt/bip352.py` (input rules) | adapted from `bip-0352/reference.py` | BSD-2-Clause (BIP-352) |
| `tests/data/bip352_send_and_receive_test_vectors.json` | `bip-0352/send_and_receive_test_vectors.json` | BSD-2-Clause (BIP-352) |
| `tests/data/test_vectors_*_proof.csv` | `bip-0374/test_vectors_*_proof.csv` | BSD-2-Clause (BIP-374) |

The rest of this project has no licence chosen yet.
