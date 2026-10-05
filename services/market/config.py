"""Marketplace settings, from environment variables (MARKET_*) with demo defaults.

The demo lists one shop: the sppay instance in services/sppay, using the
keys, API key and webhook secret it wrote to services/.data/shop.
"""

import json
import os
import pathlib
from dataclasses import dataclass, field

from ..chain import PUBLIC_SIGNET

DATA = pathlib.Path(__file__).resolve().parent.parent / ".data"


@dataclass
class Shop:
    id: str
    name: str
    address: str
    sppay_url: str
    api_key: str
    webhook_secret: str


@dataclass
class Config:
    shops: list[Shop]
    marketplace_id: str = "bazaar"
    network: str = "signet"
    db_path: str = str(DATA / "market.sqlite")
    min_conf: int = 1
    cors_origins: list[str] = field(default_factory=lambda: ["*"])


def demo_shop() -> Shop:
    d = DATA / "shop"
    keys = json.loads((d / "sppay-keys.json").read_text())
    return Shop("dana", os.environ.get("SPPAY_SHOP_NAME", "Dana's Phones"), keys["address"],
                os.environ.get("MARKET_SPPAY_URL", "http://127.0.0.1:8401"),
                (d / "api-key").read_text().strip(), (d / "webhook-secret").read_text().strip())


def from_env() -> Config:
    env = os.environ.get
    return Config(shops=[demo_shop()], marketplace_id=env("MARKET_ID", "bazaar"),
                  network=env("MARKET_NETWORK", "signet"), db_path=env("MARKET_DB", str(DATA / "market.sqlite")),
                  min_conf=int(env("MARKET_MIN_CONF", "1")))


def esplora_url() -> str:
    return os.environ.get("MARKET_ESPLORA", PUBLIC_SIGNET)
