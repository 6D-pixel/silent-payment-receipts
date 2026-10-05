import os

import uvicorn

from ..buyer import Wallet
from ..chain import PUBLIC_SIGNET, EsploraChain
from .app import create_app

wallet = Wallet.load()
print(f"demo wallet: {wallet.address()}")
app = create_app(wallet, EsploraChain(os.environ.get("WALLET_ESPLORA", PUBLIC_SIGNET)),
                 os.environ.get("WALLET_MARKET_URL", "http://127.0.0.1:8402"))
uvicorn.run(app, host="127.0.0.1", port=8403)
