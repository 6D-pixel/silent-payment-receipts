# Checkout services (signet)

A shop shows **one static silent-payment address**. These services add what a shop
and a marketplace need around it:

| Service | Port | Holds | Does |
|---|---|---|---|
| `sppay`, the shop's payment method | 8401 | watch-only keys (`b_scan`, `B_spend`) | Makes an invoice per order for an exact amount, shows the pay page, scans signet and marks invoices paid, and sends signed webhooks |
| `market`, the marketplace | 8402 | no keys, no money | Records each order's terms at checkout, takes the buyer's receipt, and rules on "you never paid me" with `spreceipt.policy` |
| `buyer`, a command-line wallet | — | the buyer's P2WPKH keys | Orders, pays, and hands the receipt to the marketplace |

The memo can't tie a payment to an order, since the payer can sign any memo for the
same output. Instead, the order is bound by:
- the invoice's exact amount: the price plus 1–999 sat, which no other open invoice of the shop has;
- the blocks in which the invoice was open.

The marketplace also accepts each outpoint for one order only.

## Run on public signet

```sh
/opt/homebrew/bin/python3.14 -m venv .venv
.venv/bin/pip install -r services/requirements.txt
.venv/bin/python -m services.sppay      # first run makes the demo shop's keys in services/.data/shop
.venv/bin/python -m services.market     # in a second terminal

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
