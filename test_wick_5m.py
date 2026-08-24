import pandas as pd

from backtest import load_data, run_config
from strategy import add_atr
from test_5m import with_1h_atr

ASIA, NY = (22, 10), (19, 21)


def run_pair(symbol, cost, tag):
    df1h = load_data(symbol, "60d", "60m")
    add_atr(df1h, 10)
    df5 = with_1h_atr(load_data(symbol, "60d", "5m"))
    for label, df in (("1h", df1h), ("5m", df5)):
        for mode in ("stop", "stop-next"):
            s, _ = run_config(df, ASIA, NY, 0.0, 1.0, 1.0, mode, "opposite", 8,
                              cost=cost, skip_sunday=True, entry_bar_tp=False,
                              sl_mode="wick", wick_buffer=0.5)
            print(f"{tag} {label} {mode:10s}: {s.get('trades', 0):>3} trades, "
                  f"WR {s.get('win_rate_pct', 0):>5.1f}%, {s.get('total_r', 0):>+7.2f}R, "
                  f"PF {s.get('profit_factor', 0)}")


if __name__ == "__main__":
    print("=== WICK STOPS + OPPOSITE-LEVEL TP, last 60 days, 1h vs 5m ===")
    run_pair("GC=F", 0.5, "GOLD   ")
    run_pair("USDJPY=X", 0.005, "USDJPY ")
