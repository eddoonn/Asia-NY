import pandas as pd
import numpy as np


def add_atr(df: pd.DataFrame, n: int = 14) -> pd.DataFrame:
    h, l, c = df["High"], df["Low"], df["Close"]
    prev_c = c.shift(1)
    tr = pd.concat([h - l, (h - prev_c).abs(), (l - prev_c).abs()], axis=1).max(axis=1)
    df["atr"] = tr.rolling(n, min_periods=1).mean()
    return df


def label_sessions(df: pd.DataFrame, asia=(0, 9), ny_late=(18, 22)) -> pd.DataFrame:
    hour = df.index.hour
    df["session"] = "other"
    df.loc[(hour >= asia[0]) & (hour < asia[1]), "session"] = "asia"
    df.loc[(hour >= ny_late[0]) & (hour < ny_late[1]), "session"] = "ny_late"
    return df


def ny_reference_levels(df: pd.DataFrame) -> dict:
    levels = {}
    dates = sorted(df.index.date)
    for d in dates:
        day = df[df.index.date == d]
        late = day[day["session"] == "ny_late"]
        if len(late):
            levels[d] = {"high": float(late["High"].max()), "low": float(late["Low"].min())}
    return levels


def find_trades(df: pd.DataFrame, levels: dict, rr: float, atr_mult: float, exit_hour: int) -> list:
    trades = []
    dates = sorted(set(df.index.date))
    for d in dates:
        prev_dates = [x for x in dates if x < d]
        if not prev_dates:
            continue
        ref = None
        for pd_ in reversed(prev_dates[-5:]):
            if pd_ in levels:
                ref = levels[pd_]
                break
        if ref is None:
            continue
        asia = df[(df.index.date == d) & (df["session"] == "asia")]
        if len(asia) < 2:
            continue

        entry = None
        for ts, row in asia.iterrows():
            crossed_high = row["High"] >= ref["high"]
            crossed_low = row["Low"] <= ref["low"]
            if crossed_high and crossed_low:
                continue
            atr_here = df.loc[:ts, "atr"].iloc[-1]
            if np.isnan(atr_here) or atr_here <= 0:
                continue
            if crossed_high:
                entry = {
                    "date": d, "side": "short", "entry_time": ts, "entry": ref["high"],
                    "sl": ref["high"] + atr_mult * atr_here,
                    "tp": ref["high"] - rr * atr_mult * atr_here,
                    "risk": atr_mult * atr_here,
                }
                break
            if crossed_low:
                entry = {
                    "date": d, "side": "long", "entry_time": ts, "entry": ref["low"],
                    "sl": ref["low"] - atr_mult * atr_here,
                    "tp": ref["low"] + rr * atr_mult * atr_here,
                    "risk": atr_mult * atr_here,
                }
                break
        if entry is None:
            continue

        forward = df[(df.index > entry["entry_time"]) & (df.index.hour < exit_hour)]
        result_r = None
        exit_price = None
        exit_time = None
        reason = None
        for ts, row in forward.iterrows():
            if entry["side"] == "short":
                hit_sl = row["High"] >= entry["sl"]
                hit_tp = row["Low"] <= entry["tp"]
            else:
                hit_sl = row["Low"] <= entry["sl"]
                hit_tp = row["High"] >= entry["tp"]
            if hit_sl:
                result_r = -1.0
                exit_price = entry["sl"]
                exit_time = ts
                reason = "sl"
                break
            if hit_tp:
                result_r = rr
                exit_price = entry["tp"]
                exit_time = ts
                reason = "tp"
                break
        if result_r is None:
            last = forward.iloc[-1] if len(forward) else asia.iloc[-1]
            exit_time = forward.index[-1] if len(forward) else asia.index[-1]
            exit_price = float(last["Close"])
            pnl = (entry["entry"] - exit_price) if entry["side"] == "short" else (exit_price - entry["entry"])
            result_r = pnl / entry["risk"]
            reason = "time"

        trades.append({**entry, "exit_time": exit_time, "exit": exit_price, "r": round(result_r, 3), "reason": reason})
    return trades


def summarize(trades: list) -> dict:
    if not trades:
        return {"trades": 0}
    r = np.array([t["r"] for t in trades])
    wins = r[r > 0]
    losses = r[r <= 0]
    gross_win = wins.sum() if len(wins) else 0.0
    gross_loss = abs(losses.sum()) if len(losses) else 0.0
    equity = np.cumsum(r)
    dd = (np.maximum.accumulate(equity) - equity).max() if len(equity) else 0.0
    return {
        "trades": len(r),
        "win_rate_pct": round(100 * len(wins) / len(r), 2),
        "avg_r": round(float(r.mean()), 3),
        "total_r": round(float(r.sum()), 2),
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else float("inf"),
        "max_drawdown_r": round(float(dd), 2),
        "longs": sum(1 for t in trades if t["side"] == "long"),
        "shorts": sum(1 for t in trades if t["side"] == "short"),
    }
