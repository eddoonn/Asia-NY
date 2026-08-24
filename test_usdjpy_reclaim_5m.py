from backtest import load_data, run_config
from strategy import add_atr
from test_5m import with_1h_atr

print("=== USDJPY wick+opposite+reclaim, last 60 days: 1h vs 5m ===")
df1h = load_data("USDJPY=X", "60d", "60m")
add_atr(df1h, 10)
df5 = with_1h_atr(load_data("USDJPY=X", "60d", "5m"))
for label, df in (("1h", df1h), ("5m", df5)):
    s, trades = run_config(df, (22, 10), (19, 21), 0.0, 1.0, 1.0, "reclaim", "opposite", 8,
                           cost=0.005, skip_sunday=True, entry_bar_tp=False,
                           sl_mode="wick", wick_buffer=0.5)
    print(f"{label}: {s.get('trades', 0):>3} trades, WR {s.get('win_rate_pct', 0):>5.1f}%, "
          f"{s.get('total_r', 0):>+7.2f}R, PF {s.get('profit_factor', 0)}")
    if label == "5m" and trades:
        for t in trades:
            print("   ", t["date"], t["side"], "entry", round(t["entry"], 3),
                  "->", t["reason"], f"{t['r']:+.2f}R")
