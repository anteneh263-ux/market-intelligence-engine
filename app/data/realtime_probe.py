"""Bounded, read-only Coinbase market-data diagnostic (not research evidence)."""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import re
import time


ENDPOINT = "wss://ws-feed.exchange.coinbase.com"


def _number(value, *, positive=False):
    number = Decimal(str(value))
    if not number.is_finite() or number < 0 or (positive and number == 0):
        raise ValueError("invalid numeric value")
    return number


@dataclass(frozen=True)
class Quote:
    product: str
    exchange_time: str
    received_time: str
    trade_id: int
    price: str
    bid: str
    ask: str
    bid_size: str
    ask_size: str
    last_size: str


def parse_ticker(message: dict, product: str, received: datetime) -> Quote:
    if message.get("type") != "ticker" or message.get("product_id") != product:
        raise ValueError("unexpected message or product")
    stamp = datetime.fromisoformat(message["time"].replace("Z", "+00:00"))
    if stamp.tzinfo is None or received.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    trade_id = message["trade_id"]
    if type(trade_id) is not int or trade_id < 0:
        raise ValueError("invalid trade ID")
    price, bid, ask = (_number(message[k], positive=True) for k in ("price", "best_bid", "best_ask"))
    if bid > ask:
        raise ValueError("crossed quote")
    bid_size, ask_size, last_size = (_number(message[k]) for k in ("best_bid_size", "best_ask_size", "last_size"))
    return Quote(product, stamp.astimezone(timezone.utc).isoformat(),
                 received.astimezone(timezone.utc).isoformat(), trade_id,
                 *map(str, (price, bid, ask, bid_size, ask_size, last_size)))


def probe(product="BTC-USD", seconds=30, max_messages=2000, *, connect=None):
    """Collect a bounded sample in memory; return diagnostics, never a dataset."""
    if not re.fullmatch(r"[A-Z0-9]{2,12}-[A-Z0-9]{2,12}", product):
        raise ValueError("invalid product")
    if not 1 <= seconds <= 300 or not 1 <= max_messages <= 10000:
        raise ValueError("probe bounds exceeded")
    connection_errors = (OSError, TimeoutError)
    if connect is None:
        from websockets.exceptions import WebSocketException
        from websockets.sync.client import connect
        connection_errors += (WebSocketException,)
    report = dict(schema="mie.realtime_probe.v1", source="coinbase_exchange", product=product,
                  evidence=False, complete_trade_history=False, messages=0, quotes=0,
                  heartbeats=0, invalid=0, duplicates_or_reordered=0, trade_id_jumps=0,
                  subscription_confirmed=False, last_quote=None, status="INCOMPLETE")
    last_id = None
    try:
        with connect(ENDPOINT, open_timeout=10, close_timeout=2, max_size=1048576) as ws:
            ws.send(json.dumps({"type": "subscribe", "product_ids": [product],
                                "channels": ["ticker", "heartbeat"]}))
            deadline = time.monotonic() + seconds
            while report["messages"] < max_messages:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    raw = ws.recv(timeout=remaining)
                except TimeoutError:
                    break
                received = datetime.now(timezone.utc)
                report["messages"] += 1
                try:
                    message = json.loads(raw)
                    if not isinstance(message, dict):
                        raise ValueError("expected JSON object")
                    kind = message.get("type")
                    if kind == "error":
                        report["status"] = "FEED_ERROR"
                        return report
                    if kind == "subscriptions":
                        channels = {c["name"] for c in message["channels"] if product in c["product_ids"]}
                        report["subscription_confirmed"] = {"ticker", "heartbeat"} <= channels
                    elif kind == "heartbeat" and message.get("product_id") == product:
                        report["heartbeats"] += 1
                    elif kind == "ticker":
                        quote = parse_ticker(message, product, received)
                        if last_id is not None and quote.trade_id <= last_id:
                            report["duplicates_or_reordered"] += 1
                            continue
                        if last_id is not None and quote.trade_id > last_id + 1:
                            report["trade_id_jumps"] += 1
                        last_id = quote.trade_id
                        report["quotes"] += 1
                        report["last_quote"] = asdict(quote)
                except (ValueError, TypeError, KeyError, AttributeError, InvalidOperation):
                    report["invalid"] += 1
            if report["quotes"] and report["subscription_confirmed"] and not report["invalid"]:
                report["status"] = "OBSERVED_QUOTES"
    except connection_errors as exc:
        report["status"] = "CONNECTION_FAILED"
        report["error_type"] = type(exc).__name__
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--product", default="BTC-USD")
    parser.add_argument("--seconds", type=int, default=30)
    parser.add_argument("--max-messages", type=int, default=2000)
    args = parser.parse_args(argv)
    result = probe(args.product, args.seconds, args.max_messages)
    print(json.dumps(result, indent=2))
    return 0 if result["status"] == "OBSERVED_QUOTES" else 1


if __name__ == "__main__":
    raise SystemExit(main())
