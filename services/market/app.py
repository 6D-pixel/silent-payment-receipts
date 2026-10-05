"""The marketplace: orders, the buyer's receipts, and settling "you never paid me".

It holds no money and no keys. At checkout it asks the shop's payment method
(sppay) for an invoice and records its terms. The buyer hands over a receipt.
If the shop says it was never paid, the marketplace checks that receipt against
the chain with spreceipt.policy and rules on it.

One transaction pays one order. Here, each outpoint can settle one order only.
Elsewhere, the shop can show a second receipt the buyer signed for the same
payment, naming another order; that proves the payment was claimed twice.

Run:  .venv/bin/python -m services.market
"""

import hmac
import json
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from dataclasses import asdict

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from spreceipt import Receipt, ReceiptFormatError
from spreceipt.bip352 import decode_sp_address
from spreceipt.policy import ChainStatus, OrderTerms, Verdict, check_double_claim, check_payment
from spreceipt.sources import SourceError, resolve

from ..sppay.scanner import sign_body
from .config import Config, Shop

SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
    id TEXT PRIMARY KEY,
    shop_id TEXT NOT NULL,
    item TEXT NOT NULL,
    terms TEXT NOT NULL,          -- OrderTerms as JSON
    invoice_id TEXT NOT NULL,
    pay_url TEXT NOT NULL,
    shop_status TEXT NOT NULL,    -- what the shop's payment method last reported
    receipt TEXT,
    verdict TEXT,
    conflict TEXT,                -- proof that the buyer claimed this payment for another order
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS claims (
    txid TEXT NOT NULL,
    vout INTEGER NOT NULL,
    order_id TEXT NOT NULL REFERENCES orders(id),
    PRIMARY KEY (txid, vout)
);
"""


class NewOrder(BaseModel):
    shop_id: str
    item: str = Field(max_length=200)
    price_sat: int = Field(gt=0)


class SubmitReceipt(BaseModel):
    receipt: dict


def same_keys(a: str, b: str) -> bool:
    try:
        _, s1, m1 = decode_sp_address(a)
        _, s2, m2 = decode_sp_address(b)
    except ValueError:
        return False
    return (s1.to_bytes_compressed(), m1.to_bytes_compressed()) == (s2.to_bytes_compressed(), m2.to_bytes_compressed())


def call_sppay(shop: Shop, method: str, path: str, body: dict | None = None) -> dict:
    req = urllib.request.Request(shop.sppay_url.rstrip("/") + path, json.dumps(body).encode() if body else None,
                                 {"Content-Type": "application/json", "Authorization": f"Bearer {shop.api_key}"},
                                 method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        raise HTTPException(502, f"shop's payment method said: {e.read().decode()[:300]}")
    except urllib.error.URLError as e:
        raise HTTPException(502, f"shop's payment method is unreachable: {e}")


def create_app(cfg: Config, chain, sppay=call_sppay) -> FastAPI:
    db = sqlite3.connect(cfg.db_path, check_same_thread=False, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    lock = threading.Lock()
    shops = {s.id: s for s in cfg.shops}

    app = FastAPI(title="marketplace")
    app.add_middleware(CORSMiddleware, allow_origins=cfg.cors_origins, allow_methods=["*"], allow_headers=["*"])

    def load(order_id: str) -> dict:
        row = db.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "no such order")
        d = dict(row)
        d["terms"] = json.loads(d["terms"])
        d["receipt"] = json.loads(d["receipt"]) if d["receipt"] else None
        d["verdict"] = json.loads(d["verdict"]) if d["verdict"] else None
        d["conflict"] = json.loads(d["conflict"]) if d["conflict"] else None
        return d

    def view(o: dict) -> dict:
        t = o["terms"]
        return {"id": o["id"], "shop": shops[o["shop_id"]].name, "item": o["item"],
                "address": t["shop_address"], "amount_sat": t["amount_sat"], "memo": t["memo"],
                "network": t["network"], "pay_by_height": t["expires_height"], "invoice_id": o["invoice_id"],
                "pay_url": o["pay_url"], "shop_status": o["shop_status"], "has_receipt": o["receipt"] is not None,
                "verdict": o["verdict"]}

    def evaluate(o: dict) -> Verdict:
        """Check the order's receipt against our own view of the chain."""
        if o["receipt"] is None:
            return Verdict(False, "no_receipt", "the buyer has not handed over a receipt")
        if o["conflict"]:
            return Verdict(False, "claimed_twice", o["conflict"]["reason"])
        receipt = Receipt.from_dict(o["receipt"])
        try:
            tx, spks, confs = resolve(chain, receipt.txid)
            height = chain.block_height(receipt.txid)
        except SourceError as e:
            return Verdict(False, "tx_not_found", f"could not fetch the transaction: {e}")
        verdict = check_payment(receipt, tx, spks, OrderTerms(**o["terms"]), ChainStatus(confs, height),
                                min_conf=cfg.min_conf)
        if verdict.ok:
            with lock:
                for txid, vout in verdict.outpoints:
                    row = db.execute("SELECT order_id FROM claims WHERE txid=? AND vout=?", (txid, vout)).fetchone()
                    if row and row["order_id"] != o["id"]:
                        return Verdict(False, "outpoint_taken", f"this payment already settled order {row['order_id']}")
                for txid, vout in verdict.outpoints:
                    db.execute("INSERT OR IGNORE INTO claims VALUES (?,?,?)", (txid, vout, o["id"]))
        return verdict

    def save_verdict(o: dict, v: Verdict) -> dict:
        d = asdict(v) | {"checked_at": time.time()}
        db.execute("UPDATE orders SET verdict=? WHERE id=?", (json.dumps(d), o["id"]))
        return d

    @app.get("/api/shops")
    def list_shops():
        return [{"id": s.id, "name": s.name, "address": s.address} for s in cfg.shops]

    @app.post("/api/orders", status_code=201)
    def new_order(req: NewOrder):
        shop = shops.get(req.shop_id)
        if shop is None:
            raise HTTPException(404, "no such shop")
        try:
            tip = chain.tip_height()
        except SourceError as e:
            raise HTTPException(503, f"chain unavailable: {e}")
        with lock:
            n = db.execute("SELECT COUNT(*) FROM orders").fetchone()[0] + 1
        order_id = f"{n}-{int(time.time()) % 100000}"
        inv = sppay(shop, "POST", "/api/invoices",
                    {"marketplace": cfg.marketplace_id, "order_id": order_id, "price_sat": req.price_sat})
        # The invoice must pay the address the shop listed, for this order, at least the price.
        if not same_keys(inv["address"], shop.address):
            raise HTTPException(502, "the shop's payment method returned an address the shop did not list")
        if inv["memo"] != f"spr1:{cfg.marketplace_id}:{order_id}" or inv["amount_sat"] != req.price_sat:
            raise HTTPException(502, "the shop's payment method returned different invoice terms")
        # The payment window starts at our own tip, so the shop cannot widen it to fit an old payment.
        terms = OrderTerms(shop.address, inv["memo"], inv["amount_sat"], cfg.network,
                           created_height=tip, expires_height=inv["expires_height"])
        db.execute("INSERT INTO orders VALUES (?,?,?,?,?,?,?,NULL,NULL,NULL,?)",
                   (order_id, shop.id, req.item, json.dumps(asdict(terms)), inv["id"], inv["pay_url"],
                    inv["status"], time.time()))
        return view(load(order_id))

    @app.get("/api/orders/{order_id}")
    def get_order(order_id: str):
        return view(load(order_id))

    @app.post("/api/orders/{order_id}/receipt")
    def submit_receipt(order_id: str, req: SubmitReceipt):
        o = load(order_id)
        try:
            Receipt.from_dict(req.receipt)
        except ReceiptFormatError as e:
            raise HTTPException(422, f"not a receipt: {e}")
        db.execute("UPDATE orders SET receipt=? WHERE id=?", (json.dumps(req.receipt), order_id))
        o["receipt"] = req.receipt
        return {"order": view(load(order_id)), "verdict": save_verdict(o, evaluate(o))}

    @app.post("/api/orders/{order_id}/dispute")
    def dispute(order_id: str):
        """The shop says it was never paid. Re-check the buyer's receipt now and rule."""
        o = load(order_id)
        v = save_verdict(o, evaluate(o))
        ruling = "paid" if v["ok"] else ("waiting" if v["code"] == "unconfirmed" else "not_paid")
        return {"order": view(load(order_id)), "ruling": ruling, "verdict": v}

    @app.post("/api/orders/{order_id}/conflict")
    def conflict(order_id: str, req: SubmitReceipt):
        """The shop shows a receipt the buyer signed for the same payment but another order."""
        o = load(order_id)
        if o["receipt"] is None:
            raise HTTPException(409, "the buyer has not handed over a receipt for this order")
        try:
            mine, other = Receipt.from_dict(o["receipt"]), Receipt.from_dict(req.receipt)
            tx, spks, _ = resolve(chain, mine.txid)
        except ReceiptFormatError as e:
            raise HTTPException(422, f"not a receipt: {e}")
        except SourceError as e:
            raise HTTPException(503, f"could not fetch the transaction: {e}")
        v = check_double_claim(mine, other, tx, spks, cfg.network)
        if v.ok:
            db.execute("UPDATE orders SET conflict=? WHERE id=?",
                       (json.dumps(asdict(v) | {"other_receipt": req.receipt}), order_id))
            with lock:
                db.execute("DELETE FROM claims WHERE order_id=?", (order_id,))
            o = load(order_id)
            save_verdict(o, evaluate(o))
        return {"order": view(load(order_id)), "proven": v.ok, "reason": v.reason}

    @app.post("/webhooks/sppay")
    async def sppay_webhook(request: Request, sppay_sig: str = Header("")):
        body = await request.body()
        event = json.loads(body)
        inv = event.get("invoice", {})
        row = db.execute("SELECT id, shop_id FROM orders WHERE invoice_id=?", (inv.get("id"),)).fetchone()
        if row is None:
            raise HTTPException(404, "unknown invoice")
        if not hmac.compare_digest(sppay_sig, sign_body(shops[row["shop_id"]].webhook_secret, body)):
            raise HTTPException(401, "bad signature")
        db.execute("UPDATE orders SET shop_status=? WHERE id=?", (inv["status"], row["id"]))
        return {"ok": True}

    return app
