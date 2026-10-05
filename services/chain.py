"""The chain as the services see it: an Esplora API (mempool.space signet by default).

Extends spreceipt's EsploraSource with what scanning and paying need: the tip,
blocks with their transactions and prevouts, address coins and broadcasting.
"""

import json
import urllib.error
import urllib.request

from spreceipt.sources import EsploraSource, SourceError
from spreceipt.tx import OutPoint, Tx, TxIn, TxOut, txid_to_bytes

PUBLIC_SIGNET = "https://mempool.space/signet/api"


def tx_from_esplora(d: dict) -> tuple[Tx, list[bytes]]:
    """Rebuild a transaction and its prevout scriptPubKeys from Esplora's JSON.

    The rebuilt transaction must hash to the txid Esplora gave, so a field that
    was dropped or misread cannot slip through.
    """
    tx = Tx(version=d["version"], locktime=d["locktime"])
    spks = []
    for i in d["vin"]:
        if i.get("is_coinbase"):
            raise SourceError("coinbase transaction")
        tx.vin.append(TxIn(OutPoint(txid_to_bytes(i["txid"]), i["vout"]), bytes.fromhex(i["scriptsig"]),
                           i["sequence"], [bytes.fromhex(w) for w in i.get("witness", [])]))
        spks.append(bytes.fromhex(i["prevout"]["scriptpubkey"]))
    for o in d["vout"]:
        tx.vout.append(TxOut(o["value"], bytes.fromhex(o["scriptpubkey"])))
    if tx.txid_hex != d["txid"]:
        raise SourceError(f"rebuilt transaction does not hash to {d['txid']}")
    return tx, spks


class EsploraChain(EsploraSource):
    def _json(self, path: str):
        return json.loads(self._get(path))

    def tip_height(self) -> int:
        return int(self._get("/blocks/tip/height"))

    def block_hash(self, height: int) -> str:
        return self._get(f"/block-height/{height}").strip()

    def block_txs(self, block_hash: str) -> list[tuple[Tx, list[bytes]]]:
        """Every non-coinbase transaction in the block that has a taproot output."""
        n = self._json(f"/block/{block_hash}")["tx_count"]
        found = []
        for start in range(0, n, 25):
            for d in self._json(f"/block/{block_hash}/txs/{start}"):
                if d["vin"][0].get("is_coinbase") or not any(o["scriptpubkey_type"] == "v1_p2tr" for o in d["vout"]):
                    continue
                found.append(tx_from_esplora(d))
        return found

    def tx_with_prevouts(self, txid: str) -> tuple[Tx, list[bytes]]:
        return tx_from_esplora(self._json(f"/tx/{txid}"))

    def utxos(self, address: str) -> list[dict]:
        return self._json(f"/address/{address}/utxo")

    def fee_rate(self) -> float:
        try:
            return float(self._json("/v1/fees/recommended")["halfHourFee"])
        except (SourceError, KeyError, ValueError):
            return 1.0

    def broadcast(self, raw_hex: str) -> str:
        req = urllib.request.Request(self.base + "/tx", raw_hex.encode(), {"Content-Type": "text/plain"})
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return resp.read().decode().strip()
        except urllib.error.HTTPError as e:
            raise SourceError(f"broadcast rejected: {e.read().decode().strip()}") from e
        except urllib.error.URLError as e:
            raise SourceError(f"broadcast failed: {e}") from e
