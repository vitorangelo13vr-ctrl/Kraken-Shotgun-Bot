# Kraken Shotgun Bot

TradingView **Shotgun** alerts to Kraken Futures, configured for the BTC/USD perpetual (`PI_XBTUSD`) and 1-hour charts.

> **Risk:** Futures are leveraged and can cause losses exceeding expectations. This starter is deliberately in dry-run mode. Verify signals, order sizing, contract specifications, and the behavior of your account in Kraken's demo environment before considering live trading. It does not choose leverage or guarantee that orders will match TradingView's simulated fills.

## Setup

1. Install Python 3.10+ and dependencies: `python -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt`.
2. Copy `.env.example` to `.env`, set a unique `WEBHOOK_SECRET`, and load environment values in your deployment environment. Do not commit `.env` or put Kraken API credentials in TradingView.
3. Run `python app.py`. Deploy behind HTTPS; TradingView webhooks require a publicly reachable endpoint. `GET /healthz` reports service status.
4. Add `tradingview/shotgun.pine` to TradingView and apply it to a BTC/USD chart on **1h**.
5. Create a strategy alert for order fills and set its message to `{{strategy.order.alert_message}}`. Use webhook URL `https://YOUR-HOST/webhook`.
6. Configure the alert's JSON message with the `secret` field and the action placeholder, for example:

   ```json
   {"secret":"YOUR_WEBHOOK_SECRET","action":"{{strategy.order.alert_message}}"}
   ```

   Instead, the Pine order alert messages already contain JSON action objects; use this TradingView message template (recommended):

   ```text
   {{strategy.order.alert_message}}
   ```

   The order alert message in the script must include the secret for authentication. Before enabling alerts, edit the `WEBHOOK_SECRET` placeholder in each Pine `alert_message` to match your `.env` value. Treat the TradingView alert configuration as sensitive.

7. Confirm webhook responses report `mode: dry-run` and check logs before any live use.

## Live orders

Live execution is intentionally opt-in: set `ENABLE_LIVE_TRADING=true` only after testing. Set `KRAKEN_FUTURES_API_KEY`, `KRAKEN_FUTURES_API_SECRET`, and `ORDER_SIZE` in the deployment environment. The API key should have only the required futures trading permissions and no withdrawal permission. Kraken credentials must never be embedded in the Pine script.

The bot uses a fixed `ORDER_SIZE` in contracts for each alert. `buy`/`sell` submit market orders; `close_long`/`close_short` submit reduce-only market orders. Confirm the contract size and account position before live use. TradingView and exchange positions can diverge if alerts are missed, duplicated, delayed, or rejected; this starter does not reconcile positions or prevent duplicate alerts.

For authenticated requests, use Kraken Futures API documentation as the source of truth: <https://docs.kraken.com/exchange/guides/futures/rest> and <https://docs.kraken.com/api/docs/futures-api/>. Test against the current Kraken Futures demo environment before production.
