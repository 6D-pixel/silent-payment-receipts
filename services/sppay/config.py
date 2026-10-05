"""sppay settings, from environment variables (SPPAY_*) with demo defaults."""

import os
import pathlib
import secrets
from dataclasses import dataclass, field

from ..chain import PUBLIC_SIGNET
from ..keys import WatchKeys, new_shop

DATA = pathlib.Path(__file__).resolve().parent.parent / ".data"


@dataclass
class Config:
    keys: WatchKeys
    network: str = "signet"
    shop_name: str = "Dana's Phones"
    api_key: str = ""
    webhook_url: str | None = None
    webhook_secret: str | None = None
    db_path: str = str(DATA / "sppay.sqlite")
    public_url: str = "http://127.0.0.1:8401"
    expiry_blocks: int = 12
    start_height: int = 0
    scan_interval: float = 20.0
    cors_origins: list[str] = field(default_factory=lambda: ["*"])

    @property
    def address(self) -> str:
        return self.keys.address(self.network)


def _secret(path: pathlib.Path) -> str:
    """A random secret kept in a file, so it survives restarts."""
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(secrets.token_urlsafe(24))
        path.chmod(0o600)
    return path.read_text().strip()


def from_env() -> Config:
    env = os.environ.get
    network = env("SPPAY_NETWORK", "signet")
    keys_file = pathlib.Path(env("SPPAY_KEYS", DATA / "shop" / "sppay-keys.json"))
    if not keys_file.exists():
        new_shop(keys_file.parent, network)
    return Config(
        keys=WatchKeys.load(keys_file),
        network=network,
        shop_name=env("SPPAY_SHOP_NAME", "Dana's Phones"),
        api_key=env("SPPAY_API_KEY") or _secret(DATA / "shop" / "api-key"),
        webhook_url=env("SPPAY_WEBHOOK_URL", "http://127.0.0.1:8402/webhooks/sppay"),
        webhook_secret=env("SPPAY_WEBHOOK_SECRET") or _secret(DATA / "shop" / "webhook-secret"),
        db_path=env("SPPAY_DB", str(DATA / "sppay.sqlite")),
        public_url=env("SPPAY_PUBLIC_URL", "http://127.0.0.1:8401"),
        expiry_blocks=int(env("SPPAY_EXPIRY_BLOCKS", "12")),
        start_height=int(env("SPPAY_START_HEIGHT", "0")),
        scan_interval=float(env("SPPAY_SCAN_INTERVAL", "20")),
    )


def esplora_url() -> str:
    return os.environ.get("SPPAY_ESPLORA", PUBLIC_SIGNET)
