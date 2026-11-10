# Market Intelligence Engine

## Lokal applikasjon

Dobbeltklikk **Start MIE.command** for å åpne den lokale arbeidsflaten.
Den samler arkiv, rapportvisning, eksport og Pine-beskrivelse.
[Bruksveiledning og avgrensninger](docs/WORKSPACE_APP.md). Ingen nye markedstester startes fra appen.


A reproducible, auditable research platform that treats every trading strategy as a **falsifiable
scientific hypothesis** — not a signal to trust. It evaluates strategies through one frozen,
versioned pipeline, compounds the results into a queryable Knowledge Base, learns which traits
survive, and **generates new hypotheses** that it tests scientifically.

> ⚠️ **Research only.** No execution, no buy/sell signals, no financial advice. A "verdict" here is a
> statement about *evidence*, never a recommendation to trade.

The platform is organised as six modules:

**Research Core → Strategy Evaluation → Knowledge Base → Meta-learning → AI Research Assistant → Strategy Discovery Engine**

See `docs/ARCHITECTURE.md` for the full design.

Research-safety gates (target-only control, full DSR trial-ledger, declared-vs-actual
input audit, and a pre-paper-trading promotion gate) are documented in
`docs/RESEARCH_SAFETY.md` — additive tools that reduce false positives without changing
any existing evaluation result. Research only; no live trading or broker.

---

## Requirements

- **macOS** (or Linux) with **Python 3.10+** (`python3 --version`).
- ~150 MB disk. No internet required to run — real sample market data is bundled.

---

## Install (VS Code, 4 commands)

Open the unzipped folder in VS Code, then in a terminal at the project root:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e ".[dev]"
```

This installs dependencies and the `mie` command. (Alternatively: `pip install -r requirements.txt`.)

Optional config: `cp .env.example .env` — but the engine runs fine with **no** `.env`; it
auto-locates the bundled `knowledge_base/` and `data_csv/` from any folder.

---

## First test command (run this first)

```bash
pytest
```

Expect **36 passing tests** across strategy logic, the platform layers, discovery, the deterministic
dossier generator, and the end-to-end research workflow. A quick smoke check without pytest:

```bash
mie matrix          # prints the frozen evaluation-matrix versions (v1 historical, v2 active)
```

---

## Analyze a Pine Script (the headline workflow)

One command runs the full research workflow on a strategy file (a sample is bundled):

```bash
mie research --pine-file imports/bb_rsi_meanrev.pine
```

This imports and classifies the strategy, compares it against the 23-strategy corpus, explains its
likely strengths/weaknesses from the Knowledge Base, asks the Discovery Engine for improved variants,
evaluates the original and the variants on the same `srp-matrix-v2` pipeline, and writes a grounded
comparison report to `knowledge_base/workflows/RESEARCH_<id>.md`.

To analyze **your own** strategy, drop a `.pine` file in `imports/` and point `--pine-file` at it.

---

## CLI reference

```bash
mie list strategies        # the 23 built-in strategies (H-002 … H-024)
mie list providers         # data providers: csv, binance, fmp, coindesk, synthetic
mie list matrices          # frozen evaluation-matrix versions + hashes
mie list modules           # research modules registered in the engine

mie matrix                 # version log for the evaluation matrix

# Evaluate one strategy on real data (bundled CSVs), writing results to the Knowledge Base:
mie evaluate --target H-010 --assets AAPL,BTC/USD,ETH/USD,SPY,XAU/USD --matrix v2

# Evaluate several at once, then refresh meta-learning:
mie batch --targets H-003,H-004,H-009 --assets AAPL,SPY --matrix v2

mie learn                  # re-mine the corpus -> knowledge_base/LEARNING_REPORT.md

# Strategy Discovery Engine:
mie discover --propose-only --k 5           # generate + EXPLAIN why (dry run, no test)
mie discover --k 4 --rounds 3 --matrix v2   # closed loop: generate -> test -> learn

mie ask "which strategies generalize across markets?"   # grounded assistant answer
mie kb show H-009          # a strategy's recorded result (JSON)

