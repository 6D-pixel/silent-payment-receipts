"""Where transactions come from: a receipt bundle, Bitcoin Core, or an Esplora server."""

import base64
import json
import urllib.error
import urllib.request

from .tx import Tx, txid_to_hex


class SourceError(RuntimeError):
    pass


class BundleSource:
    """Transactions carried inside the receipt. Cannot tell you about confirmations."""

    def __init__(self, bundle: dict):
        self.txs = {}
        for raw in [bundle["tx"], *bundle.get("prevout_txs", [])]:
            tx = Tx.from_hex(raw)
            self.txs[tx.txid_hex] = tx

    def get_tx(self, txid: str) -> Tx:
        if txid not in self.txs:
            raise SourceError(f"transaction {txid} is not in the bundle")
        return self.txs[txid]

    def confirmations(self, txid: str) -> int | None:
        return None

    def block_height(self, txid: str) -> int | None:
        return None


class CoreRPCSource:
    """Your own Bitcoin Core node (needs -txindex, or the transactions in its wallet)."""

    def __init__(self, url: str, user: str | None = None, password: str | None = None,
                 cookie_file: str | None = None):
        self.url = url
        if cookie_file:
            with open(cookie_file) as f:
                user, password = f.read().strip().split(":", 1)
        self.auth = base64.b64encode(f"{user}:{password}".encode()).decode() if user else None

    def call(self, method: str, *params):
        body = json.dumps({"jsonrpc": "1.0", "id": "spreceipt", "method": method, "params": list(params)})
        req = urllib.request.Request(self.url, body.encode(), {"Content-Type": "application/json"})
        if self.auth:
            req.add_header("Authorization", f"Basic {self.auth}")
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                reply = json.load(resp)
        except urllib.error.HTTPError as e:
            reply = json.load(e)
        if reply.get("error"):
            raise SourceError(f"{method}: {reply['error'].get('message')}")
        return reply["result"]

    def get_tx(self, txid: str) -> Tx:
        return Tx.from_hex(self.call("getrawtransaction", txid))

    def confirmations(self, txid: str) -> int | None:
        return self.call("getrawtransaction", txid, True).get("confirmations", 0)

    def block_height(self, txid: str) -> int | None:
        blockhash = self.call("getrawtransaction", txid, True).get("blockhash")
        return self.call("getblockheader", blockhash)["height"] if blockhash else None


class EsploraSource:
    """An Esplora HTTP API, e.g. your own electrs/esplora or a public one."""

    def __init__(self, base_url: str):
        self.base = base_url.rstrip("/")

    def _get(self, path: str) -> str:
        try:
            with urllib.request.urlopen(self.base + path, timeout=60) as resp:
                return resp.read().decode()
        except urllib.error.URLError as e:
            raise SourceError(f"GET {path}: {e}") from e

    def get_tx(self, txid: str) -> Tx:
        return Tx.from_hex(self._get(f"/tx/{txid}/hex"))

    def confirmations(self, txid: str) -> int | None:
        status = json.loads(self._get(f"/tx/{txid}/status"))
        if not status.get("confirmed"):
            return 0
        tip = int(self._get("/blocks/tip/height"))
        return tip - status["block_height"] + 1

    def block_height(self, txid: str) -> int | None:
        status = json.loads(self._get(f"/tx/{txid}/status"))
        return status["block_height"] if status.get("confirmed") else None


def resolve(source, txid: str) -> tuple[Tx, list[bytes], int | None]:
    """Fetch tx, the scriptPubKey of every output it spends, and its confirmations.

    Each funding transaction is hashed and must match the txid in the outpoint,
    so a bundle cannot lie about what an input spent.
    """
    tx = source.get_tx(txid)
    if tx.txid_hex != txid:
        raise SourceError("source returned a different transaction")
    spks, cache = [], {}
    for txin in tx.vin:
        prev_id = txid_to_hex(txin.prevout.txid)
        if prev_id not in cache:
            prev = source.get_tx(prev_id)
            if prev.txid != txin.prevout.txid:
                raise SourceError(f"funding transaction {prev_id} does not hash to its txid")
            cache[prev_id] = prev
        prev = cache[prev_id]
        if txin.prevout.vout >= len(prev.vout):
            raise SourceError(f"input spends missing output {prev_id}:{txin.prevout.vout}")
        spks.append(prev.vout[txin.prevout.vout].script_pubkey)
    return tx, spks, source.confirmations(txid)


def make_bundle(source, txid: str) -> dict:
    """Pack the transaction and its funding transactions into a receipt bundle."""
    tx = source.get_tx(txid)
    prev_ids = sorted({txid_to_hex(i.prevout.txid) for i in tx.vin})
    return {"tx": tx.serialize().hex(), "prevout_txs": [source.get_tx(p).serialize().hex() for p in prev_ids]}
