"""A command-line buyer wallet for signet: plain P2WPKH coins that pay silent-payment
invoices and make the receipt.

    .venv/bin/python -m services.buyer address        # where to send faucet coins
    .venv/bin/python -m services.buyer balance
    .venv/bin/python -m services.buyer buy "Phone" 30000     # order, pay, hand over the receipt

Every payment's change goes to a new key, since a receipt reveals the shared
secret for the keys that paid.
"""

import argparse
import json
import math
import pathlib
import sys
import urllib.error
import urllib.request

from spreceipt import make_sender_receipt
from spreceipt.bip352 import decode_sp_address, encode_p2wpkh_address, sender_output_keys
from spreceipt.crypto import G, Scalar, hash160, random_scalar
from spreceipt.tx import OutPoint, Tx, TxIn, TxOut, p2tr_script, p2wpkh_script, txid_to_bytes, txid_to_hex

from webdemo.wallet import sign_p2wpkh

from .chain import PUBLIC_SIGNET, EsploraChain

WALLET = pathlib.Path(__file__).resolve().parent / ".data" / "buyer" / "wallet.json"
# vbytes: P2WPKH input, P2TR output, P2WPKH output, and the fixed part of a segwit tx
VB_IN, VB_TR, VB_WPKH, VB_FIXED = 68, 43, 31, 11
DUST = 330


class WalletError(RuntimeError):
    pass


class Wallet:
    def __init__(self, keys: list[Scalar], network: str = "signet", path: pathlib.Path | None = None,
                 paid: dict[str, list[int]] | None = None):
        self.keys, self.network, self.path = keys, network, path
        self.paid = paid or {}  # txid -> indexes of the keys that paid it

    @classmethod
    def load(cls, path: pathlib.Path = WALLET, network: str = "signet") -> "Wallet":
        if not path.exists():
            w = cls([random_scalar()], network, path)
            w.save()
            return w
        d = json.loads(path.read_text())
        return cls([Scalar.from_bytes_checked(bytes.fromhex(k)) for k in d["keys"]], d["network"], path,
                   d.get("paid", {}))

    def save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"network": self.network, "keys": [k.to_bytes().hex() for k in self.keys],
                                         "paid": self.paid}))
        self.path.chmod(0o600)

    def spk(self, key: Scalar) -> bytes:
        return p2wpkh_script(hash160((key * G).to_bytes_compressed()))

    def address(self, key: Scalar | None = None) -> str:
        key = key or self.keys[0]
        return encode_p2wpkh_address(key * G, self.network)

    def coins(self, chain) -> list[tuple[OutPoint, int, Scalar]]:
        found = []
        for key in self.keys:
            for u in chain.utxos(self.address(key)):
                found.append((OutPoint(txid_to_bytes(u["txid"]), u["vout"]), u["value"], key))
        return found

    def pay(self, chain, address: str, amount_sat: int, memo: str, fee_rate: float | None = None):
        """Pay exactly amount_sat to a silent-payment address. Returns (txid, receipt)."""
        coins = sorted(self.coins(chain), key=lambda c: -c[1])
        rate = max(fee_rate or chain.fee_rate(), 1.0)
        chosen, total = [], 0
        for c in coins:
            chosen.append(c)
            total += c[1]
            fee = math.ceil(rate * (VB_FIXED + VB_IN * len(chosen) + VB_TR + VB_WPKH))
            if total >= amount_sat + fee:
                break
        else:
            raise WalletError(f"not enough coins: have {total:,} sat, need {amount_sat:,} plus fee")

        _, B_scan, B_m = decode_sp_address(address)
        tx = Tx(vin=[TxIn(op) for op, _, _ in chosen])
        keys = [(k, False) for _, _, k in chosen]
        [xonly] = sender_output_keys(keys, [i.prevout for i in tx.vin], [(B_scan, B_m)])
        tx.vout.append(TxOut(amount_sat, p2tr_script(xonly)))
        change = total - amount_sat - fee
        change_key = random_scalar()
        if change >= DUST:
            tx.vout.append(TxOut(change, self.spk(change_key)))
        for n, (_, value, key) in enumerate(chosen):
            sign_p2wpkh(tx, n, key, value)

        spks = [self.spk(k) for _, _, k in chosen]
        receipt = make_sender_receipt(tx, spks, address, memo, [k for _, _, k in chosen], self.network)
        receipt.bundle = {"tx": tx.serialize().hex(),
                          "prevout_txs": sorted({chain.get_tx(txid_to_hex(op.txid)).serialize().hex()
                                                 for op, _, _ in chosen})}
        txid = chain.broadcast(tx.serialize().hex())
        self.paid[txid] = [next(i for i, k in enumerate(self.keys) if k is key) for _, _, key in chosen]
        if change >= DUST:
            self.keys.append(change_key)
        self.save()
        return txid, receipt

    def receipt_for(self, chain, txid: str, address: str, memo: str):
        """Sign another receipt for a payment this wallet made (any memo: a receipt can't stop that)."""
        if txid not in self.paid:
            raise WalletError(f"this wallet did not make payment {txid}")
        tx, spks = chain.tx_with_prevouts(txid)
        return make_sender_receipt(tx, spks, address, memo, [self.keys[i] for i in self.paid[txid]], self.network)


def _http(method: str, url: str, body: dict | None = None) -> dict:
    req = urllib.request.Request(url, json.dumps(body).encode() if body is not None else None,
                                 {"Content-Type": "application/json"}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        raise WalletError(f"{method} {url}: {e.code} {e.read().decode()[:300]}") from e


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="services.buyer")
    p.add_argument("--esplora", default=PUBLIC_SIGNET)
    p.add_argument("--market", default="http://127.0.0.1:8402")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("address")
    sub.add_parser("balance")
    b = sub.add_parser("buy")
    b.add_argument("item")
    b.add_argument("price_sat", type=int)
    b.add_argument("--shop", default="dana")
    args = p.parse_args(argv)

    wallet, chain = Wallet.load(), EsploraChain(args.esplora)
    if args.cmd == "address":
        print(wallet.address())
    elif args.cmd == "balance":
        coins = wallet.coins(chain)
        print(f"{sum(v for _, v, _ in coins):,} sat in {len(coins)} coins")
    else:
        order = _http("POST", f"{args.market}/api/orders",
                      {"shop_id": args.shop, "item": args.item, "price_sat": args.price_sat})
        print(f"order {order['id']}: pay {order['amount_sat']:,} sat to {order['address']}")
        txid, receipt = wallet.pay(chain, order["address"], order["amount_sat"], order["memo"])
        print(f"paid: {txid}")
        sppay = order["pay_url"].split("/pay/")[0]
        try:
            _http("POST", f"{sppay}/api/invoices/{order['invoice_id']}/tx", {"txid": txid})
        except WalletError as e:
            print(f"could not tell the shop yet: {e}", file=sys.stderr)
        res = _http("POST", f"{args.market}/api/orders/{order['id']}/receipt", {"receipt": receipt.to_dict()})
        print(f"receipt handed to the marketplace: {res['verdict']['code']} ({res['verdict']['reason']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
