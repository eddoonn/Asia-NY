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

Tuned config (walk-forward robust winner, GC=F 365d hourly):

```bash
python backtest.py --asia 22-10 --ny-late 19-21 --entry-buffer 1.0 --atr-mult 1.0 \
  --atr-len 10 --rr 0.75 --exit-hour 8 --period 365d
```

| Window | Trades | Win rate | Total R | Profit factor | Max DD |
|---|---|---|---|---|---|
| Last 60d | 46 | 67.4% | +7.8R | 1.52 | 3.3R |
| Last 180d | 138 | 71.7% | +35.4R | 1.96 | 3.3R |
| Last 365d | 272 | 72.4% | +74.5R | 2.05 | 5.5R |
| 365d thirds | ~90 each | — | +26.5 / +28.1 / +19.2 | — | — |

## What the sweep found

| Parameter | Baseline (video-literal) | Tuned | Why |
|---|---|---|---|
| Asia window | 00:00–09:00 UTC | **22:00–10:00 UTC** | sweep starts right at 18:00 ET futures open, not midnight UTC |
| NY-late window | 18:00–22:00 | **19:00–21:00 UTC** | final 2h before futures close hold the real liquidity |
| Entry | touch of level | **level + 1.0×ATR sweep** | a mere poke isn't a grab; require deep penetration |
| Target | 2R | **0.75R** | the reversal is a quick snap-back, not a trend ride |
| Stop | 1.0×ATR14 | **1.0×ATR10** | faster ATR adapts to the post-close volatility shift |
| Baseline result | −39.8R, 27.8% WR | +74.5R, 72.4% WR | |

Methodology: 6,480-config grid on 365d of hourly data; 2,137 configs profitable in *all three* 4-month segments; ranked by total R among those. The winner's edge is stable across thirds, and long/short counts are balanced (134/138).

Caveats: still in-sample selection (robustness filter reduces but doesn't eliminate overfitting); fills are idealized (no spread/slippage/commission — with gold spread ~0.3–0.5 and 0.75R targets, costs matter); hourly bars hide intrabar sweep-then-reverse sequences.

Note: Yahoo hourly data only reaches back ~730 days, so examples from the video (e.g., Tue Jun 14, 2022) can't be replayed with this data source.

## Outputs

- `results/trades.csv` – every trade with entry/exit, side, reason (sl/tp/time), R multiple
- `results/equity_curve.png` – cumulative R curve

## Tuning ideas

- `--asia-start/--asia-end` to match your broker's Asia hours (e.g., Tokyo 9am JST = 00:00 UTC)
- `--ny-late-start/--ny-late-end` for what counts as "NY's final hours" (e.g., 19–21 UTC covers post-2pm ET)
- `--rr` and `--atr-mult` for payoff vs. stop tightness
