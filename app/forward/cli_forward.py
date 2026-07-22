"""CLI wiring for the locked forward-testing lifecycle. Kept separate so app/cli.py only
gains two tiny hooks. Commands: start, run, status, evaluate, list, verify.

Every command supports --json for machine-readable output; otherwise a compact human
summary is printed. `start` is refused unless the promotion gate returns PROMOTE.

Research only: no live broker, no real orders, no private keys.
"""
from __future__ import annotations

import json
from typing import List


def add_forward_parser(sub) -> None:
    p = sub.add_parser("forward",
                       help="locked forward-testing lifecycle (simulation; no broker/orders/keys)")
    p.add_argument("action",
                   choices=["start", "run", "status", "evaluate", "list", "verify"])
    p.add_argument("--id", dest="ftid", help="forward_test_id (run/status/evaluate/verify)")
    p.add_argument("--target", help="strategy id to lock (start), e.g. H-025")
    p.add_argument("--assets", default="BTC/USD,ETH/USD,SPY,XAU/USD")
    p.add_argument("--start-date", help="forward start date YYYY-MM-DD (start)")
    p.add_argument("--control", help="control strategy id for the promotion gate (start)")
    p.add_argument("--timeframe", default="1d")
    p.add_argument("--cost-bps", type=float, default=5.0)
    p.add_argument("--slippage-bps", type=float, default=5.0)
    p.add_argument("--matrix", default="v2")
    p.add_argument("--provider", default="csv")
    p.add_argument("--csv-root", default="data_csv")
    p.add_argument("--root", default=None, help="forward-tests root (default: knowledge_base)")
    p.add_argument("--enforce", action="store_true",
                   help="verify: invalidate the test if the locked hashes drifted")
    p.add_argument("--json", action="store_true", help="machine-readable JSON output")


def _emit(obj, as_json: bool) -> None:
    if as_json:
        print(json.dumps(obj, indent=2, sort_keys=True, default=str))


def run_forward(args) -> int:
    from app.forward import ForwardEngine, ForwardError

    pk = {"root": args.csv_root} if args.provider == "csv" else {}
    eng = ForwardEngine(root=args.root, provider=args.provider, provider_kwargs=pk)
    as_json = bool(args.json)

    try:
        if args.action == "start":
            if not args.target or not args.start_date:
                print("forward start requires --target and --start-date"); return 1
            assets: List[str] = [a.strip() for a in args.assets.split(",") if a.strip()]
            m = eng.start(args.target, assets, start_date=args.start_date,
                          timeframe=args.timeframe, cost_bps=args.cost_bps,
                          slippage_bps=args.slippage_bps, control_id=args.control)
            activated = m.get("activation", {}).get("activated", False)
            _emit(m, as_json)
            if not as_json:
                print(f"forward_test_id: {m['forward_test_id']}")
                print(f"strategy: {m['strategy_id']} v{m['strategy_version']}  "
                      f"status: {m['status']}")
                print("activated (ACTIVE)" if activated else
                      "NOT activated — consistency check blocked it (stays DRAFT)")
            return 0

        if args.action == "run":
            if not args.ftid:
                print("forward run requires --id"); return 1
            out = eng.run(args.ftid)
            _emit(out, as_json)
            if not as_json:
                if out["status"] == "INVALIDATED":
                    print(f"{args.ftid}: INVALIDATED — locked code/params changed. "
                          f"Start a new strategy version + new forward test.")
                else:
                    print(f"{args.ftid}: {out['status']}  "
                          f"+{out['new_observations']} obs "
                          f"(total {out['total_observations']})")
            return 0

        if args.action == "status":
            if not args.ftid:
                print("forward status requires --id"); return 1
            out = eng.status(args.ftid)
            _emit(out, as_json)
            if not as_json:
                print(f"{out['forward_test_id']}  {out['status']}")
                print(f"  strategy: {out['strategy_id']} v{out['strategy_version']}")
                print(f"  start: {out['start_date']}  earliest eval: {out['earliest_eval_date']}")
                print(f"  observations: {out['observations']}  "
                      f"last: {out['last_observation_utc']}")
            return 0

        if args.action == "evaluate":
            if not args.ftid:
                print("forward evaluate requires --id"); return 1
            rep = eng.evaluate(args.ftid)
            _emit(rep, as_json)
            if not as_json:
                print(f"{rep['forward_test_id']}: {rep['decision']}")
                for r in rep["reasons"]:
                    print(f"  - {r}")
            return 0

        if args.action == "verify":
            if not args.ftid:
                print("forward verify requires --id"); return 1
            out = eng.verify(args.ftid, enforce=args.enforce)
            _emit(out, as_json)
            if not as_json:
                integ = out["integrity"]
                print(f"{out['forward_test_id']}  status: {out['status']}  "
                      f"integrity: {'OK' if integ['ok'] else 'DRIFT'}")
                for c in integ.get("changes", []):
                    print(f"  changed {c['field']}: locked {c['locked']} -> current {c['current']}")
            return 0

        if args.action == "list":
            tests = eng.list_tests()
            _emit({"tests": tests}, as_json)
            if not as_json:
                if not tests:
                    print("no forward tests yet")
                for t in tests:
                    print(f"{t['forward_test_id']:<40} {t['status']:<12} "
                          f"{t['strategy_id']}  start {t['start_date']}")
            return 0

    except ForwardError as e:
        print(f"forward {args.action} refused: {e}")
        return 1
    except KeyError as e:
        print(f"forward {args.action} failed: {e}")
        return 1

    print(f"unknown forward action {args.action}")
    return 1
