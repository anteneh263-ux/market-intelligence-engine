"""Isolated v2/random experiment. No historical KB evidence or holdout feedback.

CLI: python -m app.discovery.experiment contract.json output_directory
Contract dates are inclusive UTC timestamps. Output must not already exist.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import random
import tempfile

import pandas as pd

from app.discovery.generator import enumerate_candidates
from app.discovery.genome import materialize
from app.discovery.learning_v2 import LearningStatsV2, policy_hash
from app.discovery.traits import avoid_reason, recipe_traits, score_breakdown
from app.research_core import Orchestrator
from app.research_core.versioning import resolve
from app.strategy_research.providers.base import DataRequest
from app.strategy_research.providers.csv_source import CSVDataSource
from app.strategy_research.strategies import register_strategy


class EmptyCorpus:
    def all_results(self):
        return []


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def validate(contract):
    if contract.get("holdout_provenance_confirmed") is not True:
        raise ValueError("Confirm holdout provenance explicitly; this tool cannot establish prior non-use")
    assets = contract["assets"]
    if not assets or len(set(assets)) != len(assets):
        raise ValueError("Assets must be nonempty and unique")
    for asset in assets:
        if any(c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789/:-_" for c in asset):
            raise ValueError("Invalid asset symbol")
    for key in ("rounds", "per_round", "max_vote"):
        if type(contract[key]) is not int or contract[key] < 1:
            raise ValueError(f"{key} must be a positive integer")
    if contract["max_vote"] not in (2, 3):
        raise ValueError("max_vote must be 2 or 3")
    if contract["timeframe"] != "1d":
        raise ValueError("Initial experiment supports daily bars only")
    names = [a.replace("/", "").replace(":", "") for a in assets]
    if len(set(names)) != len(names):
        raise ValueError("Assets collide in CSV filenames")
    seeds = contract["control_seeds"]
    if not seeds or any(type(s) is not int for s in seeds) or len(set(seeds)) != len(seeds):
        raise ValueError("Control seeds must be unique integers")
    dev, hold = [pd.to_datetime(contract[k], utc=True) for k in ("development", "holdout")]
    if len(dev) != 2 or len(hold) != 2 or not dev[0] < dev[1] < hold[0] < hold[1]:
        raise ValueError("Require ordered, nonoverlapping development and holdout windows")
    resolve(contract["matrix_version"])
    candidates = enumerate_candidates(contract["max_vote"])
    if contract["rounds"] * contract["per_round"] > len(candidates):
        raise ValueError("Budget exceeds candidate space")
    return candidates


def stage_data(contract, root):
    """Freeze normalized rows, per-asset counts and content hashes for both arms."""
    manifest = {}
    source = CSVDataSource(contract["csv_root"])
    for window in ("development", "holdout"):
        folder = root / window
        folder.mkdir()
        manifest[window] = {}
        start, end = pd.to_datetime(contract[window], utc=True)
        for asset in contract["assets"]:
            frame = source.get(DataRequest(asset, contract["timeframe"])).df
            frame = frame[frame.open_time.between(start.value // 10**6, end.value // 10**6)]
            if len(frame) < contract.get("min_bars", 500):
                raise ValueError(f"Too few bars: {window}/{asset}: {len(frame)}")
            data = frame.to_csv(index=False).encode()
            name = asset.replace("/", "").replace(":", "") + "_" + contract["timeframe"] + ".csv"
            (folder / name).write_bytes(data)
            manifest[window][asset] = {"rows": len(frame), "sha256": hashlib.sha256(data).hexdigest(),
                                       "first_ms": int(frame.open_time.min()), "last_ms": int(frame.open_time.max())}
    return manifest


def metrics(rows):
    counts = Counter(row["family"] for row in rows)
    hits = [i + 1 for i, row in enumerate(rows) if row["verdict"] == "Supported"]
    return {"attempts": len(rows), "supported": len(hits),
            "supported_fraction": len(hits) / len(rows) if rows else None,
            "attempts_to_first_supported": hits[0] if hits else None,
            "no_hit_within_budget": not hits,
            "unique_families": len(counts), "family_counts": dict(counts),
            "verdict_counts": dict(Counter(row["verdict"] for row in rows))}


def run(contract, output, evaluator=None):
    candidates = validate(contract)
    output = Path(output)
    # Exclusive directory creation prevents accidental overwrite or reuse.
    output.mkdir(parents=True, exist_ok=False)
    with tempfile.TemporaryDirectory(prefix="mie-experiment-") as temporary:
        root = Path(temporary)
        manifest = stage_data(contract, root)
        locked = {"contract": contract, "datasets": manifest,
                  "recipes": [{**r.to_dict(), "traits": recipe_traits(r)} for r in candidates], "policy_hash": policy_hash(),
                  "matrix_hash": resolve(contract["matrix_version"]).hash(),
                  "source_hashes": {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
                                    for p in sorted(Path("app").rglob("*.py"))},
                  "python_hash_seed": os.environ.get("PYTHONHASHSEED"),
                  "initial_evidence": "empty; development feedback within each arm only"}
        (output / "lock.json").write_text(json.dumps({**locked, "lock_hash": digest(locked)}, indent=2))
        orch = Orchestrator()

        def evaluate(recipe, window):
            if evaluator is not None:
                return evaluator(recipe, window)
            sid = "G-EXP-" + recipe.hash()
            register_strategy(materialize(recipe, sid))
            return orch.run(sid, contract["assets"], matrix_version=contract["matrix_version"],
                            timeframe=contract["timeframe"], provider="csv",
                            provider_kwargs={"root": str(root / window)}, write=False).report

        arms = [("v2", None)] + [(f"random-{seed}", seed) for seed in contract["control_seeds"]]
        selected, summaries = {}, {}
        with (output / "events.jsonl").open("x") as log:
            def emit(event):
                log.write(json.dumps(event, sort_keys=True, default=float) + "\n")
                log.flush()

            def checked(recipe, window, arm, round_no):
                try:
                    report = evaluate(recipe, window)
                    if report.get("is_real") is not True:
                        raise ValueError("Evaluation did not confirm real data")
                    if report.get("verdict") not in {"Supported", "Not Supported", "Graveyard", "Inconclusive"}:
                        raise ValueError("Unknown verdict")
                    return report
                except Exception as exc:
                    emit({"event": "error", "arm": arm, "round": round_no,
                          "window": window, "recipe": recipe.to_dict(), "error": str(exc)})
                    raise  # never quietly replace failed attempts or reduce an arm's budget

            for arm, seed in arms:
                rng = random.Random(seed)
                remaining = list(candidates)
                evidence, rows, chosen = [], [], []
                for rn in range(1, contract["rounds"] + 1):
                    stats = LearningStatsV2.from_corpus(EmptyCorpus(), evidence)
                    if seed is None:
                        eligible = [r for r in remaining if not avoid_reason(r, stats)]
                        eligible.sort(key=lambda r: (-score_breakdown(r, stats)["score"], r.hash()))
                    else:
                        eligible = list(remaining)
                        rng.shuffle(eligible)
                    if len(eligible) < contract["per_round"]:
                        raise ValueError("Treatment exhausted eligible candidates; equal budget cannot be met")
                    batch = eligible[:contract["per_round"]]
                    # Freeze the whole batch before incorporating its outcomes.
                    for recipe in batch:
                        emit({"event": "selection", "arm": arm, "round": rn, "recipe": recipe.to_dict(),
                              "rationale": score_breakdown(recipe, stats) if seed is None else {"uniform_seed": seed},
                              "remaining": len(remaining), "eligible": len(eligible)})
                    for recipe in batch:
                        report = checked(recipe, "development", arm, rn)
                        traits = recipe_traits(recipe)
                        evidence.append({"verdict": report["verdict"], "traits": traits,
                                         "result": {"is_real": True, "dsr": report.get("headline", {}).get("dsr")}})
                        rows.append({"family": traits["family"], "verdict": report["verdict"]})
                        emit({"event": "result", "window": "development", "arm": arm, "round": rn,
                              "recipe_hash": recipe.hash(), "report": report})
                        remaining.remove(recipe)
                        chosen.append(recipe)
                selected[arm] = chosen
                summaries[arm] = {"development": metrics(rows)}
            # No arm sees any holdout result until ALL selection trajectories are frozen.
            for arm, recipes in selected.items():
                rows = []
                for i, recipe in enumerate(recipes):
                    report = checked(recipe, "holdout", arm, i // contract["per_round"] + 1)
                    rows.append({"family": recipe_traits(recipe)["family"], "verdict": report["verdict"]})
                    emit({"event": "result", "window": "holdout", "arm": arm,
                          "attempt": i + 1, "recipe_hash": recipe.hash(), "report": report})
                summaries[arm]["holdout"] = metrics(rows)
        (output / "summary.json").write_text(json.dumps(summaries, indent=2))
        return summaries


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("contract", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    if os.environ.get("PYTHONHASHSEED") != "0":
        parser.error("Start this process with PYTHONHASHSEED=0 for reproducible family ties")
    run(json.loads(args.contract.read_text()), args.output)
