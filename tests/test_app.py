from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest.mock import patch

import app as bot


class WebhookTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.env = patch.multiple(
            bot,
            WEBHOOK_SECRET="test-secret",
            LIVE_TRADING=False,
            DB_PATH=os.path.join(self.tempdir.name, "events.sqlite3"),
            RISK_PER_TRADE_USD=10.0,
            CONTRACT_VALUE_USD=1.0,
            STOP_LOSS_PCT=1.0,
            MAX_CONTRACTS=10,
            MAX_ORDER_NOTIONAL_USD=1000.0,
        )
        self.env.start()
        self.client = bot.app.test_client()

    def tearDown(self):
        self.env.stop()
        self.tempdir.cleanup()

    def post(self, action="buy", event_id="test-1", **extra):
        payload = {"secret": "test-secret", "action": action, "event_id": event_id, "reference_price": 65000, **extra}
        return self.client.post("/webhook", data=json.dumps(payload), content_type="application/json")

    def test_sizing_respects_risk_and_caps(self):
        self.assertEqual(bot._contracts_for_risk(65000), 10)
        with patch.object(bot, "RISK_PER_TRADE_USD", 3.2):
            self.assertEqual(bot._contracts_for_risk(65000), 3)

    def test_dry_run_entry(self):
        response = self.post()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["mode"], "dry-run")
        self.assertEqual(response.json["order"]["size"], 10)

    def test_bad_secret_rejected(self):
        response = self.client.post("/webhook", json={"secret": "wrong", "action": "buy", "event_id": "x"})
        self.assertEqual(response.status_code, 401)

    def test_duplicate_event_does_not_repeat(self):
        first = self.post(event_id="same-id")
        second = self.post(event_id="same-id")
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json["mode"], "dry-run")

    def test_missing_event_id_rejected(self):
        response = self.client.post("/webhook", json={"secret": "test-secret", "action": "buy"})
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
