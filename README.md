# Sweep Reclaim — Asia/London Session Liquidity Strategies

Two validated main strategies, forward-tested on eToro demo:

1. **Tokyo Reclaim** — Tokyo session sweeps the prior NY-late (19:00–21:00 UTC) extreme and *fails to hold it* (hourly close back inside) → enter with the market; SL beyond the sweep wick + 0.5×ATR; TP at the opposite NY level. Pairs: USD/EUR/GBP/AUD-JPY. 2026 verified: 74 trades, 74.3% WR, +45.7R, t=+4.84; replicated out-of-sample on 3 untouched crosses.
2. **London Reclaim** — London session (07:00–13:00 UTC) sweeps the current Asia range (built dynamically, no look-ahead) and reclaims it → same mechanics. Pairs: EURUSD, GBPUSD, USDJPY. 2026 verified: 133 trades, 45.1% WR, +157.1R, t=+4.27; 3/3 FX pairs positive in walk-forward train+test. (London-GOLD excluded: failed walk-forward, t=0.93.)

Combined 2026 verified: 207 trades, +202.7R, t=+5.32, maxDD 9.87R, 7/8 months positive. Every trade independently re-derived from raw OHLC (207/207 match). Full adversarial audit passed: 3× costs, SL slippage, parameter neighborhood, bootstrap P(loss)=0%, entry-gap, sub-period, leave-one-out.

**Execution:** `reclaim_monitor.py` runs hourly; on a confirmed sweep+reclaim hourly close it places a market order with attached SL/TP. `end_session.py` (08:05 & 17:05 UTC) closes leftovers and reports P&L to Discord.

## Original research log

The original "Asia session hunts New York's final-hours high/low, then reverses" gold strategy — and why the naive hourly version failed — is documented below and in the research scripts.

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

Methodology: 6,480-config grid; 3-segment consistency filter; then **true out-of-sample test** — sweep on first 70% of data (Jun 2025–Apr 2026), validate top 20 configs on untouched last 30% (Apr–Aug 2026), with 0.3 price-unit round-trip cost modeled.

Out-of-sample results (with costs): **20/20 configs profitable on the test set, median +16.5R in ~4 months.** Best: +17.9R, 68.7% WR, PF 1.75 over 83 test trades. The parameter neighborhood is flat (many near-identical configs cluster at the top), which indicates a real effect rather than a knife-edge fit.

Costs: `--cost 0.3` models ~3 ticks round trip (spread + slippage + fees). The strategy's edge survives: 365d net drops only from +74.5R to +68.5R (PF 1.95) because the small 0.75R targets are hit often while costs are fixed per trade.

Other engine features: `--account/--risk-pct` dollar sizing report, monthly R breakdown, win/loss streaks, avg win/loss.

Caveats: still single instrument (COMEX gold futures); hourly bars hide intrabar sweep-then-reverse sequences; Yahoo data quality during rolls/holidays is imperfect.

Note: Yahoo hourly data only reaches back ~730 days, so examples from the video (e.g., Tue Jun 14, 2022) can't be replayed with this data source.

## Outputs

- `results/trades.csv` – every trade with entry/exit, side, reason (sl/tp/time), R multiple
- `results/equity_curve.png` – cumulative R curve

## Auto-trading (eToro or IG)

Fully hands-off overnight execution. Orders rest **server-side** — no bot needs to be awake:

- `place_orders.py` (~23:05 London): computes the two sweep triggers and places resting entry orders with attached SL (1×ATR10) and TP (0.75R), sized to risk 1%
- `end_session.py` (~09:05 London): cancels unfilled orders and closes anything still open (the backtest's time exit)
- Both post confirmations to Discord

Set `BROKER=etoro` or `BROKER=ig` in `.env` (or pass `--broker`).

### eToro

Uses the public API (https://api-portal.etoro.com): **MIT (market-if-touched) orders** with `triggerRate` = server-side sweep entry; SL/TP attached at creation. Gold = CFD (no spread betting → CGT applies; eToro gold spread ~0.45–0.65, backtest still profitable at 0.5 cost: +64R/yr, PF 1.88).

```bash
# .env: ETORO_MODE=demo, ETORO_PUBLIC_KEY=..., ETORO_USER_KEY=... (account settings, verified account)
python place_orders.py --broker etoro --dry-run
python place_orders.py --broker etoro
```

Sizing: units (oz) = risk$ ÷ stop distance. `end_session.py` looks up each order: pending → cancel; filled → market-close the position.

### IG

Spread betting (tax-free) via REST API; stop orders with attached SL/TP. See `.env.example` for IG_* keys; epic search snippet below.

```python
from config import load_env; load_env()
from ig_client import IGClient
ig = IGClient(); ig.login()
for m in ig.search("GOLD")["markets"]: print(m["epic"], m["instrumentName"])
```

Sizing: `size = risk£ / stop_distance` (£ per point) for spread bets; for CFDs set `IG_CURRENCY=USD` and check contract size/min deal size.

### Schedule (Task Scheduler, local time — 23:05 BST / 22:05 GMT place, 09:05 London end)

```powershell
schtasks /Create /SC DAILY /ST 23:05 /TR "python \"C:\...\asia-gold-reversal\place_orders.py\"" /F
schtasks /Create /SC DAILY /ST 09:05 /TR "python \"C:\...\asia-gold-reversal\end_session.py\"" /F
```

**Run the demo account for at least 4 weeks.** Verify: sweep fills match the backtest's stop-order fill assumption, spread at 23:00–02:00 is near the modeled cost, and TP fills aren't slipping. Leveraged CFDs are high risk — most retail accounts lose money.

## Discord daily signal

`notify.py` posts the current session state to a Discord channel via webhook:

- **Armed** (gray): NY-late high/low + exact sweep trigger prices for the session
- **Triggered/Open** (blue): sweep happened — entry/stop/target
- **Closed** (green/red): result in R

```bash
echo DISCORD_WEBHOOK=... > .env   # webhook URL, gitignored
python notify.py --test           # verify delivery
python notify.py                  # post current session state
```

Schedule it with Task Scheduler (run at/after 22:10 UTC for the armed message, and optionally again ~08:10 UTC for the result):

```powershell
schtasks /Create /SC DAILY /ST 18:10 /TR "python \"C:\Users\Test Edon\Desktop\MatchForecast codes\asia-gold-reversal\notify.py\"" /F
```

(`/ST` is local time — 18:10 EDT = 22:10 UTC; adjust for your timezone. Re-running later in the session updates the message to Triggered/Closed.)

## Tuning ideas

- `--asia-start/--asia-end` to match your broker's Asia hours (e.g., Tokyo 9am JST = 00:00 UTC)
- `--ny-late-start/--ny-late-end` for what counts as "NY's final hours" (e.g., 19–21 UTC covers post-2pm ET)
- `--rr` and `--atr-mult` for payoff vs. stop tightness
