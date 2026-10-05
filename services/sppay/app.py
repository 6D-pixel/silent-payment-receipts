"""sppay: a silent-payment payment method for one shop.

The shop shows one static silent-payment address. For each order, sppay makes an
invoice, shows a pay page, and watches the chain with watch-only keys to mark the
invoice paid. The payer's wallet reports which transaction paid which invoice;
one transaction pays one invoice. A marketplace creates invoices with the shop's API key and gets
signed webhooks when they are paid.

Run:  .venv/bin/python -m services.sppay  (settings in services/sppay/config.py)
"""

import html
import hmac
import threading

import segno
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from spreceipt.sources import SourceError

from .config import Config
from .scanner import Scanner, Webhooks
from .store import Conflict, Store


class NewInvoice(BaseModel):
    marketplace: str = Field(pattern=r"^[a-z0-9-]{1,32}$")
    order_id: str = Field(pattern=r"^[A-Za-z0-9-]{1,64}$")
    price_sat: int = Field(gt=0, le=21_000_000 * 100_000_000)


class PaidWith(BaseModel):
    txid: str = Field(pattern=r"^[0-9a-f]{64}$")


def public(inv: dict, cfg: Config) -> dict:
    return {
        "id": inv["id"], "status": inv["status"], "address": cfg.address, "network": cfg.network,
        "memo": inv["memo"], "marketplace": inv["marketplace"], "order_id": inv["order_id"],
        "price_sat": inv["price_sat"], "amount_sat": inv["amount_sat"],
        "created_height": inv["created_height"], "expires_height": inv["expires_height"],
        "txid": inv["txid"], "vouts": inv["vouts"], "paid_height": inv["paid_height"],
        "pay_url": f"{cfg.public_url}/pay/{inv['id']}",
    }


def create_app(cfg: Config, chain, scan_in_background: bool = True) -> FastAPI:
    store = Store(cfg.db_path)
    # A fresh shop starts scanning at the current tip; after that, where it left off.
    start = cfg.start_height or (0 if store.get_state("scanned_height") else chain.tip_height())
    scanner = Scanner(store, chain, cfg.keys, Webhooks(cfg.webhook_url, cfg.webhook_secret), start)
    app = FastAPI(title="sppay")
    app.state.store, app.state.scanner = store, scanner
    app.add_middleware(CORSMiddleware, allow_origins=cfg.cors_origins, allow_methods=["*"], allow_headers=["*"])

    if scan_in_background:
        threading.Thread(target=scanner.run_forever, args=(cfg.scan_interval,), daemon=True).start()

    def merchant(authorization: str = Header("")) -> None:
        if not hmac.compare_digest(authorization, f"Bearer {cfg.api_key}"):
            raise HTTPException(401, "missing or wrong API key")

    def get(inv_id: str) -> dict:
        inv = store.invoice(inv_id)
        if inv is None:
            raise HTTPException(404, "no such invoice")
        return inv

    @app.get("/api/info")
    def info():
        return {"address": cfg.address, "network": cfg.network, "shop": cfg.shop_name,
                "scanned_height": int(store.get_state("scanned_height") or 0)}

    @app.post("/api/invoices", dependencies=[Depends(merchant)], status_code=201)
    def new_invoice(req: NewInvoice):
        try:
            tip = chain.tip_height()
        except SourceError as e:
            raise HTTPException(503, f"chain unavailable: {e}")
        try:
            inv = store.create_invoice(req.marketplace, req.order_id, req.price_sat, tip, tip + cfg.expiry_blocks)
        except Conflict as e:
            raise HTTPException(409, str(e))
        return public(inv, cfg)

    @app.get("/api/invoices/{inv_id}")
    def invoice(inv_id: str):
        return public(get(inv_id), cfg)

    @app.post("/api/invoices/{inv_id}/tx")
    def paid_with(inv_id: str, req: PaidWith):
        """The payer's wallet reports its txid so the invoice shows 'seen' before the block."""
        inv = get(inv_id)
        try:
            found = scanner.check_tx(req.txid, inv["id"])
        except SourceError as e:
            raise HTTPException(404, f"transaction not found: {e}")
        if found is None or found["id"] != inv["id"]:
            raise HTTPException(422, "this transaction does not pay this invoice (wrong amount, or it already paid another one)")
        return public(found, cfg)

    @app.get("/pay/{inv_id}", response_class=HTMLResponse)
    def pay_page(inv_id: str):
        return pay_html(public(get(inv_id), cfg), cfg.shop_name)

    return app


