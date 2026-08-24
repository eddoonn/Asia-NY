import pandas as pd

tags = {"gold_real": "GOLD", "eurusd_real": "EURUSD", "gbpusd_real": "GBPUSD", "usdjpy_real": "USDJPY"}
frames = {}
for tag, name in tags.items():
    m = pd.read_csv(f"results/months_2026_{tag}.csv")
    frames[tag] = m.set_index("month")

months = sorted(set().union(*[set(f.index) for f in frames.values()]))
header = "month     " + "".join(f"{n:>9}" for n in tags) + "   PORTFOLIO  trades"
print(header)
tot = 0.0
tot_trades = 0
for m in months:
    rs = [float(frames[n]["total_r"].get(m, 0.0)) for n in tags]
    tr = [int(frames[n]["trades"].get(m, 0)) for n in tags]
    print(f"{m} " + "".join(f"{r:>+9.2f}" for r in rs) + f"  {sum(rs):>+9.2f}  {sum(tr):>5}")
    tot += sum(rs)
    tot_trades += sum(tr)

print()
for n in tags:
    f = frames[n]
    trades = int(f["trades"].sum())
    wr = 100 * f["wins"].sum() / max(trades, 1)
    print(f"{n}: {trades} trades, WR {wr:.1f}%, {f['total_r'].sum():+.2f}R")

print(f"\nPORTFOLIO: {tot_trades} trades, {tot:+.2f}R = {tot * 100:+,.0f} USD at 100/trade")
neg = [m for m in months if sum(float(frames[n]["total_r"].get(m, 0.0)) for n in tags) < 0]
print("negative portfolio months:", neg if neg else "NONE")
