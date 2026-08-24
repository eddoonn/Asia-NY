import os

import pandas as pd

from backtest import load_data, run_config
from strategy import add_atr

ASIA = (22, 10)
NY_LATE = (19, 21)
BUF, ATR_MULT, ATR_LEN, RR, EXIT_HOUR = 1.0, 1.0, 10, 0.75, 8


def with_1h_atr(df5):
    h1 = df5.resample("1h").agg({"Open": "first", "High": "max", "Low": "min", "Close": "last"}).dropna()
    atr_h1 = add_atr(h1.copy(), ATR_LEN)["atr"]
    mapped = []
    for ts in df5.index:
        hour_start = ts.floor("h")
        mapped.append(atr_h1.get(hour_start))
    df5 = df5.copy()
    df5["atr"] = mapped
    df5["atr"] = df5["atr"].ffill()
    return df5


def run(symbol, cost, tag):
    df1h = load_data(symbol, "60d", "60m")
    add_atr(df1h, ATR_LEN)
    s1, t1 = run_config(df1h, ASIA, NY_LATE, RR, ATR_MULT, BUF, "stop", "rr", EXIT_HOUR, cost=cost)

    df5 = load_data(symbol, "60d", "5m")
    df5 = with_1h_atr(df5)
    s5, t5 = run_config(df5, ASIA, NY_LATE, RR, ATR_MULT, BUF, "stop", "rr", EXIT_HOUR, cost=cost)

    print(f"{tag}: 1h -> {s1.get('trades')} trades, WR {s1.get('win_rate_pct')}%, "
          f"{s1.get('total_r')}R, PF {s1.get('profit_factor')}, DD {s1.get('max_drawdown_r')}R")
    print(f"{tag}: 5m -> {s5.get('trades')} trades, WR {s5.get('win_rate_pct')}%, "
          f"{s5.get('total_r')}R, PF {s5.get('profit_factor')}, DD {s5.get('max_drawdown_r')}R")
    return s1, s5


if __name__ == "__main__":
    run("GC=F", 0.3, "GOLD   ")
    run("EURUSD=X", 0.00004, "EURUSD ")
    run("USDJPY=X", 0.002, "USDJPY ")
