from backtest import load_data, run_config
from strategy import add_atr

INSTRUMENTS = [("GOLD", "GC=F", 0.5), ("EURUSD", "EURUSD=X", 0.0001),
               ("GBPUSD", "GBPUSD=X", 0.00015), ("USDJPY", "USDJPY=X", 0.005)]

for name, sym, cost in INSTRUMENTS:
    df = load_data(sym, "365d", "60m")
    add_atr(df, 10)
    split = int(len(df) * 0.7)
    for mode in ("stop", "stop-next"):
        s_tr, t_tr = run_config(df.iloc[:split], (22, 10), (19, 21), 0.0, 1.0, 1.0, mode,
                                "opposite", 8, cost=cost, skip_sunday=True, entry_bar_tp=False,
                                sl_mode="wick", wick_buffer=0.5)
        s_te, t_te = run_config(df.iloc[split:], (22, 10), (19, 21), 0.0, 1.0, 1.0, mode,
                                "opposite", 8, cost=cost, skip_sunday=True, entry_bar_tp=False,
                                sl_mode="wick", wick_buffer=0.5)
        tr = s_tr.get("trades", 0)
        te = s_te.get("trades", 0)
        print(f"{name} {mode:10s}: train {tr:>3} trades {s_tr.get('total_r', 0):>+8.2f}R "
              f"(WR {s_tr.get('win_rate_pct', 0):>5.1f}%) | test {te:>3} trades "
              f"{s_te.get('total_r', 0):>+8.2f}R (WR {s_te.get('win_rate_pct', 0):>5.1f}%)")
    if name == "GOLD" and t_tr:
        print("sample wick trades (GOLD train):")
        for t in t_tr[:5]:
            print("  ", t["date"], t["side"], "entry", round(t["entry"], 2),
                  "sl", round(t["sl"], 2), "tp", round(t["tp"], 2),
                  "->", t["reason"], t["r"], "R")
