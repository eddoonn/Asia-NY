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
python backtest.py                       # baseline (video-literal) config
python sweep.py                          # grid search over sessions/entries/targets
```

Tuned config (best from sweep, GC=F 180d hourly):

```bash
python backtest.py --asia 22-9 --ny-late 19-22 --entry-mode stop --entry-buffer 0.5 \
  --atr-mult 1.5 --rr 0.5 --exit-hour 9 --period 180d
```

Result: 145 trades, 73.8% win rate, PF 1.75, +22.7R, max DD 3.9R. Stable across both 90d halves (73.6% / 74.0% WR).

## What the sweep found

| Parameter | Baseline (video-literal) | Tuned | Why |
|---|---|---|---|
| Asia window | 00:00–09:00 UTC | **22:00–09:00 UTC** | sweep starts right at 18:00 ET futures open, not midnight UTC |
| NY-late window | 18:00–22:00 | **19:00–22:00 UTC** | final 3h before futures close hold the real liquidity |
| Entry | touch of level | **level + 0.5×ATR sweep** | a mere poke isn't a grab; require penetration |
| Target | 2R | **0.5R** | the reversal is a quick snap-back, not a trend ride |
| Stop | 1.0×ATR | **1.5×ATR** | wider stop survives the post-sweep chop |
| Baseline result | −39.8R, 27.8% WR | +22.7R, 73.8% WR | |

Caveats: 3,072-config grid search on one instrument/period = overfitting risk; fills are idealized (no spread/slippage/commission); hourly bars hide intrabar sweep-then-reverse sequences, which cuts the real win rate of the "90% version" but also skips some losers.

Note: Yahoo hourly data only reaches back ~730 days, so examples from the video (e.g., Tue Jun 14, 2022) can't be replayed with this data source.

## Outputs

- `results/trades.csv` – every trade with entry/exit, side, reason (sl/tp/time), R multiple
- `results/equity_curve.png` – cumulative R curve

## Tuning ideas

- `--asia-start/--asia-end` to match your broker's Asia hours (e.g., Tokyo 9am JST = 00:00 UTC)
- `--ny-late-start/--ny-late-end` for what counts as "NY's final hours" (e.g., 19–21 UTC covers post-2pm ET)
- `--rr` and `--atr-mult` for payoff vs. stop tightness
