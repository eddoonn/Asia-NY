import numpy as np
import pandas as pd

TICKS_PER_DAY = {"s": 86_400, "ms": 86_400_000, "us": 86_400_000_000, "ns": 86_400_000_000_000}


def atr_values(df: pd.DataFrame, n: int = 14) -> np.ndarray:
    h, l, c = df["High"], df["Low"], df["Close"]
    prev_c = c.shift(1)
    tr = pd.concat([h - l, (h - prev_c).abs(), (l - prev_c).abs()], axis=1).max(axis=1)
    return tr.rolling(n, min_periods=1).mean().values


def add_atr(df: pd.DataFrame, n: int = 14) -> pd.DataFrame:
    df["atr"] = atr_values(df, n)
    return df


def session_mask(index: pd.DatetimeIndex, window) -> np.ndarray:
    hours = index.hour
    s, e = window
    if s <= e:
        return np.asarray((hours >= s) & (hours < e))
    return np.asarray((hours >= s) | (hours < e))


def day_ids(index: pd.DatetimeIndex) -> np.ndarray:
    unit = getattr(index, "unit", "ns")
    return index.asi8 // TICKS_PER_DAY.get(unit, TICKS_PER_DAY["ns"])


def asia_day_ids(index: pd.DatetimeIndex, window):
    mask = session_mask(index, window)
    ids = day_ids(index)
    s, _ = window
    if s > window[1]:
        ids = np.where(index.hour >= s, ids + 1, ids)
    return mask, np.where(mask, ids, -1)


def ny_levels(index: pd.DatetimeIndex, high: np.ndarray, low: np.ndarray, window, opens: np.ndarray = None, closes: np.ndarray = None) -> dict:
    mask = session_mask(index, window)
    ids = day_ids(index)
    levels = {}
    for did in np.unique(ids[mask]):
        sel = mask & (ids == did)
        pos = np.where(sel)[0]
        o = float(opens[pos[0]]) if opens is not None else 0.0
        c = float(closes[pos[-1]]) if closes is not None else 0.0
        levels[int(did)] = (float(high[pos].max()), float(low[pos].min()), int(pos[-1]), o, c)
    return levels


