"""CLI wiring for the paper-trading module. Kept separate so app/cli.py only gains
two tiny hooks. Commands: init, update, status, leaderboard, stop."""
from __future__ import annotations

import json


def add_paper_parser(sub) -> None:
    p = sub.add_parser("paper", help="safe paper-trading simulator (no real orders/keys/money)")
    p.add_argument("action", choices=["init", "update", "status", "leaderboard", "stop"])
    p.add_argument("--markets", default="BTC/USD,ETH/USD")
    p.add_argument("--capital", type=float, default=10_000.0)
    p.add_argument("--fee-bps", type=float, default=10.0)
    p.add_argument("--slippage-bps", type=float, default=5.0)
    p.add_argument("--warmup", type=int, default=300)
    p.add_argument("--provider", default="coinbase")
    p.add_argument("--csv-root", default="data_csv")
    p.add_argument("--root", default="paper_trading")


def run_paper(args) -> int:
    import os
    from app.paper import PaperEngine
    from app.paper.engine import PaperDataError

    pk = {"root": args.csv_root} if args.provider == "csv" else {}
    eng = PaperEngine(root=args.root, provider=args.provider, provider_kwargs=pk)
    config_path = os.path.join(args.root, "config.json")

    # commands that require an initialized session -> friendly message, no traceback
    if args.action in ("update", "status", "leaderboard", "stop") and not os.path.exists(config_path):
        print("Paper trading is not initialized. Run 'paper init' first.")
        return 1

    try:
        if args.action == "init":
            markets = [m.strip() for m in args.markets.split(",") if m.strip()]
            out = eng.init(markets=markets, capital=args.capital, fee_bps=args.fee_bps,
                           slippage_bps=args.slippage_bps, warmup=args.warmup)
        elif args.action == "update":
            out = eng.update()
        elif args.action == "status":
            out = eng.status()
        elif args.action == "leaderboard":
            out = eng.leaderboard()
        elif args.action == "stop":
            out = eng.stop()
        else:  # pragma: no cover
            out = {"error": f"unknown action {args.action}"}
    except PaperDataError as e:
        # data fetch / candle validation failed: SHORT message, no traceback
        print(f"paper {args.action} failed: {e}")
        if args.provider != "coinbase":
            print(f"(data source: {args.provider}). Try '--provider coinbase' for free, keyless BTC-USD/ETH-USD daily candles.")
        else:
            print("(data source: coinbase). Check network connectivity and that the market symbols are BTC/USD and ETH/USD.")
        return 1
    except FileNotFoundError:
        print("Paper trading is not initialized. Run 'paper init' first.")
        return 1
    print(json.dumps(out, indent=2))
    return 0