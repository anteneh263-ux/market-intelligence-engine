"""CLI wiring for the research-safety gates. Commands: control, promote, audit, trials.
Research only - no live trading, no broker, no order execution."""
from __future__ import annotations

import json
from typing import List


def add_safety_parser(sub) -> None:
    p = sub.add_parser("safety", help="research-safety gates & trial auditing (research only)")
    p.add_argument("action", choices=["control", "promote", "audit", "trials"])
    p.add_argument("--target", help="candidate strategy id, e.g. H-025")
    p.add_argument("--control", help="control strategy id (default CTRL-TARGET-ONLY)")
    p.add_argument("--assets", default="BTC/USD,ETH/USD,SPY,XAU/USD")
    p.add_argument("--matrix", default="v2")
    p.add_argument("--provider", default="csv")
    p.add_argument("--csv-root", default="data_csv")
    p.add_argument("--timeframe", default="1d")


def run_safety(args) -> int:
    pk = {"root": args.csv_root} if args.provider == "csv" else {}
    assets: List[str] = [a.strip() for a in args.assets.split(",") if a.strip()]
    if args.action == "control":
        from app.research_safety import compare
        out = compare(args.target, args.control or "CTRL-TARGET-ONLY", assets,
                      timeframe=args.timeframe, matrix=args.matrix,
                      provider=args.provider, provider_kwargs=pk)
    elif args.action == "promote":
        from app.research_safety import evaluate_promotion
        out = evaluate_promotion(args.target, assets, control_id=args.control,
                                 timeframe=args.timeframe, matrix=args.matrix,
                                 provider=args.provider, provider_kwargs=pk)
    elif args.action == "audit":
        from app.research_safety import run_audit
        from app.strategy_research.strategies import get_strategy
        from app.strategy_research.providers import DataRequest, get_provider
        df = get_provider(args.provider, **pk).get(DataRequest(asset=assets[0], timeframe=args.timeframe)).df
        out = run_audit(get_strategy(args.target), df)
    elif args.action == "trials":
        from app.research_safety import TrialLedger
        out = TrialLedger().summary()
    else:  # pragma: no cover
        out = {"error": f"unknown action {args.action}"}
    print(json.dumps(out, indent=2, default=str))
    return 0
