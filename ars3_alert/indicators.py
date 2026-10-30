"""Indicators: causal daily values and flags built from the loaded sources.

Nothing here looks ahead: row t only uses data available on day t, so the live runner (last row) and
the backtest (every row) share one code path. A flag is <NA> when an input is missing; it is never False.
"""
import numpy as np
import pandas as pd


def stride_ma(close, weeks):
    """Weekly-close SMA anchored at every day: mean of the closes 7 days apart ending that day."""
    lane = np.arange(len(close)) % 7
    return close.groupby(lane).transform(lambda c: c.rolling(weeks).mean())


def expanding_pct(s, min_history):
    """Percentile rank of each value among all non-missing values up to that day."""

    def rank(window):
        today = window[-1]
        return np.nan if np.isnan(today) else (window[~np.isnan(window)] <= today).mean()

    return s.expanding(min_periods=min_history).apply(rank, raw=True)


def days_since_halving(index, halvings):
    h = pd.DatetimeIndex(pd.to_datetime(halvings)).as_unit("ns").sort_values()
    pos = h.searchsorted(index, side="right") - 1  # latest halving on or before each day
    days = (index - h[np.clip(pos, 0, None)]).days.to_numpy(dtype=float)
    return pd.Series(np.where(pos >= 0, days, np.nan), index=index)


def aligned(data, cfg, index):
    """Every non-price source on the price index, shifted by its publication lag and gap-filled."""
    cols = {}
    for name, s in data.items():
        if name == "price":
            continue
        src = cfg["sources"][name.split(".")[0]]
        if src.get("flow"):  # flows: a day without a print means zero, until the data goes stale
            end = s.index.max() + pd.Timedelta(days=src.get("ffill_days", 3))
            s = s.reindex(pd.date_range(s.index.min(), end), fill_value=0.0)
        s = s.shift(src.get("lag_days", 0), freq="D").reindex(index)
        cols[name] = s if src.get("flow") else s.ffill(limit=src.get("ffill_days", 3))
    return pd.DataFrame(cols, index=index)


def _flag(cond, *inputs):
    """Nullable boolean that is <NA> wherever any input is missing."""
    missing = pd.concat(inputs, axis=1).isna().any(axis=1)
    return cond.astype("boolean").mask(missing)


def compute(data, cfg):
    """Returns (values, flags): float readings and nullable-boolean flags, both indexed by day."""
    px = data["price"]
    price = px.reindex(pd.date_range(px.index.min(), px.index.max())).ffill(limit=2)
    x = aligned(data, cfg, price.index)
    col = lambda name: x[name] if name in x else pd.Series(np.nan, index=price.index)
    s, r, rc = cfg["stress"], cfg["regime"], cfg["recovery"]
    close, low = price["close"], price["low"]

    stablecoin = col("stablecoin")
    v = pd.DataFrame({
        "close": close,
        "fast_ma": stride_ma(close, r["fast_ma_weeks"]),
        "slow_ma": stride_ma(close, r["slow_ma_weeks"]),
        "sth_cost": col("onchain.sth_cost_basis"),
        "true_mean": col("onchain.true_market_mean"),
        "fng": col("fng"),
        "mnav": col("mnav"),
        "etf_flow": col("etf").rolling(s["etf_window_days"]).sum(),
        "stablecoin_chg_pct": (stablecoin / stablecoin.shift(s["stablecoin_window_days"]) - 1) * 100,
        "lth_loss": col("onchain.lth_realized_loss").abs().rolling(s["lth_loss_window_days"]).mean(),  # size of the loss, whatever the sign convention
        "funding": col("funding").rolling(s["funding_window_days"]).mean(),
        "halving_days": days_since_halving(price.index, cfg["phase"]["halvings"]),
    })
    pct = lambda name: expanding_pct(v[name], s["min_history_days"])
    etf_pct, lth_pct, funding_pct = pct("etf_flow"), pct("lth_loss"), pct("funding")
    etf_weeks = [col("etf").rolling(7).sum().shift(7 * k) for k in range(rc["etf_positive_weeks"])]
    lo, hi = cfg["phase"]["top_zone_days"]

    flags = pd.DataFrame({
        "phase.top_zone": _flag((v.halving_days >= lo) & (v.halving_days <= hi), v.halving_days),
        "regime.above_fast_ma": _flag(close > v.fast_ma, v.fast_ma),
        "regime.above_slow_ma": _flag(close > v.slow_ma, v.slow_ma),
        "regime.above_sth_cost": _flag(close > v.sth_cost, v.sth_cost),
        "regime.above_true_mean": _flag(close > v.true_mean, v.true_mean),
        "stress.fng": _flag(v.fng < s["fng_below"], v.fng),
        "stress.mnav": _flag(v.mnav < s["mnav_below"], v.mnav),
        "stress.etf_outflow": _flag(etf_pct <= s["etf_percentile"], etf_pct),
        "stress.stablecoin": _flag(v.stablecoin_chg_pct <= s["stablecoin_change_pct"], v.stablecoin_chg_pct),
        "stress.lth_loss": _flag(lth_pct >= s["lth_loss_percentile"], lth_pct),
        "stress.slow_ma_touch": _flag(low <= v.slow_ma * (1 + s["ma200w_touch_pct"] / 100), v.slow_ma),
        "stress.funding": _flag(funding_pct <= s["funding_percentile"], funding_pct),
        "recovery.etf_positive": _flag(pd.concat(etf_weeks, axis=1).gt(0).all(axis=1), *etf_weeks),
        "recovery.funding_neutral": _flag(v.funding.abs() <= rc["funding_neutral_abs"], v.funding),
    })
    return v, flags
