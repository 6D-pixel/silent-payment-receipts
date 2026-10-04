"""JSON in, JSON out: the one function the web page calls (through Pyodide or an HTTP helper)."""

import json

from .story import DemoSession

METHODS = {
    "state", "create_invoice", "pay", "tx_view", "shop_scan", "make_receipt", "verify",
    "cheat_reuse", "cheat_tamper", "cheat_backdoor", "receiver_proof", "verify_sample",
}

_session: DemoSession | None = None


def call(method: str, args_json: str = "{}") -> str:
    global _session
    try:
        args = json.loads(args_json or "{}")
        if method == "reset" or _session is None:
            _session = DemoSession()
        if method == "reset":
            result = _session.state()
        elif method in METHODS:
            result = getattr(_session, method)(**args)
        else:
            raise ValueError(f"unknown method {method!r}")
        return json.dumps({"ok": True, "result": result})
    except Exception as e:  # report every failure to the page instead of crashing it
        return json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"})
