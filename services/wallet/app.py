"""The demo buyer's wallet as a small service, already signed in, so judges can pay
from the web page without setting anything up.

It holds a pre-funded signet wallet (services/.data/buyer/wallet.json). For each
payment it pays the order, tells the shop's payment method which transaction
paid it, and hands the receipt to the marketplace.

Run:  .venv/bin/python -m services.wallet
"""

import json
import threading
import urllib.error
import urllib.request

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from spreceipt.bip352 import SP_HRP, decode_sp_address
from spreceipt.sources import SourceError

from ..buyer import Wallet, WalletError


class PayOrder(BaseModel):
    order_id: str


class Reuse(BaseModel):
    txid: str
    order_id: str


class Send(BaseModel):
    address: str = Field(max_length=200)
    amount_sat: int = Field(ge=330, le=10_000_000)  # 330 sat is the smallest taproot output nodes relay
    memo: str = Field(min_length=1, max_length=200)


def http(method: str, url: str, body: dict | None = None) -> dict:
    req = urllib.request.Request(url, json.dumps(body).encode() if body is not None else None,
                                 {"Content-Type": "application/json"}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        raise HTTPException(502, f"{url} said: {e.read().decode()[:300]}")
    except urllib.error.URLError as e:
        raise HTTPException(502, f"{url} is unreachable: {e}")


def create_app(wallet: Wallet, chain, market_url: str, http=http) -> FastAPI:
    lock = threading.Lock()  # one payment at a time, so two judges never spend the same coin
    app = FastAPI(title="demo wallet")
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

    @app.get("/api/wallet")
    def info():
        try:
            coins = wallet.coins(chain)
        except SourceError as e:
            raise HTTPException(503, f"chain unavailable: {e}")
        return {"address": wallet.address(), "network": wallet.network,
                "balance_sat": sum(v for _, v, _ in coins), "coins": len(coins)}

    @app.post("/api/pay")
    def pay(req: PayOrder):
        order = http("GET", f"{market_url}/api/orders/{req.order_id}")
        if order["has_receipt"]:
            raise HTTPException(409, "this order is already paid")
        with lock:
            try:
                txid, receipt = wallet.pay(chain, order["address"], order["amount_sat"], order["memo"])
            except (WalletError, SourceError) as e:
                raise HTTPException(400, str(e))
        sppay = order["pay_url"].split("/pay/")[0]
        try:
            http("POST", f"{sppay}/api/invoices/{order['invoice_id']}/tx", {"txid": txid})
        except HTTPException:
            pass  # the shop's scanner will still find it in the block
        res = http("POST", f"{market_url}/api/orders/{req.order_id}/receipt", {"receipt": receipt.to_dict()})
        return {"txid": txid, "receipt": receipt.to_dict(), "order": res["order"], "verdict": res["verdict"]}

    @app.get("/api/tx/{txid}")
    def tx_status(txid: str):
        """Where a payment is now, from the wallet's view of the chain."""
        try:
            height = chain.block_height(txid)
            if height is None:
                chain.get_tx(txid)
                return {"state": "mempool", "block_height": None, "confirmations": 0}
            return {"state": "confirmed", "block_height": height, "confirmations": chain.tip_height() - height + 1}
        except SourceError:
            return {"state": "not_found", "block_height": None, "confirmations": 0}

    @app.post("/api/send")
    def send(req: Send):
        """Pay any silent-payment address and return the receipt. The page passes it on."""
        try:
            hrp, _, _ = decode_sp_address(req.address.strip())
        except ValueError as e:
            raise HTTPException(422, f"not a silent-payment address: {e}")
        if hrp != SP_HRP[wallet.network]:
            raise HTTPException(422, f"this is a {hrp} address; the demo wallet is on {wallet.network}")
        with lock:
            try:
                txid, receipt = wallet.pay(chain, req.address.strip(), req.amount_sat, req.memo)
            except (WalletError, SourceError) as e:
                raise HTTPException(400, str(e))
        return {"txid": txid, "receipt": receipt.to_dict()}

    @app.post("/api/reuse")
    def reuse(req: Reuse):
        """Cheat on purpose: sign a receipt naming another order for a payment already made."""
        order = http("GET", f"{market_url}/api/orders/{req.order_id}")
        try:
            receipt = wallet.receipt_for(chain, req.txid, order["address"], order["memo"])
        except (WalletError, SourceError) as e:
            raise HTTPException(400, str(e))
        res = http("POST", f"{market_url}/api/orders/{req.order_id}/receipt", {"receipt": receipt.to_dict()})
        return {"txid": req.txid, "receipt": receipt.to_dict(), "order": res["order"], "verdict": res["verdict"]}

    return app