def find_trades(df, asia_mask, asia_day_id, levels, rr, atr_mult, entry_buffer, entry_mode, tp_mode, exit_hour, atr=None, cost=0.0, skip_sunday=False, entry_bar_tp=True, sl_mode="atr", wick_buffer=0.5, trend_filter=False) -> list:
    o = df["Open"].values
    h = df["High"].values
    l = df["Low"].values
    c = df["Close"].values
    if atr is None:
        atr = df["atr"].values
    index = df.index
    hours = np.asarray(index.hour)
    n = len(o)

    trades = []
    asia_pos = np.where(asia_mask)[0]
    if len(asia_pos) == 0:
        return trades
    seg_ids = asia_day_id[asia_pos]
    unique_days = np.unique(seg_ids)
    starts = np.searchsorted(seg_ids, unique_days, side="left")
    ends = np.searchsorted(seg_ids, unique_days, side="right")

    for k, did in enumerate(unique_days):
        seg = asia_pos[starts[k]:ends[k]]
        if len(seg) < 2:
            continue
        first_pos = seg[0]
        if skip_sunday and index[first_pos].weekday() == 6:
            continue
        ref = None
        for p in range(int(did) - 1, int(did) - 8, -1):
            lv = levels.get(p)
            if lv and lv[2] < first_pos:
                ref = lv
                break
        if ref is None:
            continue

        entry = None
        for pos in seg:
            a = atr[pos]
            if not np.isfinite(a) or a <= 0:
                continue
            buf = entry_buffer * a
            rh, rl = ref[0], ref[1]
            short_trig = h[pos] >= rh + buf
            long_trig = l[pos] <= rl - buf
            if trend_filter and len(ref) >= 5:
                bull = ref[4] > ref[3]
                if bull:
                    short_trig = False
                else:
                    long_trig = False
            if short_trig and long_trig:
                continue
            if not (short_trig or long_trig):
                continue
            if entry_mode == "reclaim":
                if short_trig and c[pos] < rh:
                    side = "short"
                elif long_trig and c[pos] > rl:
                    side = "long"
                else:
                    continue
            else:
                side = "short" if short_trig else "long"
            level = rh + buf if side == "short" else rl - buf
            risk = atr_mult * a
            if side == "short":
                sl = level + risk
                tp = rl if tp_mode == "opposite" else level - rr * risk
            else:
                sl = level - risk
                tp = rh if tp_mode == "opposite" else level + rr * risk
            if sl_mode == "wick":
                if side == "short":
                    wick = h[pos]
                    sl = wick + wick_buffer * a
                    risk = sl - level
                    tp = rl if tp_mode == "opposite" else level - rr * risk
                else:
                    wick = l[pos]
                    sl = wick - wick_buffer * a
                    risk = level - sl
                    tp = rh if tp_mode == "opposite" else level + rr * risk
                if risk <= 0:
                    continue
            if entry_mode == "stop":
                entry = (side, pos, float(level), float(sl), float(tp), float(risk))
            elif entry_mode == "stop-next":
                if pos + 1 >= n:
                    continue
                entry = (side, pos + 1, float(o[pos + 1]), float(sl), float(tp), float(risk))
            else:
                if pos + 1 >= n:
                    continue
                entry = (side, pos + 1, float(o[pos + 1]), float(sl), float(tp), float(risk))
            break
        if entry is None:
            continue

        side, epos, entry_px, sl, tp, risk = entry
        result_r = exit_px = exit_pos = reason = None
        mae = mfe = 0.0
        for pos in range(epos, n):
            first_bar = (pos == epos)
            if side == "short":
                mae = max(mae, h[pos] - entry_px)
                mfe = max(mfe, entry_px - l[pos])
                hit_sl = h[pos] >= sl
                hit_tp = l[pos] <= tp and not (first_bar and not entry_bar_tp)
            else:
                mae = max(mae, entry_px - l[pos])
                mfe = max(mfe, h[pos] - entry_px)
                hit_sl = l[pos] <= sl
                hit_tp = h[pos] >= tp and not (first_bar and not entry_bar_tp)
            if hit_sl:
                gross = -risk
                result_r, exit_px, exit_pos, reason = (gross - cost) / risk, sl, pos, "sl"
                break
            if hit_tp:
                gross = (entry_px - tp) if side == "short" else (tp - entry_px)
                result_r, exit_px, exit_pos, reason = (gross - cost) / risk, tp, pos, "tp"
                break
            if hours[pos] == exit_hour and pos > epos:
                gross = (entry_px - c[pos]) if side == "short" else (c[pos] - entry_px)
                result_r, exit_px, exit_pos, reason = (gross - cost) / risk, float(c[pos]), pos, "time"
                break
        if result_r is None:
            pos = n - 1
            gross = (entry_px - c[pos]) if side == "short" else (c[pos] - entry_px)
            result_r, exit_px, exit_pos, reason = (gross - cost) / risk, float(c[pos]), pos, "eod"

        trades.append({
            "date": index[seg[0]].date(), "side": side,
            "entry_time": index[epos], "entry": entry_px,
            "sl": sl, "tp": tp,
            "exit_time": index[exit_pos], "exit": exit_px,
            "r": round(result_r, 3), "reason": reason,
            "mae_r": round(mae / risk, 2), "mfe_r": round(mfe / risk, 2),
        })
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

    streak = 0
    max_w = max_l = 0
    for x in r:
        if x > 0:
            streak = streak + 1 if streak > 0 else 1
        else:
            streak = streak - 1 if streak < 0 else -1
        max_w = max(max_w, streak)
        max_l = min(max_l, streak)

    return {
        "trades": len(r),
        "win_rate_pct": round(100 * len(wins) / len(r), 2),
        "avg_r": round(float(r.mean()), 3),
        "total_r": round(float(r.sum()), 2),
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else float("inf"),
        "max_drawdown_r": round(float(dd), 2),
        "avg_win_r": round(float(wins.mean()), 3) if len(wins) else 0.0,
        "avg_loss_r": round(float(losses.mean()), 3) if len(losses) else 0.0,
        "max_win_streak": max_w,
        "max_loss_streak": abs(max_l),
        "longs": sum(1 for t in trades if t["side"] == "long"),
        "shorts": sum(1 for t in trades if t["side"] == "short"),
    }
