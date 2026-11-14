# Kraken Shotgun Bot

TradingView Shotgun alerts to a Kraken Futures BTC/USD perpetual (`PI_XBTUSD`) webhook, intended for a **1-hour** chart. This is a risk-controlled starter, **not a production-ready or guaranteed-safe trading system**. Dry-run mode is the default.

## Signal alert payload

The Pine strategy sends a JSON message per filled order with `action`, `event_id`, and `reference_price`. In TradingView create a strategy alert for order fills, set its message to `{{strategy.order.alert_message}}`, and webhook URL to `https://YOUR-HOST/webhook`. Keep a private TradingView copy of the Pine script and set its `webhookSecret` input to match the server's `WEBHOOK_SECRET`. Do not publish the script or put Kraken API credentials in TradingView. A secret embedded in an alert payload is a bearer credential: use HTTPS, a long random secret, and rotate it if exposed.

The alert is for a BTC/USD chart on a 1h timeframe. The webhook accepts only the expected action set. It rejects missing/invalid event IDs and blocks replays using a local SQLite database. Keep that database on persistent storage, back it up, and do not run multiple instances against a local SQLite file on ephemeral disks.

## Install and run

```sh
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
gunicorn --workers 1 --bind 127.0.0.1:8080 app:app
```

Place a TLS-terminating reverse proxy in front of the service and restrict inbound traffic to the extent practical. `GET /healthz` reports mode and symbol, but does not check exchange connectivity. Set environment variables using a secret manager or deployment configuration; do not commit `.env`.

## Position sizing and protections

For entries, the webhook estimates contracts from configured risk divided by approximate loss per inverse contract (`CONTRACT_VALUE_USD * STOP_LOSS_PCT / 100`). It then clamps size to `MAX_CONTRACTS` and `MAX_ORDER_NOTIONAL_USD`. Size is whole contracts. The signal's reference price is used only for validation; the risk calculation uses inverse contract face value and stop distance. Fees, slippage, funding, gaps, currency conversion and liquidation risk are not included. Verify the instrument's current specifications and independently validate the calculation before use.

In live mode, before each entry the bot queries the account's open positions and refuses to add to an existing `PI_XBTUSD` position. Before each close, it verifies the actual position side and uses the exchange-reported size with a reduce-only order. It does not automatically reconcile an unknown or mismatched position, and safely rejects rather than guessing. If TradingView sends a close and then an opposite entry, the entry is accepted only if Kraken already reports flat; otherwise it is rejected and needs manual reconciliation/new signal.

`event_id` deduplication prevents ordinary webhook retries from submitting an order twice. If a process fails after claiming an event, that event stays locked; check Kraken position/order history before any manual recovery. Do not delete the database to retry blindly.

## Stops, targets, and live trading

Pine stop/target exits are **TradingView-triggered market-close alerts**, not exchange-resident stop orders. A webhook outage, alert delay, chart issue, rejected order, or internet outage can leave a position open without its intended stop. Do not rely on these alerts as guaranteed protection. No leverage is set by this app.

Live orders are disabled unless `ENABLE_LIVE_TRADING=true`. Before considering live mode, test the full flow in Kraken's current demo environment; verify the API auth and position response against official documentation, contract value/size, account permissions, alert behavior, and reversal races. Use a dedicated API key with no withdrawal permission. Review exchange state, risk settings, and compliance with local laws. Never paste API secrets into chat or source control.

Official references: <https://docs.kraken.com/exchange/guides/futures/rest>, <https://docs.kraken.com/exchange/guides/futures/introduction>, <https://docs.kraken.com/api/docs/futures-api/>.
