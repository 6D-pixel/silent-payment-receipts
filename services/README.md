# Checkout services (signet)

A shop shows **one static silent-payment address**. These services add what a shop
and a marketplace need around it:

| Service | Port | Holds | Does |
|---|---|---|---|
| `sppay`, the shop's payment method | 8401 | watch-only keys (`b_scan`, `B_spend`) | Makes an invoice per order, shows the pay page, scans signet and marks invoices paid, and sends signed webhooks |
| `market`, the marketplace | 8402 | no keys, no money | Records each order's terms at checkout, takes the buyer's receipt, and rules on "you never paid me" with `spreceipt.policy` |
| `wallet`, the demo buyer's wallet | 8403 | the buyer's P2WPKH keys | Already signed in for the live page: pays an order, tells sppay the txid, and hands the receipt to the marketplace |
| `buyer`, the same wallet on the command line | — | the buyer's P2WPKH keys | Orders, pays, and hands the receipt to the marketplace |

**One transaction pays one order.** The payer can sign receipts with different memos
for the same payment, so:
- the buyer's wallet tells sppay which transaction paid which invoice, and sppay never
  lets one transaction pay a second invoice;
- the marketplace accepts each outpoint for one order only: the first valid receipt
  for a payment claims it, even while it is still in the mempool;
- the marketplace passes the buyer's receipt to the shop, which checks it on its own;
- the payment must confirm after the order was made, so an old payment can't be reused.

## Run on public signet

```sh
/opt/homebrew/bin/python3.14 -m venv .venv
.venv/bin/pip install -r services/requirements.txt
.venv/bin/python -m services.sppay      # first run makes the demo shop's keys in services/.data/shop
.venv/bin/python -m services.market     # in a second terminal

.venv/bin/python -m services.wallet     # in a third terminal: the demo buyer's wallet
(cd web && npm run dev)                 # then open http://localhost:5173/live.html

.venv/bin/python -m services.buyer address        # fund this address from a signet faucet
.venv/bin/python -m services.buyer balance
.venv/bin/python -m services.buyer buy "Phone" 30000
curl -X POST 127.0.0.1:8402/api/orders/<order id>/dispute   # the shop says "never paid": rule on it
```

The chain is mempool.space's signet API by default. Set `SPPAY_ESPLORA` and
`MARKET_ESPLORA` to point the services at another Esplora server.

## Tests

`.venv/bin/python -m unittest tests.test_services` runs both services and the buyer
wallet against an in-memory chain with real signed transactions.
