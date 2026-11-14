from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from flask import Flask, jsonify, request

app = Flask(__name__)
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("shotgun")

WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")
KRAKEN_API_KEY = os.getenv("KRAKEN_FUTURES_API_KEY", "")
KRAKEN_API_SECRET = os.getenv("KRAKEN_FUTURES_API_SECRET", "")
ORDER_SIZE = int(os.getenv("ORDER_SIZE", "1"))
LIVE_TRADING = os.getenv("ENABLE_LIVE_TRADING", "false").lower() == "true"
KRAKEN_BASE_URL = os.getenv("KRAKEN_FUTURES_BASE_URL", "https://futures.kraken.com")
ENDPOINT_PATH = "/derivatives/api/v3/sendorder"
SYMBOL = "PI_XBTUSD"


def _sign(body: str, nonce: str) -> str:
    """Create Kraken Futures REST Authent signature for the submitted form body."""
    if not KRAKEN_API_SECRET:
        raise RuntimeError("KRAKEN_FUTURES_API_SECRET is not configured")
    digest = hashlib.sha256((body + nonce + ENDPOINT_PATH).encode()).digest()
    secret = base64.b64decode(KRAKEN_API_SECRET)
    return base64.b64encode(hmac.new(secret, digest, hashlib.sha512).digest()).decode()


def _submit_order(side: str, *, reduce_only: bool) -> dict:
    if not KRAKEN_API_KEY or not KRAKEN_API_SECRET:
        raise RuntimeError("Kraken Futures API credentials are not configured")
    fields = {
        "orderType": "mkt",
        "symbol": SYMBOL,
        "side": side,
        "size": str(ORDER_SIZE),
        "reduceOnly": "true" if reduce_only else "false",
    }
    body = urlencode(fields)
    nonce = str(time.time_ns() // 1_000_000)
    req = Request(
        KRAKEN_BASE_URL.rstrip("/") + ENDPOINT_PATH,
        data=body.encode(),
        headers={
            "APIKey": KRAKEN_API_KEY,
            "Authent": _sign(body, nonce),
            "Nonce": nonce,
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    with urlopen(req, timeout=10) as response:
        result = json.loads(response.read().decode())
    if result.get("result") == "error" or result.get("error"):
        raise RuntimeError(f"Kraken rejected order: {result.get('error', result)}")
    return result


@app.get("/healthz")
def health():
    return jsonify({"ok": True, "live_trading": LIVE_TRADING, "symbol": SYMBOL})


@app.post("/webhook")
def webhook():
    if not WEBHOOK_SECRET:
        return jsonify(error="Webhook is not configured"), 503
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify(error="Expected a JSON object"), 400
    supplied_secret = payload.pop("secret", "")
    if not isinstance(supplied_secret, str) or not hmac.compare_digest(supplied_secret, WEBHOOK_SECRET):
        return jsonify(error="Unauthorized"), 401

    action = payload.get("action")
    orders = {
        "buy": ("buy", False),
        "sell": ("sell", False),
        "close_long": ("sell", True),
        "close_short": ("buy", True),
    }
    if action not in orders:
        return jsonify(error="Unsupported action"), 400
    if ORDER_SIZE < 1:
        return jsonify(error="ORDER_SIZE must be at least 1 contract"), 500

    side, reduce_only = orders[action]
    order = {"symbol": SYMBOL, "side": side, "size": ORDER_SIZE, "reduceOnly": reduce_only, "orderType": "mkt"}
    if not LIVE_TRADING:
        log.info("DRY RUN - would submit order %s", order)
        return jsonify(ok=True, mode="dry-run", order=order), 200

    try:
        result = _submit_order(side, reduce_only=reduce_only)
    except Exception as exc:
        log.exception("Order submission failed")
        return jsonify(error="Order submission failed", detail=str(exc)), 502
    return jsonify(ok=True, mode="live", order=order, exchange=result), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8080")))
