"""sppay's database: invoices, the payments its scanner found, and scan progress."""

import json
import secrets
import sqlite3
import threading
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS invoices (
    id TEXT PRIMARY KEY,
    marketplace TEXT NOT NULL,
    order_id TEXT NOT NULL,
    memo TEXT NOT NULL,
    price_sat INTEGER NOT NULL,
    amount_sat INTEGER NOT NULL,
    created_height INTEGER NOT NULL,
    expires_height INTEGER NOT NULL,
    status TEXT NOT NULL,             -- open, seen, paid, expired
    txid TEXT,
    vouts TEXT,
    paid_height INTEGER,
    receipt TEXT,                     -- the buyer's receipt, passed on by the marketplace
    receipt_check TEXT,               -- the shop's own check of it
    created_at REAL NOT NULL,
    UNIQUE (marketplace, order_id)
);
CREATE TABLE IF NOT EXISTS payments (
    txid TEXT NOT NULL,
    vout INTEGER NOT NULL,
    k INTEGER NOT NULL,
    amount_sat INTEGER NOT NULL,
    block_height INTEGER,
    invoice_id TEXT REFERENCES invoices(id),
    direct INTEGER NOT NULL DEFAULT 0,  -- the payer said it paid outside any invoice
    receipt TEXT,                     -- for a payment outside any invoice: the payer's receipt, if sent
    receipt_check TEXT,
    PRIMARY KEY (txid, vout)
);
CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

OPEN = ("open", "seen")


class Conflict(ValueError):
    pass


class Store:
    def __init__(self, path: str):
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        self.db.executescript(SCHEMA)

    # ------------------------------------------------------------ invoices

    def create_invoice(self, marketplace: str, order_id: str, price_sat: int,
                       created_height: int, expires_height: int) -> dict:
        memo = f"spr1:{marketplace}:{order_id}"
        with self.lock:
            if self.db.execute("SELECT 1 FROM invoices WHERE marketplace=? AND order_id=?",
                               (marketplace, order_id)).fetchone():
                raise Conflict(f"order {order_id} already has an invoice")
            amount = price_sat
            inv_id = secrets.token_urlsafe(12)
            self.db.execute(
                "INSERT INTO invoices (id, marketplace, order_id, memo, price_sat, amount_sat, created_height,"
                " expires_height, status, created_at) VALUES (?,?,?,?,?,?,?,?,'open',?)",
                (inv_id, marketplace, order_id, memo, price_sat, amount, created_height, expires_height, time.time()))
        return self.invoice(inv_id)

    def invoice(self, inv_id: str) -> dict | None:
        row = self.db.execute("SELECT * FROM invoices WHERE id=?", (inv_id,)).fetchone()
        if row is None:
            return None
        d = dict(row)
        d["vouts"] = json.loads(d["vouts"]) if d["vouts"] else []
        d["receipt"] = json.loads(d["receipt"]) if d["receipt"] else None
        d["receipt_check"] = json.loads(d["receipt_check"]) if d["receipt_check"] else None
        return d

    def save_receipt(self, inv_id: str, receipt: dict, check: dict) -> dict:
        with self.lock:
            self.db.execute("UPDATE invoices SET receipt=?, receipt_check=? WHERE id=?",
                            (json.dumps(receipt), json.dumps(check), inv_id))
        return self.invoice(inv_id)

    def match_open(self, amount_sat: int, height: int | None) -> dict | None:
        """For a payment nobody reported: the one open invoice it fits, if only one does.

        Usually the payer's wallet reports its txid for the invoice instead. When two
        open invoices have the same price, the payment stays unmatched.
        """
        q = f"SELECT id FROM invoices WHERE status IN {OPEN} AND amount_sat = ?"
        args: tuple = (amount_sat,)
        if height is not None:
            q += " AND created_height < ? AND expires_height >= ?"
            args += (height, height)
        rows = self.db.execute(q, args).fetchall()
        return self.invoice(rows[0]["id"]) if len(rows) == 1 else None

    def mark(self, inv_id: str, status: str, txid: str | None = None, vouts: list[int] | None = None,
             paid_height: int | None = None) -> dict:
        with self.lock:
            self.db.execute("UPDATE invoices SET status=?, txid=COALESCE(?, txid), vouts=COALESCE(?, vouts),"
                            " paid_height=COALESCE(?, paid_height) WHERE id=?",
                            (status, txid, json.dumps(vouts) if vouts is not None else None, paid_height, inv_id))
        return self.invoice(inv_id)

    def expire(self, height: int) -> list[dict]:
        rows = self.db.execute(f"SELECT id FROM invoices WHERE status IN {OPEN} AND expires_height < ?",
                               (height,)).fetchall()
        return [self.mark(r["id"], "expired") for r in rows]

    # ------------------------------------------------------------ payments

    def record_payment(self, txid: str, vout: int, k: int, amount_sat: int, block_height: int | None,
                       invoice_id: str | None, direct: bool = False) -> None:
        with self.lock:
            self.db.execute(
                "INSERT INTO payments (txid, vout, k, amount_sat, block_height, invoice_id, direct)"
                " VALUES (?,?,?,?,?,?,?) ON CONFLICT (txid, vout) DO UPDATE SET"
                " block_height=COALESCE(excluded.block_height, block_height),"
                " invoice_id=COALESCE(excluded.invoice_id, invoice_id), direct=MAX(direct, excluded.direct)",
                (txid, vout, k, amount_sat, block_height, invoice_id, int(direct)))

    def payments(self, txid: str | None = None) -> list[dict]:
        rows = (self.db.execute("SELECT * FROM payments WHERE txid=?", (txid,)) if txid else
                self.db.execute("SELECT * FROM payments ORDER BY block_height IS NOT NULL, block_height DESC"))
        out = []
        for r in rows:
            d = dict(r)
            d["receipt"] = json.loads(d["receipt"]) if d["receipt"] else None
            d["receipt_check"] = json.loads(d["receipt_check"]) if d["receipt_check"] else None
            out.append(d)
        return out

    def save_payment_receipt(self, txid: str, receipt: dict, check: dict) -> None:
        with self.lock:
            self.db.execute("UPDATE payments SET receipt=?, receipt_check=? WHERE txid=?",
                            (json.dumps(receipt), json.dumps(check), txid))

    # ------------------------------------------------------------ scan progress

    def get_state(self, key: str) -> str | None:
        row = self.db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    def set_state(self, key: str, value: str) -> None:
        with self.lock:
            self.db.execute("INSERT INTO state VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value=excluded.value",
                            (key, value))
