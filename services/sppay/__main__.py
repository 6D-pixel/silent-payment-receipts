import logging

import uvicorn

from ..chain import EsploraChain
from .app import create_app
from .config import esplora_url, from_env

logging.basicConfig(level=logging.INFO)
cfg = from_env()
print(f"sppay for {cfg.shop_name}: {cfg.address}")
uvicorn.run(create_app(cfg, EsploraChain(esplora_url())), host="127.0.0.1", port=8401)
