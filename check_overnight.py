import pandas as pd

from backtest import load_data, run_config
from strategy import add_atr

PAIRS = [("USDJPY", "USDJPY=X", 0.005), ("EURJPY", "EURJPY=X", 0.008),
         ("GBPJPY", "GBPJPY=X", 0.012), ("AUDJPY", "AUDJPY=X", 0.008),
         ("EURUSD", "EURUSD=X", 0.0001), ("GBPUSD", "GBPUSD=X", 0.00015)]

rows = []
for name, sym, cost in PAIRS:
    df = load_data(sym, "30d", "60m")
    add_atr(df, 10)
    for sname, trig, ref, exit_h, ebuf, wb in (
            ("Tokyo", (22, 10), (19, 21), 8, 1.0, 0.5),):
        _, trades = run_config(df, trig, ref, 0.0, 1.0, ebuf, "reclaim", "opposite",
                               exit_h, cost=cost, skip_sunday=True, entry_bar_tp=False,
                               sl_mode="wick", wick_buffer=wb)
        for t in trades:
            et = pd.Timestamp(t["entry_time"])
            if et >= pd.Timestamp("2026-08-24 21:00", tz="UTC"):
                sig = (et - pd.Timedelta(hours=1)).tz_convert("Europe/London")
                lon = et.tz_convert("Europe/London")
                rows.append(f"{name} {t['side']:>9} | signal bar closed {sig:%a %H:%M} London | "
                            f"entry {lon:%a %H:%M} London | exit {t['reason']} {t['r']:+.2f}R")

print("=== Engine: overnight + today signals (Tokyo profile) ===")
for r in rows:
    print(" ", r)
if not rows:
    print("  NONE — no confirmed sweep+reclaim since Aug 24 22:00 UTC")

for name, sym, cost in [("EURUSD", "EURUSD=X", 0.0001), ("GBPUSD", "GBPUSD=X", 0.00015)]:
    df = load_data(sym, "30d", "60m")
    add_atr(df, 10)
    _, trades = run_config(df, (7, 13), (22, 10), 0.0, 1.0, 0.5, "reclaim", "opposite",
                           17, cost=cost, skip_sunday=True, entry_bar_tp=False,
                           sl_mode="wick", wick_buffer=0.25)
    for t in trades:
        et = pd.Timestamp(t["entry_time"])
        if et >= pd.Timestamp("2026-08-25 05:00", tz="UTC"):
            sig = (et - pd.Timedelta(hours=1)).tz_convert("Europe/London")
            lon = et.tz_convert("Europe/London")
            print(f"London {name} {t['side']:>9} | signal bar closed {sig:%a %H:%M} London | "
                  f"entry {lon:%a %H:%M} London | exit {t['reason']} {t['r']:+.2f}R")
