"""CLI — evaluate a strategy through the frozen pipeline and (optionally) write
results into the knowledge base.

Examples:
  # Real data from CSVs in ./data_csv (e.g. BTCUSDT_1d.csv, AAPL_1d.csv):
  python -m app.strategy_research.run --strategy H-010 --provider csv \
      --assets BTC/USDT,ETH/USDT --timeframe 1d --write

  # Methodology demo on synthetic data (never written as evidence):
  python -m app.strategy_research.run --strategy H-020 --provider synthetic \
      --assets BTC/USDT,ETH/USDT --timeframe 1d

Run with no strategy to list what's available.
"""
from __future__ import annotations

import argparse
import json

from app.research_core import versioning

from . import MATRICES, available_providers, available_strategies
from .dossier import apply_report
from .evaluation import EvalRequest, evaluate


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Strategy Research evaluation engine")
    ap.add_argument("--strategy", help="dossier id, e.g. H-010")
    ap.add_argument("--provider", default="csv", help=f"one of {available_providers()}")
    ap.add_argument("--assets", default="BTC/USDT,ETH/USDT", help="comma-separated")
    ap.add_argument("--timeframe", default="1d")
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None)
    ap.add_argument("--limit", type=int, default=1500)
    ap.add_argument("--csv-root", default="data_csv", help="folder of CSVs for csv provider")
    ap.add_argument("--matrix", default=versioning.ACTIVE_VERSION, choices=sorted(MATRICES),
                    help="evaluation matrix (default: %(default)s); select v1-v3 explicitly for legacy methods")
    ap.add_argument("--write", action="store_true",
                    help="write STEP 5 into the dossier + INDEX (real data only)")
    ap.add_argument("--json", action="store_true", help="print full report as JSON")
    args = ap.parse_args(argv)
    matrix = MATRICES[args.matrix]

    if not args.strategy:
        print("Strategies:", ", ".join(available_strategies()))
        print("Providers :", ", ".join(available_providers()))
        print("Matrix    :", matrix.version, matrix.hash())
        return 0

    strategy_ids = [s.strip() for s in args.strategy.split(",") if s.strip()]
    assets = [a.strip() for a in args.assets.split(",") if a.strip()]
    pk = {"root": args.csv_root} if args.provider == "csv" else {}

    for sid in strategy_ids:
        req = EvalRequest(
            strategy_id=sid, provider=args.provider, assets=assets,
            timeframe=args.timeframe, start=args.start, end=args.end, limit=args.limit,
            matrix_version=matrix.version,
        )
        rep = evaluate(req, matrix, provider_kwargs=pk)

        print(f"\n{'='*70}\n{rep.strategy_id} {rep.strategy_name}  [{rep.strategy_class}]")
        print(f"provider={rep.provider} is_real={rep.is_real} "
              f"assets={rep.assets_evaluated} matrix={rep.matrix_version}/{rep.matrix_hash} "
              f"pooling={rep.headline.get('pooling')}")
        h = rep.headline
        if h:
            print(f"OOS Sharpe(ann)={h.get('oos_sharpe_annual',0):.3f}  "
                  f"DSR={h.get('dsr',0):.3f}  beats_bh={h.get('beats_buy_hold')}  "
                  f"beats_rand={h.get('beats_random')}  maxDD={h.get('max_drawdown',0)*100:.1f}%  "
                  f"trades={h.get('num_trades',0)}")
        print(f"robustness={rep.robustness_score:.3f} stability={rep.stability_score:.3f} "
              f"generalization={rep.generalization_score:.3f} confidence={rep.confidence_score:.3f}")
        print(f"VERDICT: {rep.verdict}  ({'; '.join(rep.verdict_reasons)})")
        for n in rep.notes:
            print("note:", n)

        if args.write:
            res = apply_report(rep)
            print("write:", {k: res[k] for k in ('dossier_written', 'index_written',
                                                 'meta_written', 'results_written')})
        if args.json:
            print(json.dumps(rep.to_dict(), indent=2, default=float))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
