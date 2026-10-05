import uvicorn

from ..chain import EsploraChain
from .app import create_app
from .config import esplora_url, from_env

uvicorn.run(create_app(from_env(), EsploraChain(esplora_url())), host="127.0.0.1", port=8402)
