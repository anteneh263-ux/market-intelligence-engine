import json
import unittest
from datetime import datetime, timezone

from app.data.realtime_probe import parse_ticker, probe


def ticker(**changes):
    row = dict(type="ticker", product_id="BTC-USD", price="100", best_bid="99.9",
               best_ask="100.1", best_bid_size="2", best_ask_size="3", last_size="0.5",
               trade_id=10, time="2026-11-19T04:00:00Z")
    row.update(changes)
    return row


class Socket:
    def __init__(self, messages):
        self.messages = iter(messages)
        self.sent = None

    def __enter__(self): return self
    def __exit__(self, *args): return False
    def send(self, message): self.sent = json.loads(message)
    def recv(self, timeout):
        try:
            return json.dumps(next(self.messages))
        except StopIteration:
            raise TimeoutError from None


class RealtimeProbeTests(unittest.TestCase):
    def test_parse(self):
        q = parse_ticker(ticker(), "BTC-USD", datetime.now(timezone.utc))
        self.assertEqual(q.bid, "99.9")
        self.assertEqual(q.exchange_time, "2026-11-19T04:00:00+00:00")

    def test_invalid_quotes(self):
        for change in ({"price": "NaN"}, {"best_bid": "200"}, {"trade_id": True},
                       {"product_id": "ETH-USD"}, {"time": "2026-11-19T04:00:00"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                parse_ticker(ticker(**change), "BTC-USD", datetime.now(timezone.utc))

    def test_sample_never_claims_completeness(self):
        ws = Socket([{"type": "subscriptions", "channels": [
            {"name": name, "product_ids": ["BTC-USD"]} for name in ("ticker", "heartbeat")]},
            {"type": "heartbeat", "product_id": "BTC-USD"}, ticker(), ticker(), ticker(trade_id=15)])
        result = probe(connect=lambda *a, **k: ws)
        self.assertEqual(result["status"], "OBSERVED_QUOTES")
        self.assertEqual(result["quotes"], 2)
        self.assertEqual(result["duplicates_or_reordered"], 1)
        self.assertEqual(result["trade_id_jumps"], 1)
        self.assertFalse(result["evidence"])
        self.assertFalse(result["complete_trade_history"])
        self.assertEqual(ws.sent["channels"], ["ticker", "heartbeat"])

    def test_error_and_missing_ack(self):
        self.assertEqual(probe(connect=lambda *a, **k: Socket([{"type": "error"}]))["status"], "FEED_ERROR")
        self.assertEqual(probe(connect=lambda *a, **k: Socket([ticker()]))["status"], "INCOMPLETE")

    def test_connection_failure(self):
        def fail(*a, **k): raise OSError("offline")
        self.assertEqual(probe(connect=fail)["status"], "CONNECTION_FAILED")


if __name__ == "__main__":
    unittest.main()
