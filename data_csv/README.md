# Real OHLCV data — drop CSVs here

The CSV provider (`--provider csv`) reads real historical bars from this folder. This is the
primary way to run STEP 5 with scientific validity in a locked-down environment.

## File naming

```
<ASSET><QUOTE>_<timeframe>.csv     e.g.  BTCUSDT_1d.csv   ETHUSDT_4h.csv   AAPL_1d.csv
```
The engine strips `/` from the asset, so `BTC/USDT` → `BTCUSDT_<tf>.csv`.

## Columns (case-insensitive; common aliases accepted)

```
timestamp, open, high, low, close, volume
```
`timestamp` may be ms epoch, seconds epoch, or an ISO/parseable date. `volume` is optional.
Aliases handled automatically: `date/time/datetime/open_time` → timestamp; `o/h/l/c/v`,
`adj close` → close, etc.

## Where to export from

Any exchange or vendor: Binance/CoinDesk/FMP/Polygon exports, TradingView chart "Export chart
data…", Yahoo Finance downloads, your own database. The data is treated as **real** (`is_real=True`)
and may set a dossier verdict — so only put genuine market data here.

*Nothing in this folder is committed by default except this README; add your own CSVs locally.*