def pay_html(inv: dict, shop: str) -> str:
    e = html.escape
    btc = f"{inv['amount_sat'] / 1e8:.8f}"
    qr = segno.make(inv["address"], error="m").svg_inline(scale=5, dark="#000", light=None)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Pay {e(shop)}</title>
<style>
  :root {{ --ink:#1d1b16; --paper:#fbfaf6; --line:#d9d4c7; --ok:#1f7a4d; --wait:#8a5a00; }}
  @media (prefers-color-scheme: dark) {{ :root {{ --ink:#ece8de; --paper:#191814; --line:#3a372f; --ok:#5fc48f; --wait:#e0a943; }} }}
  body {{ margin:0; background:var(--paper); color:var(--ink); font:16px/1.5 system-ui, sans-serif; }}
  main {{ max-width:30rem; margin:0 auto; padding:2rem 1rem; }}
  h1 {{ font-size:1.4rem; margin:0 0 .25rem; }}
  .amount {{ font-size:2rem; font-weight:700; font-variant-numeric:tabular-nums; margin:1rem 0 0; }}
  .note {{ color:var(--wait); margin:.25rem 0 1.5rem; }}
  .qr {{ display:flex; justify-content:center; margin:1rem 0; }}
  .qr svg {{ background:#fff; padding:12px; border-radius:6px; }}  /* scanners want dark on light */
  dl {{ display:grid; grid-template-columns:auto minmax(0,1fr); gap:.4rem 1rem; }}
  dt {{ font-weight:600; }} dd {{ margin:0; overflow-wrap:anywhere; font-family:ui-monospace, monospace; font-size:.9rem; }}
  #status {{ padding:.6rem .8rem; border:1px solid var(--line); border-radius:6px; }}
  #status[data-s=paid] {{ color:var(--ok); border-color:var(--ok); }}
</style></head>
<body><main>
  <h1>Pay {e(shop)}</h1>
  <div>Order {e(inv['order_id'])} on {e(inv['marketplace'])}</div>
  <p class="amount">{btc} BTC</p>
  <p class="note">Pay with a wallet that makes a receipt, and keep it: it proves you paid this order.</p>
  <div class="qr" aria-label="QR code of the shop's address">{qr}</div>
  <dl>
    <dt>Address</dt><dd>{e(inv['address'])}</dd>
    <dt>Amount</dt><dd>{inv['amount_sat']:,} sat</dd>
    <dt>Memo</dt><dd>{e(inv['memo'])}</dd>
    <dt>Pay by</dt><dd>block {inv['expires_height']:,}</dd>
  </dl>
  <p id="status" data-s="{e(inv['status'])}" role="status">Status: {e(inv['status'])}</p>
</main>
<script>
  const label = {{open:"Waiting for payment", seen:"Payment seen, waiting for a block", paid:"Paid", expired:"Expired"}};
  const el = document.getElementById("status");
  async function tick() {{
    try {{
      const inv = await (await fetch("/api/invoices/{e(inv['id'])}")).json();
      el.dataset.s = inv.status;
      el.textContent = "Status: " + (label[inv.status] || inv.status) + (inv.txid ? " (" + inv.txid.slice(0, 12) + "…)" : "");
      if (inv.status === "paid" || inv.status === "expired") return;
    }} catch (_) {{}}
    setTimeout(tick, 5000);
  }}
  tick();
</script>
</body></html>"""