mie research --pine-file imports/bb_rsi_meanrev.pine     # full workflow on a Pine Script
```

All results are reproducible from `(request hash, matrix hash, provider)`.

---

## Run the API server (optional)

The same capabilities are exposed over HTTP:

```bash
uvicorn app.main:app --reload
```

Then open <http://127.0.0.1:8000/docs> for the interactive API (health, market data, and the
`/api/engine/*` platform endpoints: matrices, evaluate, knowledge-base, learn, ask).

---

## Project structure

```
market-intelligence-engine/
├── app/                       # the engine (importable package)
│   ├── research_core/         # orchestrator, matrix versioning, module registry, dossier generator
│   ├── strategy_research/     # Strategy Evaluation module: providers, strategies, frozen pipeline
│   ├── knowledge_base/        # corpus persistence + query API + literature access
│   ├── meta_learning/         # corpus pattern mining
│   ├── assistant/             # AI Research Assistant (grounded Q&A)
│   ├── discovery/             # Strategy Discovery Engine (generate + learn)
│   ├── adapters/              # small Pine Script importer
│   ├── main.py, routers/      # FastAPI HTTP surface
│   └── cli.py                 # the `mie` command line
├── knowledge_base/            # THE CORPUS (dossiers, results, meta, literature, learning report)
│   ├── INDEX.md               # catalogue of all strategies + meta-hypotheses
│   ├── H-0xx_*.md             # per-strategy 7-step dossiers
│   ├── results/               # machine-readable results (versioned per matrix)
│   ├── literature/            # structured literature store (real citations)
│   ├── discovery/             # generated-strategy namespace (ledger + results)
│   └── workflows/             # research-workflow reports
├── data_csv/                  # bundled REAL sample OHLCV (AAPL, BTC, ETH, SPY, Gold; 2018-2026)
├── imports/                   # sample Pine Script(s)
├── tests/                     # test suite (pytest)
├── docs/                      # architecture + design documents
├── pyproject.toml             # packaging, dependencies, `mie` entry point
├── requirements.txt
└── .env.example
```

---

## Data

Five real daily OHLCV datasets (2018-01 → 2026-07) are bundled in `data_csv/`: AAPL, BTC/USD, ETH/USD,
SPY, XAU/USD (Gold). The CSV provider maps an asset like `BTC/USD` to `data_csv/BTCUSD_1d.csv`.

To add your own: drop a CSV named `<ASSET><QUOTE>_<timeframe>.csv` (columns `timestamp, open, high,
low, close, volume`; timestamp may be epoch seconds/ms or an ISO date) into `data_csv/`. See
`data_csv/README.md`. Live providers (Binance/FMP/CoinDesk) are available via `--provider` if you set
API keys in `.env`.

---

## How it works (one paragraph)

Every strategy runs through the **same frozen matrix** (`srp-matrix-v2`): expanding-window
walk-forward out-of-sample, a `{0,5,10}` bps cost/slippage ladder, buy-and-hold + matched
random-timing benchmarks, a **deflated Sharpe** (data-snooping) correction, Benjamini–Hochberg FDR,
ex-ante regime labelling, and a mechanical four-way verdict (`Supported / Not Supported /
Inconclusive / Graveyard`). Look-ahead is structurally impossible (positions are shifted forward one
bar). Matrix versions are content-addressed and immutable — `v1` (historical) and `v2` (active,
per-asset drawdown pooling) — so conclusions are never silently mixed. The included corpus already
has all 23 built-in strategies evaluated on v2 (result: 20 Graveyard, 3 Not Supported, 0 Supported —
zero false positives). Dossiers are produced by a **deterministic generator** (identical inputs →
byte-identical output) that only assembles structured spec + real literature; it never invents.

---

## Troubleshooting

- **`command not found: mie`** — activate the virtualenv first (`source .venv/bin/activate`) and run
  `pip install -e ".[dev]"`. Or invoke without installing: `python -m app.cli matrix`.
- **`python3: command not found` / wrong version** — install Python 3.10+ (`brew install python@3.12`),
  then recreate the venv. Check with `python3 --version`.
- **`ModuleNotFoundError: app`** — run commands from the **project root** (the folder containing
  `pyproject.toml`), or `pip install -e .` so `app` is importable, or set `PYTHONPATH=.`.
- **`numpy`/`pandas` build errors on Apple Silicon** — upgrade pip first (`pip install --upgrade pip`);
  modern wheels install without compiling. Ensure you're on Python 3.10+.
- **Knowledge Base looks empty / verdicts are "Not Yet Tested"** — the engine auto-locates
  `knowledge_base/` next to the code; if you moved it, set `MIE_KB_ROOT=/abs/path/to/knowledge_base`
  in `.env`. Verify with `mie kb show H-009` (should show a Graveyard verdict).
- **"only N bars … skipped"** during evaluation — the asset CSV is too short; use the bundled data or
  supply ≥ ~250 daily bars.
- **Live provider returns nothing** — Binance/FMP/CoinDesk need network access and (FMP/CoinDesk) an
  API key in `.env`; otherwise use `--provider csv` with the bundled data (the default).
- **`uvicorn: command not found`** — it installs with the dependencies; activate the venv, or run
  `python -m uvicorn app.main:app --reload`.

---

## What's included / not included

Included: the complete Python platform, the full 23-strategy corpus, real sample data, the discovery
engine, the deterministic dossier generator, the research workflow, tests, and design docs. Not
included: a web UI (Phase 3 — deliberately deferred). The platform is complete and usable from the
CLI and HTTP API today.
