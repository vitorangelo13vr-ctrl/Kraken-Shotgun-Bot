from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import math
import os
import sqlite3
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from flask import Flask, jsonify, request

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("shotgun")

WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")
KRAKEN_API_KEY = os.getenv("KRAKEN_FUTURES_API_KEY", "")
KRAKEN_API_SECRET = os.getenv("KRAKEN_FUTURES_API_SECRET", "")
LIVE_TRADING = os.getenv("ENABLE_LIVE_TRADING", "false").lower() == "true"
KRAKEN_BASE_URL = os.getenv("KRAKEN_FUTURES_BASE_URL", "https://futures.kraken.com").rstrip("/")
SYMBOL = os.getenv("KRAKEN_FUTURES_SYMBOL", "PI_XBTUSD")
TIMEFRAME = "5"
ORDER_ENDPOINT = "/derivatives/api/v3/sendorder"
POSITIONS_ENDPOINT = "/derivatives/api/v3/openpositions"
DB_PATH = os.getenv("IDEMPOTENCY_DB", "shotgun-events.sqlite3")

# PI_XBTUSD is an inverse contract. Confirm the current contract specification
# for your account/venue before setting this value or enabling live execution.
CONTRACT_VALUE_USD = float(os.getenv("CONTRACT_VALUE_USD", "1"))
RISK_PER_TRADE_USD = float(os.getenv("RISK_PER_TRADE_USD", "10"))
STOP_LOSS_PCT = float(os.getenv("STOP_LOSS_PCT", "1.0"))
MAX_CONTRACTS = int(os.getenv("MAX_CONTRACTS", "10"))
MAX_ORDER_NOTIONAL_USD = float(os.getenv("MAX_ORDER_NOTIONAL_USD", "1000"))


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""CREATE TABLE IF NOT EXISTS webhook_events (
        event_id TEXT PRIMARY KEY,
        state TEXT NOT NULL,
        response_json TEXT,
        created_at INTEGER NOT NULL
    )""")
    return conn


def _claim_event(event_id: str) -> dict[str, Any] | None:
    """Claim an event once. A duplicate never submits a second order."""
    with _db() as conn:
        row = conn.execute("SELECT state, response_json FROM webhook_events WHERE event_id=?", (event_id,)).fetchone()
        if row:
            if row["state"] == "done" and row["response_json"]:
                return json.loads(row["response_json"])
            raise ValueError("Duplicate or previously interrupted event; reconcile the exchange position before retrying")
        conn.execute("INSERT INTO webhook_events(event_id,state,created_at) VALUES(?,?,?)", (event_id, "processing", int(time.time())))
    return None


def _finish_event(event_id: str, response: dict[str, Any]) -> None:
    with _db() as conn:
        conn.execute("UPDATE webhook_events SET state='done', response_json=? WHERE event_id=?", (json.dumps(response), event_id))


def _signature(endpoint: str, post_data: str, nonce: str) -> str:
    if not KRAKEN_API_SECRET:
        raise RuntimeError("Kraken Futures API secret is not configured")
    digest = hashlib.sha256((post_data + nonce + endpoint).encode()).digest()
    secret = base64.b64decode(KRAKEN_API_SECRET, validate=True)
    return base64.b64encode(hmac.new(secret, digest, hashlib.sha512).digest()).decode()


def _kraken_request(endpoint: str, fields: dict[str, str] | None = None) -> dict[str, Any]:
    if not KRAKEN_API_KEY or not KRAKEN_API_SECRET:
        raise RuntimeError("Kraken Futures credentials are not configured")
    post_data = urlencode(fields or {})
    nonce = str(time.time_ns() // 1_000_000)
    headers = {
        "APIKey": KRAKEN_API_KEY,
        "Authent": _signature(endpoint, post_data, nonce),
        "Nonce": nonce,
    }
    data = post_data.encode() if fields else None
    if data is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    req = Request(KRAKEN_BASE_URL + endpoint, data=data, headers=headers, method="POST" if data else "GET")
    try:
        with urlopen(req, timeout=10) as response:
            payload = json.loads(response.read().decode())
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError("Kraken API request failed; inspect service logs and reconcile account state") from exc
    if not isinstance(payload, dict) or payload.get("result") == "error" or payload.get("error"):
        raise RuntimeError("Kraken returned an unsuccessful API response")
    return payload


def _open_position() -> dict[str, Any] | None:
    response = _kraken_request(POSITIONS_ENDPOINT)
    positions = response.get("openPositions")
    if not isinstance(positions, list):
        raise RuntimeError("Unrecognized openpositions response; refusing to trade")
    matching = [p for p in positions if isinstance(p, dict) and p.get("symbol") == SYMBOL]
    if not matching:
        return None
    if len(matching) != 1:
        raise RuntimeError("Multiple matching positions returned; refusing to trade")
    position = matching[0]
    side = str(position.get("side", "")).lower()
    try:
        size = abs(int(position["size"]))
    except (KeyError, TypeError, ValueError):
        raise RuntimeError("Unrecognized position size; refusing to trade") from None
    if side not in {"long", "short"} or size < 1:
        raise RuntimeError("Unrecognized position side/size; refusing to trade")
    return {"side": side, "size": size}


def _contracts_for_risk(reference_price: float) -> int:
    if not math.isfinite(reference_price) or reference_price <= 0:
        raise ValueError("reference_price must be a positive finite number")
    if CONTRACT_VALUE_USD <= 0 or RISK_PER_TRADE_USD <= 0 or MAX_CONTRACTS < 1 or MAX_ORDER_NOTIONAL_USD <= 0:
        raise RuntimeError("Invalid risk configuration")
    if not 0 < STOP_LOSS_PCT < 100:
        raise RuntimeError("STOP_LOSS_PCT must be between 0 and 100")
    risk_per_contract = CONTRACT_VALUE_USD * STOP_LOSS_PCT / 100.0
    risk_limited = math.floor(RISK_PER_TRADE_USD / risk_per_contract)
    notional_limited = math.floor(MAX_ORDER_NOTIONAL_USD / CONTRACT_VALUE_USD)
    return max(0, min(risk_limited, notional_limited, MAX_CONTRACTS))


def _send_order(side: str, size: int, reduce_only: bool) -> dict[str, Any]:
    return _kraken_request(ORDER_ENDPOINT, {
        "orderType": "mkt",
        "symbol": SYMBOL,
        "side": side,
        "size": str(size),
        "reduceOnly": "true" if reduce_only else "false",
    })


@app.get("/healthz")
def health():
    return jsonify(ok=True, mode="live" if LIVE_TRADING else "dry-run", symbol=SYMBOL, timeframe="5m")


@app.post("/webhook")
def webhook():
    if not WEBHOOK_SECRET or WEBHOOK_SECRET == "replace-with-a-long-random-secret":
        return jsonify(error="Webhook secret is not configured"), 503
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify(error="Expected a JSON object"), 400
    supplied_secret = payload.pop("secret", "")
    if not isinstance(supplied_secret, str) or not hmac.compare_digest(supplied_secret, WEBHOOK_SECRET):
        return jsonify(error="Unauthorized"), 401
    if payload.get("market") != SYMBOL:
        return jsonify(error="Alert market does not match configured Kraken Futures contract"), 400
    if payload.get("timeframe") != TIMEFRAME:
        return jsonify(error="This bot only accepts 5-minute strategy alerts"), 400

    action = payload.get("action")
    if action not in {"buy", "sell", "close_long", "close_short"}:
        return jsonify(error="Unsupported action"), 400
    event_id = payload.get("event_id")
    if not isinstance(event_id, str) or not 1 <= len(event_id) <= 128:
        return jsonify(error="event_id is required (1-128 characters)"), 400

    try:
        duplicate = _claim_event(event_id)
    except ValueError as exc:
        return jsonify(error=str(exc)), 409
    if duplicate is not None:
        return jsonify(duplicate), 200

    try:
        if action in {"buy", "sell"}:
            reference_price = float(payload.get("reference_price", "nan"))
            size = _contracts_for_risk(reference_price)
            if size < 1:
                raise ValueError("Risk limits result in less than one contract; no order sent")
            side, reduce_only = ("buy" if action == "buy" else "sell"), False
            expected_position = "flat"
        else:
            size = 0
            side, reduce_only = ("sell", True) if action == "close_long" else ("buy", True)
            expected_position = "long" if action == "close_long" else "short"

        order: dict[str, Any] = {
            "symbol": SYMBOL, "side": side, "reduceOnly": reduce_only,
            "orderType": "mkt", "size": size if size else "from-position",
        }
        if not LIVE_TRADING:
            if not size:
                size = int(os.getenv("DRY_RUN_CLOSE_SIZE", "1"))
                order["size"] = size
            result = {"ok": True, "mode": "dry-run", "order": order, "timeframe": "5m", "risk_per_trade_usd": RISK_PER_TRADE_USD}
        else:
            position = _open_position()
            if expected_position == "flat":
                if position is not None:
                    raise RuntimeError("A position is already open for this symbol; refusing to add or reverse")
            else:
                if position is None or position["side"] != expected_position:
                    raise RuntimeError("Position does not match requested close; refusing to send order")
                size = position["size"]
                order["size"] = size
            exchange = _send_order(side, size, reduce_only)
            result = {"ok": True, "mode": "live", "order": order, "exchange": exchange, "timeframe": "5m"}
        _finish_event(event_id, result)
        return jsonify(result), 200
    except (ValueError, RuntimeError) as exc:
        log.warning("Webhook event %s rejected: %s", event_id, exc)
        response = {"ok": False, "error": str(exc)}
        _finish_event(event_id, response)
        return jsonify(response), 409
    except Exception:
        log.exception("Unexpected webhook processing failure for event %s", event_id)
        return jsonify(error="Unexpected failure; event is locked to prevent duplicate execution"), 500


@app.errorhandler(413)
def too_large(_error):
    return jsonify(error="Request too large"), 413


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.getenv("PORT", "8080")), debug=False)
