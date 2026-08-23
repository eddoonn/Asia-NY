# Asia Session Gold Liquidity Grab Reversal

Backtest of the "Asia session hunts New York's final-hours high/low, then reverses" strategy for gold.

## Logic

1. **Reference levels** – Each day's NY late window (default 18:00–22:00 UTC) produces a `high` and `low`. These are "resting liquidity" targets.
2. **Asia session** (default 00:00–09:00 UTC) sweeps one of those levels:
   - Price trades above prior NY-late high → **SHORT** at that high.
   - Price trades below prior NY-late low → **LONG** at that low.
3. **Stop loss** = entry ± `atr_mult * ATR(14)` at trigger time.
4. **Take profit** = fixed R multiple of the risk (`--rr`, default 2R).
5. **Time exit** at end of holding window (default: any bar before 09:00 UTC) if neither SL nor TP hits.

One trade per day maximum; first level touched wins. Ambiguous candles touching both levels in the same hour are skipped.

## Usage

```bash
pip install -r requirements.txt
python backtest.py                       # gold futures GC=F, last 60 days of 1h bars
python backtest.py --symbol XAUUSD=X --rr 1.5 --atr-mult 1.5
```

Note: Yahoo hourly data is limited (~730 days max). `XAUUSD=X` is spot-ish FX quoting; `GC=F` is COMEX futures. Neither includes broker spread/slippage — results are idealized fills.

## Outputs

- `results/trades.csv` – every trade with entry/exit, side, reason (sl/tp/time), R multiple
- `results/equity_curve.png` – cumulative R curve

## Tuning ideas

- `--asia-start/--asia-end` to match your broker's Asia hours (e.g., Tokyo 9am JST = 00:00 UTC)
- `--ny-late-start/--ny-late-end` for what counts as "NY's final hours" (e.g., 19–21 UTC covers post-2pm ET)
- `--rr` and `--atr-mult` for payoff vs. stop tightness
