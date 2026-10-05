"""Key files for the demo shop.

The shop's wallet keeps b_spend. sppay gets a watch-only file with b_scan and
B_spend: enough to find payments, not to spend them.
"""

import json
import os
import pathlib
from dataclasses import dataclass

from spreceipt.bip352 import encode_sp_address
from spreceipt.crypto import G, GE, Scalar, random_scalar


@dataclass
class WatchKeys:
    b_scan: Scalar
    B_spend: GE

    @property
    def B_scan(self) -> GE:
        return self.b_scan * G

    def address(self, network: str) -> str:
        return encode_sp_address(self.B_scan, self.B_spend, network)

    @classmethod
    def load(cls, path: str | os.PathLike) -> "WatchKeys":
        d = json.loads(pathlib.Path(path).read_text())
        return cls(Scalar.from_bytes_checked(bytes.fromhex(d["b_scan"])),
                   GE.from_bytes_compressed(bytes.fromhex(d["B_spend"])))


def _write_secret(path: pathlib.Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")
    path.chmod(0o600)


def new_shop(directory: str | os.PathLike, network: str) -> str:
    """Write wallet.json (spend key) and sppay-keys.json (watch-only); return the address."""
    d = pathlib.Path(directory)
    b_scan, b_spend = random_scalar(), random_scalar()
    keys = WatchKeys(b_scan, b_spend * G)
    address = keys.address(network)
    _write_secret(d / "wallet.json", {"b_scan": b_scan.to_bytes().hex(), "b_spend": b_spend.to_bytes().hex(),
                                      "address": address})
    _write_secret(d / "sppay-keys.json", {"b_scan": b_scan.to_bytes().hex(),
                                          "B_spend": keys.B_spend.to_bytes_compressed().hex(), "address": address})
    return address
