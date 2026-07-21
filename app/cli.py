"""Unified command line for the Market Intelligence Engine.

One entry point over the whole platform:

    python -m app.cli list [strategies|providers|matrices|modules]
    python -m app.cli matrix                         # version log
    python -m app.cli evaluate --target H-010 --assets AAPL,BTC/USD --matrix v2 [--no-write]
    python -m app.cli batch --targets H-003,H-004,... --assets AAPL,BTC/USD --matrix v2
    python -m app.cli learn                          # run meta-learning, write LEARNING_REPORT.md
    python -m app.cli ask "which strategies generalize across markets?"
    python -m app.cli kb show H-010                  # a strategy's recorded result

Strategy Evaluation is just the default module; the same flow drives any module
registered in the Research Core.
"""
from __future__ import annotations

import argparse
import json

from app.research_core import Orchestrator, module_registry, versioning
from app.knowledge_base import KnowledgeBase
from app.meta_learning import mine, render_report
from app.assistant import ResearchAssistant


def _split(s):
    return [x.strip() for x in s.split(",") if x.strip()]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="mie", description="Market Intelligence Engine")
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("list"); p.add_argument("what", nargs="?", default="strategies")
    sub.add_parser("matrix")
    p = sub.add_parser("evaluate")
    p.add_argument("--target", required=True); p.add_argument("--assets", required=True)
    p.add_argument("--module", default="strategy_evaluation")
    p.add_argument("--matrix", default="v2"); p.add_argument("--provider", default="csv")
    p.add_argument("--timeframe", default="1d"); p.add_argument("--csv-root", default="data_csv")
    p.add_argument("--no-write", action="store_true")
    p = sub.add_parser("batch")
    p.add_argument("--targets", required=True); p.add_argument("--assets", required=True)
    p.add_argument("--module", default="strategy_evaluation")
    p.add_argument("--matrix", default="v2"); p.add_argument("--provider", default="csv")
    p.add_argument("--timeframe", default="1d"); p.add_argument("--csv-root", default="data_csv")
    sub.add_parser("learn")
    p = sub.add_parser("discover")
    p.add_argument("--k", type=int, default=5); p.add_argument("--rounds", type=int, default=1)
    p.add_argument("--assets", default="AAPL,BTC/USD,ETH/USD,SPY,XAU/USD")
    p.add_argument("--matrix", default="v2"); p.add_argument("--provider", default="csv")
    p.add_argument("--csv-root", default="data_csv")
    p.add_argument("--propose-only", action="store_true", help="generate + explain, don't test")
    p = sub.add_parser("research")
    p.add_argument("--pine-file", required=True); p.add_argument("--name", default="")
    p.add_argument("--assets", default="AAPL,BTC/USD,ETH/USD,SPY,XAU/USD")
    p.add_argument("--matrix", default="v2"); p.add_argument("--provider", default="csv")
    p.add_argument("--csv-root", default="data_csv"); p.add_argument("--variants", type=int, default=2)
    p = sub.add_parser("ask"); p.add_argument("question")
    p = sub.add_parser("kb"); p.add_argument("action"); p.add_argument("target", nargs="?")
    from app.paper.cli_paper import add_paper_parser, run_paper
    add_paper_parser(sub)

    args = ap.parse_args(argv)
    kb = KnowledgeBase()

    if args.cmd == "list":
        if args.what.startswith("strat"): print("\n".join(module_registry.get_module("strategy_evaluation").catalogue()))
        elif args.what.startswith("prov"): print("\n".join(module_registry.providers()))
        elif args.what.startswith("mat"): print(json.dumps(versioning.list_versions(), indent=2))
        elif args.what.startswith("mod"): print(json.dumps(module_registry.list_modules(), indent=2))
        return 0

    if args.cmd == "matrix":
        print(json.dumps(versioning.list_versions(), indent=2)); return 0

    if args.cmd in ("evaluate", "batch"):
        orch = Orchestrator(kb)
        pk = {"root": args.csv_root} if args.provider == "csv" else {}
        common = dict(module=args.module, matrix_version=args.matrix, provider=args.provider,
                      timeframe=args.timeframe, provider_kwargs=pk)
        targets = [args.target] if args.cmd == "evaluate" else _split(args.targets)
        write = not getattr(args, "no_write", False)
        outs = orch.run_batch(targets, _split(args.assets), write=write, **common)
        for o in outs:
            h = o.report.get("headline", {})
            print(f"{o.target:<7} {o.verdict:<14} matrix={o.matrix_version} "
                  f"DSR={_f(h.get('dsr'))} genl={_f(o.report.get('generalization_score'))} "
                  f"real={o.is_real}")
        if write:
            print("meta-learning refreshed → LEARNING_REPORT.md")
        return 0

    if args.cmd == "learn":
        path = kb.write_artifact("LEARNING_REPORT.md", render_report(mine(kb)))
        print("wrote", path); return 0

    if args.cmd == "discover":
        from app.discovery import StrategyDiscoveryEngine, DISCOVERY_POLICY_VERSION, policy_hash
        eng = StrategyDiscoveryEngine(kb)
        print(f"discovery policy {DISCOVERY_POLICY_VERSION} ({policy_hash()})")
        pk = {"root": args.csv_root} if args.provider == "csv" else {}
        if args.propose_only:
            for p in eng.propose(args.k, round_no=0, persist=False):
                print(f"  {p.gid}  prio={p.priority:.3f}  {p.label}")
                print(f"        why: {p.rationale['why']}")
            return 0
        for _ in range(args.rounds):
            s = eng.run_round(args.k, _split(args.assets), matrix_version=args.matrix,
                              provider=args.provider, provider_kwargs=pk)
            print(f"round {s['round']}: generated {s['generated']} tested {s['tested']} "
                  f"survived {s['survived']} (rate {s['survival_rate']:.2f}) "
                  f"bestDSR {s['best_dsr']:.3f} | budget {s['budget']}")
        path = kb.write_artifact("discovery/DISCOVERY_REPORT.md", eng.render_report())
        print("wrote", path)
        return 0

    if args.cmd == "research":
        from app.research_core.workflow import research_strategy
        pine = open(args.pine_file).read()
        pk = {"root": args.csv_root} if args.provider == "csv" else {}
        r = research_strategy(pine, name=args.name, assets=_split(args.assets),
                              matrix=args.matrix, provider=args.provider,
                              provider_kwargs=pk, k_variants=args.variants, kb=kb)
        if not r.get("ok"):
            print("import failed:", r.get("warnings")); return 1
        print(f"{r['uid']} {r['name']} [{r['class']}] recipe={r['recipe']}")
        print(f"  original: {r['verdict']} DSR={_f(r['orig_dsr'])}  resembles={r['resembles']}")
        for v in r["variants"]:
            print(f"  variant {v['gid']}: {v['verdict']} DSR={_f(v['dsr'])}  {v['recipe']}")
        print("report:", r["report_path"])
        return 0

    if args.cmd == "ask":
        a = ResearchAssistant(kb).answer(args.question)
        print(a.text)
        if a.citations: print("\nsources:", ", ".join(a.citations))
        return 0

    if args.cmd == "kb":
        if args.action == "show" and args.target:
            print(json.dumps(kb.result(args.target), indent=2))
        elif args.action == "results":
            print(json.dumps(kb.all_results(), indent=2))
        return 0

    if args.cmd == "paper":
        return run_paper(args)

    ap.print_help(); return 0


def _f(x):
    return "—" if x is None else (f"{x:.3f}" if isinstance(x, (int, float)) else str(x))


if __name__ == "__main__":
    raise SystemExit(main())
