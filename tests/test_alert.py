import json

import numpy as np
import pandas as pd
import pytest

from ars3_alert import backtest, indicators, runner, sources, store
from ars3_alert.scoring import State, score
from common.env import load_yaml
from conftest import random_walk_ohlcv


@pytest.fixture
def cfg(tmp_path):
    cfg = load_yaml("ars3_alert/config.yaml")
    cfg["paths"] = {k: str(tmp_path / v) for k, v in cfg["paths"].items()}
    return cfg


def synthetic_data(n=1500, seed=1):
    """Price, F&G, funding, stablecoin supply and ETF flows (weekdays only); no on-chain, no mNAV."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2021-01-01", periods=n, freq="D")
    price = random_walk_ohlcv(n, seed, "2021-01-01", "D").set_index("timestamp")[["open", "high", "low", "close", "volume"]]
    etf_days = idx[idx.dayofweek < 5][-900:]
    return {
        "price": price,
        "fng": pd.Series(rng.integers(0, 100, n).astype(float), idx),
        "funding": pd.Series(rng.normal(1e-4, 1e-4, n), idx),
        "stablecoin": pd.Series(1e11 * np.exp(np.cumsum(rng.normal(0, 0.003, n))), idx),
        "etf": pd.Series(rng.normal(0, 1e8, len(etf_days)), etf_days),
    }


# ---- indicators ----
def test_indicators_are_causal(cfg):
    """Cutting the future off must not change any past value or flag: that is what makes live == backtest."""
    data = synthetic_data()
    v, f = indicators.compute(data, cfg)
    cut = v.index[1200]
    v_cut, f_cut = indicators.compute({k: s.loc[:cut] for k, s in data.items()}, cfg)
    pd.testing.assert_frame_equal(v.loc[:cut], v_cut.loc[:cut])
    pd.testing.assert_frame_equal(f.loc[:cut], f_cut.loc[:cut])


def test_missing_sources_give_na_flags_never_false(cfg):
    _, f = indicators.compute(synthetic_data(), cfg)
    for name in ("regime.above_sth_cost", "regime.above_true_mean", "stress.mnav", "stress.lth_loss"):
        assert f[name].isna().all(), name
    assert f["stress.fng"].dropna().any() and f["stress.fng"].notna().sum() > 1000


def test_halving_clock_matches_the_research_note():
    days = indicators.days_since_halving(pd.DatetimeIndex(["2024-04-20", "2025-10-06"]), ["2020-05-11", "2024-04-20"])
    assert days.tolist() == [0, 534]


def test_stride_ma_averages_weekly_closes():
    close = pd.Series(np.arange(1.0, 29.0))
    ma = indicators.stride_ma(close, 2)
    assert ma.iloc[13] == (14 + 7) / 2 and ma.iloc[:7].isna().all()


def test_expanding_percentile_ranks_against_the_past_only():
    pct = indicators.expanding_pct(pd.Series([1.0, 2.0, 3.0, 2.0]), min_history=1)
    assert pct.tolist() == [1.0, 1.0, 1.0, 0.75]


# ---- scoring ----
STRESS = ["stress.fng", "stress.mnav", "stress.etf_outflow", "stress.stablecoin", "stress.lth_loss",
          "stress.slow_ma_touch", "stress.funding"]


def make_row(**flags):
    names = STRESS + ["regime.above_fast_ma", "regime.above_slow_ma", "regime.above_sth_cost",
                      "regime.above_true_mean", "recovery.etf_positive", "recovery.funding_neutral", "phase.top_zone"]
    return pd.Series({n: flags.get(n.replace(".", "_"), pd.NA) for n in names}, dtype="object")


def stress_row(fired, known=7):
    flags = {n.replace(".", "_"): i < fired for i, n in enumerate(STRESS[:known])}
    return make_row(**flags)


def test_stress_needs_min_flags_and_has_hysteresis(cfg):
    on = score(stress_row(4), cfg)
    assert on.state.stress_on and on.events == ("STRESS_ON",)
    assert not score(stress_row(3), cfg).state.stress_on
    assert score(stress_row(3), cfg, on.state).state.stress_on  # stays on until fewer than clear_flags
    off = score(stress_row(2), cfg, on.state)
    assert not off.state.stress_on and off.events == ("STRESS_OFF",)


def test_missing_flags_are_skipped_not_counted_as_false(cfg):
    few = score(stress_row(4, known=4), cfg)  # 4 of 4 known fired, but below min_available
    assert few.stress_available == 4 and len(few.stress_missing) == 3
    assert not few.state.stress_on  # too little data: no verdict, previous state held
    assert score(stress_row(4, known=4), cfg, State(stress_on=True)).state.stress_on


@pytest.mark.parametrize("flags, expected", [
    ({"regime_above_fast_ma": True, "regime_above_slow_ma": True, "regime_above_sth_cost": True, "regime_above_true_mean": True}, "above_trend"),
    ({"regime_above_fast_ma": False, "regime_above_slow_ma": False, "regime_above_sth_cost": False, "regime_above_true_mean": True}, "below_trend"),
    ({"regime_above_fast_ma": True, "regime_above_slow_ma": False}, "mixed"),
    ({"regime_above_fast_ma": True}, "unknown"),
])
def test_regime_share_of_available_tests(cfg, flags, expected):
    assert score(make_row(**flags), cfg).state.regime == expected


def test_regime_event_is_not_raised_when_coming_from_unknown(cfg):
    assert score(make_row(regime_above_fast_ma=True, regime_above_slow_ma=True), cfg).events == ()


def test_recovery_needs_min_confirmations(cfg):
    row = make_row(regime_above_fast_ma=True, regime_above_sth_cost=True, recovery_etf_positive=True)
    assert score(row, cfg).state.recovery_on
    assert not score(make_row(regime_above_fast_ma=True, recovery_etf_positive=True), cfg).state.recovery_on


# ---- store, alerts ----
def test_store_roundtrip_and_corrupt_state(tmp_path):
    path = tmp_path / "s.json"
    assert store.load_state(path) == State()
    store.save_state(path, State(stress_on=True, regime="mixed"))
    assert store.load_state(path) == State(stress_on=True, regime="mixed")
    path.write_text("{not json")
    assert store.load_state(path) == State()


def test_run_log_keeps_the_union_of_columns(tmp_path):
    path = tmp_path / "runs.csv"
    store.append_run(path, {"a": 1})
    store.append_run(path, {"a": 2, "b": 3})
    assert list(pd.read_csv(path).columns) == ["a", "b"] and len(pd.read_csv(path)) == 2


# ---- runner end to end ----
def serve(monkeypatch, data):
    """Make every source come from `data`; sources that are not in it fail like an unreachable API."""
    def fetcher(name):
        def fetch(cfg):
            if name not in data:
                raise sources.SourceError("unavailable in test")
            return data[name]
        return fetch
    monkeypatch.setattr(sources, "FETCHERS", {n: fetcher(n) for n in sources.FETCHERS})


def test_run_once_logs_persists_and_alerts_once(cfg, monkeypatch):
    serve(monkeypatch, synthetic_data())
    stressed = lambda f: f.assign(**{c: True for c in STRESS[:5]})
    real_compute = indicators.compute
    monkeypatch.setattr(indicators, "compute", lambda d, c: (lambda v, f: (v, stressed(f)))(*real_compute(d, c)))
    sent = []
    send = lambda text, shadow: sent.append((text, shadow)) or True

    first = runner.run_once(cfg, send=send)
    assert first.events == ("STRESS_ON",) and len(sent) == 1
    assert sent[0][1] is True and "Cụm căng thẳng BẬT" in sent[0][0] and "Nguồn không lấy được" in sent[0][0]
    assert runner.run_once(cfg, send=send).events == () and len(sent) == 1  # no repeat while the state holds

    assert json.load(open(cfg["paths"]["regime"]))["stress_on"] is True
    log = pd.read_csv(cfg["paths"]["runs"])
    assert len(log) == 2 and "v.close" in log and "f.stress.fng" in log and log["events"].iloc[0] == "STRESS_ON"


def test_failed_delivery_keeps_state_so_the_alert_is_retried(cfg, monkeypatch):
    serve(monkeypatch, synthetic_data())
    real_compute = indicators.compute
    monkeypatch.setattr(indicators, "compute", lambda d, c: (lambda v, f: (v, f.assign(**{k: True for k in STRESS[:5]})))(*real_compute(d, c)))
    assert runner.run_once(cfg, send=lambda t, s: False).events == ("STRESS_ON",)
    assert runner.run_once(cfg, send=lambda t, s: True).events == ("STRESS_ON",)  # announced on the retry


def test_no_price_data_is_reported_not_crashed(cfg, monkeypatch):
    serve(monkeypatch, {})
    sent = []
    assert runner.run_once(cfg, send=lambda t, s: sent.append(t)) is None
    assert "Không có dữ liệu giá" in sent[0]


# ---- backtest ----
def test_replay_matches_live_scoring_day_by_day(cfg):
    _, flags = indicators.compute(synthetic_data(), cfg)
    days = backtest.replay(flags, cfg)
    for day in flags.index[::97]:  # spot-check: replaying from the start up to `day` ends in the same state
        sub = backtest.replay(flags.loc[:day], cfg).iloc[-1]
        assert sub["stress_on"] == days.loc[day, "stress_on"] and sub["regime"] == days.loc[day, "regime"]


def test_evaluate_and_false_alarms(cfg):
    idx = pd.date_range("2026-01-01", "2026-09-30", freq="D")
    days = pd.DataFrame({"stress_on": False, "recovery_on": False, "top_zone": False, "regime": "mixed",
                         "stress_available": 6, "stress_hits": 0, "events": ""}, index=idx)
    days.loc["2026-03-10", "events"] = "STRESS_ON"          # outside the panic and bottom windows
    days.loc["2026-06-20":"2026-06-30", "stress_on"] = True  # inside the bottom window
    res = backtest.evaluate(days, cfg).set_index("milestone")
    assert res.loc["bottom", "expected_state_on"] and not res.loc["panic", "expected_state_on"]
    assert res.loc["bottom", "first_on"].isoformat() == "2026-06-20"
    assert [d.date().isoformat() for d in backtest.false_alarms(days, cfg)] == ["2026-03-10"]
