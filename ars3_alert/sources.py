"""Data sources. Every fetcher returns a daily pandas object (UTC dates) or raises SourceError."""
import logging
import time
from contextlib import contextmanager
from functools import partial
from pathlib import Path

import ccxt
import numpy as np
import pandas as pd
import requests

from common.env import get_env
from common.retry import retry

log = logging.getLogger(__name__)
ONCHAIN_METRICS = ("sth_cost_basis", "lth_realized_loss", "true_market_mean")


class SourceError(Exception):
    """A source is unreachable, unconfigured or returned an unexpected payload."""


@contextmanager
def _unexpected_payload(name):
    try:
        yield
    except (KeyError, TypeError, ValueError, IndexError) as e:
        raise SourceError(f"{name}: unexpected payload ({type(e).__name__}: {e})") from e


def _daily(dates, values, how="last"):
    """Series indexed by UTC date; several points on one day are collapsed with `how`."""
    index = pd.DatetimeIndex(dates).normalize().as_unit("ns")
    s = pd.Series(np.asarray(values, dtype=float), index=index)
    return getattr(s.groupby(level=0), how)().sort_index()


@retry(times=3, on=(requests.ConnectionError, requests.Timeout))
def _get_json(url, headers=None, params=None):
    r = requests.get(url, headers=headers, params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def _paginate(fetch_page, since_ms, ts_of):
    """Collect rows page by page from `since_ms` until the source has nothing newer."""
    fetch_page = retry(times=3, on=(ccxt.NetworkError,))(fetch_page)
    rows, now_ms = [], time.time() * 1000
    while since_ms < now_ms:
        page = fetch_page(since_ms)
        if not page or ts_of(page[-1]) + 1 <= since_ms:
            break
        rows += page
        since_ms = ts_of(page[-1]) + 1
    return rows


def fetch_price(cfg):
    p = cfg["price"]
    ex = getattr(ccxt, p["exchange"])({"enableRateLimit": True})
    rows = _paginate(
        lambda since: ex.fetch_ohlcv(p["symbol"], "1d", since=since, limit=1000),
        ex.parse8601(f"{p['since']}T00:00:00Z"),
        ts_of=lambda r: r[0],
    )
    if not rows:
        raise SourceError("price: no candles returned")
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
    df.index = pd.DatetimeIndex(pd.to_datetime(df.pop("ts"), unit="ms")).normalize().as_unit("ns")
    df = df[~df.index.duplicated(keep="last")].sort_index()
    return df[df.index < pd.Timestamp.now("UTC").tz_localize(None).normalize()]  # drop the unfinished candle


def fetch_fng(cfg):
    payload = _get_json("https://api.alternative.me/fng/", params={"limit": 0, "format": "json"})
    with _unexpected_payload("fng"):
        data = payload["data"]
        return _daily(pd.to_datetime([int(d["timestamp"]) for d in data], unit="s"), [d["value"] for d in data])


def fetch_funding(cfg):
    c = cfg["sources"]["funding"]
    ex = getattr(ccxt, c["exchange"])({"enableRateLimit": True})
    rows = _paginate(
        lambda since: ex.fetch_funding_rate_history(c["symbol"], since=since, limit=1000),
        ex.parse8601(f"{c['since']}T00:00:00Z"),
        ts_of=lambda r: r["timestamp"],
    )
    if not rows:
        raise SourceError("funding: no rows returned")
    with _unexpected_payload("funding"):
        dates = pd.to_datetime([r["timestamp"] for r in rows], unit="ms")
        return _daily(dates, [r["fundingRate"] for r in rows], how="mean")


def fetch_stablecoin(cfg):
    rows = _get_json("https://stablecoins.llama.fi/stablecoincharts/all")
    with _unexpected_payload("stablecoin"):
        dates = pd.to_datetime([int(r["date"]) for r in rows], unit="s")
        return _daily(dates, [r["totalCirculatingUSD"]["peggedUSD"] for r in rows])


def _json_series(name, url, date_key, value_key, rows_key=None, unit=None, **request):
    """Generic JSON-rows source. Field names are configured, so a mismatch lists the keys it found."""
    payload = _get_json(url, **request)
    rows = payload
    try:
        rows = payload[rows_key] if rows_key else payload
        raw_dates = [r[date_key] for r in rows]
        dates = pd.to_datetime([int(d) for d in raw_dates], unit=unit) if unit else pd.to_datetime(raw_dates)
        return _daily(dates, [r[value_key] for r in rows])
    except (KeyError, TypeError, ValueError, IndexError) as e:
        keys = list(rows[0]) if isinstance(rows, list) and rows else list(rows) if isinstance(rows, dict) else []
        raise SourceError(f"{name}: unexpected payload ({type(e).__name__}: {e}); keys found: {keys[:15]}") from e


def fetch_etf(cfg):
    c = cfg["sources"]["etf"]
    return _json_series("etf", c["url"], c["date_key"], c["value_key"], c.get("rows_key"))


def fetch_mnav(cfg):
    path = Path(cfg["sources"]["mnav"]["path"])
    if not path.exists():
        raise SourceError(f"mnav: manual file {path} not found (header: date,mnav)")
    with _unexpected_payload("mnav"):
        df = pd.read_csv(path)
        return _daily(pd.to_datetime(df["date"]), df["mnav"])


def fetch_onchain(cfg, metric):
    c = cfg["sources"]["onchain"]
    spec, token = c["metrics"].get(metric), get_env(c["token_env"])
    if not spec:
        raise SourceError(f"onchain.{metric}: endpoint not configured")
    if not token:
        raise SourceError(f"onchain.{metric}: {c['token_env']} is not set")
    return _json_series(
        f"onchain.{metric}", c["base_url"] + spec["path"], spec["date_key"], spec["value_key"],
        rows_key=spec.get("rows_key"), unit=spec.get("unit"), headers={"Authorization": f"Bearer {token}"},
    )


FETCHERS = {
    "price": fetch_price, "fng": fetch_fng, "funding": fetch_funding,
    "stablecoin": fetch_stablecoin, "etf": fetch_etf, "mnav": fetch_mnav,
    **{f"onchain.{m}": partial(fetch_onchain, metric=m) for m in ONCHAIN_METRICS},
}
_FETCH_ERRORS = (SourceError, requests.RequestException, ccxt.BaseError, KeyError, ValueError)


def _read_cache(path):
    df = pd.read_csv(path, index_col=0, parse_dates=True)
    df.index = pd.DatetimeIndex(df.index).normalize().as_unit("ns")
    return df.squeeze("columns") if df.shape[1] == 1 else df


def load(name, cfg, offline=False):
    """Fresh data, else cache (any age offline, up to run.max_stale_days otherwise): (data, status)."""
    path = Path(cfg["paths"]["cache"]) / f"{name}.csv"
    reason = "offline"
    if not offline:
        try:
            data = FETCHERS[name](cfg)
            if data.empty:
                raise SourceError(f"{name}: source returned no rows")
            path.parent.mkdir(parents=True, exist_ok=True)
            data.rename_axis("date").to_csv(path)
            return data, "ok"
        except _FETCH_ERRORS as e:
            reason = str(e)
    if path.exists():
        age_days = (time.time() - path.stat().st_mtime) / 86400
        if offline or age_days <= cfg["run"]["max_stale_days"]:
            return _read_cache(path), f"cached ({age_days:.1f}d old): {reason}"
    raise SourceError(f"{name}: {reason}")


def load_all(cfg, offline=False):
    """Every source that could be loaded, plus a status line per source (missing ones are flagged)."""
    data, status = {}, {}
    for name in FETCHERS:
        try:
            data[name], status[name] = load(name, cfg, offline)
        except SourceError as e:
            status[name] = f"missing: {e}"
        log.info("source %-28s %s", name, status[name])
    return data, status


def verify(cfg):
    """Fetch every source fresh, bypassing the cache, and print what actually came back."""
    for name, fetch in FETCHERS.items():
        try:
            d = fetch(cfg)
            last = d["close"].iloc[-1] if isinstance(d, pd.DataFrame) else d.iloc[-1]
            print(f"OK   {name:<28} rows={len(d):>6}  {d.index[0].date()} -> {d.index[-1].date()}  last={last:.6g}")
        except _FETCH_ERRORS as e:
            print(f"FAIL {name:<28} {e}")
