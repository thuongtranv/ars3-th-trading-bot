"""One test per ARS-3 bug that was fixed, so a regression fails loudly."""
import importlib
import shutil
import sys
from pathlib import Path

import ccxt
import numpy as np
import pandas as pd
import pytest
import yaml

import ars3_backtest
from ars3_backtest import backtest, calculate_metrics
from ars3_broker_ccxt import Broker
from ars3_strategy_rs75 import strategy_cfg
from conftest import random_walk_ohlcv

ROOT = Path(__file__).resolve().parents[1]
BUY = {"type": "BUY", "stop": 90.0, "tp1": 110.0, "size": 1.0}


def flat_bars(n=8):
    df = random_walk_ohlcv(n)
    df[["open", "high", "low", "close"]] = 100.0
    return df


def fake_signals(monkeypatch, at_history_len, signal=BUY):
    """Replace the strategy: emit `signal` once, when the history has `at_history_len` bars."""
    seen = []

    def fake(history, cfg, balance=None):
        seen.append(history)
        return [dict(signal)] if len(history) == at_history_len else []

    monkeypatch.setattr(ars3_backtest, "generate_signals", fake)
    return seen


# ---- 1. environment variable names ----
def broker_with_keys(monkeypatch, tmp_path, **env):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    path = tmp_path / "cfg.yaml"
    path.write_text(yaml.dump({"exchange": "binance", "simulation_mode": False, "testnet": False, "futures": False}))
    captured = {}

    class FakeExchange:
        options, urls = {}, {}

        def __init__(self, params):
            captured.update(params)

        def load_markets(self): pass
        def fetch_ticker(self, symbol): pass

    monkeypatch.setattr(ccxt, "binance", FakeExchange)
    return Broker(str(path)), captured


@pytest.mark.parametrize("secret_var", ["BINANCE_SECRET_KEY", "BINANCE_API_SECRET"])
def test_broker_reads_secret_under_both_names(monkeypatch, tmp_path, secret_var):
    broker, captured = broker_with_keys(monkeypatch, tmp_path, BINANCE_API_KEY="k", **{secret_var: "s"})
    assert captured["secret"] == "s" and not broker.simulation_mode


def test_broker_without_keys_falls_back_to_simulation(monkeypatch, tmp_path):
    broker, _ = broker_with_keys(monkeypatch, tmp_path)
    assert broker.simulation_mode


# ---- 2. backtest: equity length and mark-to-market ----
def test_backtest_equity_covers_every_bar_with_real_strategy():
    cfg = yaml.safe_load((ROOT / "ars3_config.yaml").read_text())
    df, _ = backtest(random_walk_ohlcv(600), cfg)
    assert len(df["equity"]) == 600 and df["equity"].notna().all()


def test_equity_marks_open_position_to_market(monkeypatch):
    fake_signals(monkeypatch, at_history_len=3)
    df = flat_bars()
    df.loc[3, ["high", "close"]] = [103.0, 102.0]
    out, _ = backtest(df, {}, initial_balance=10_000)
    assert out["equity"].iloc[3] == pytest.approx(10_002)  # +2 unrealised, not yet in balance
    assert out["equity"].iloc[2] == 10_000


# ---- 3. backtest: no look-ahead ----
def test_signal_uses_only_closed_bars_and_fills_at_next_open(monkeypatch):
    seen = fake_signals(monkeypatch, at_history_len=3)
    df = flat_bars()
    df.loc[3, "open"] = 101.0
    df.loc[3, "high"] = 101.0
    _, trades = backtest(df, {})
    assert [len(h) for h in seen[:4]] == [0, 1, 2, 3]  # bar i sees bars 0..i-1 only
    entry = next(t for t in trades if t["type"] == "ENTRY")
    assert entry["timestamp"] == df["timestamp"][3] and entry["entry_price"] == 101.0


def test_stop_loss_wins_when_stop_and_target_share_a_bar(monkeypatch):
    fake_signals(monkeypatch, at_history_len=3)
    df = flat_bars()
    df.loc[4, ["high", "low"]] = [111.0, 89.0]
    _, trades = backtest(df, {})
    assert [t["type"] for t in trades if "pnl" in t] == ["STOP_LOSS"]
    assert trades[-1]["pnl"] == pytest.approx(-10)


# ---- 4. backtest metrics ----
def test_metrics_count_round_trips_and_use_gross_profit_factor():
    trades = [
        {"type": "ENTRY", "trade_id": 1},
        {"type": "TAKE_PROFIT_1", "trade_id": 1, "pnl": 50.0},
        {"type": "STOP_LOSS", "trade_id": 1, "pnl": -20.0},  # same round trip: net +30
        {"type": "ENTRY", "trade_id": 2},
        {"type": "STOP_LOSS", "trade_id": 2, "pnl": -10.0},
        {"type": "ENTRY", "trade_id": 3},
        {"type": "STOP_LOSS", "trade_id": 3, "pnl": -20.0},
    ]
    m = calculate_metrics(pd.DataFrame({"equity": [10_000, 10_030, 10_020, 10_000]}), trades, 10_000)
    assert (m["total_trades"], m["winning_trades"], m["losing_trades"]) == (3, 1, 2)
    assert m["win_rate_pct"] == pytest.approx(100 / 3)
    assert m["profit_factor"] == pytest.approx(30 / 30)  # gross profit / gross loss, not avg win / avg loss


def test_sharpe_is_annualised_with_the_given_bar_frequency():
    equity = pd.Series([100.0, 101.0, 100.5, 102.0, 101.0])
    trades = [{"type": "STOP_LOSS", "trade_id": 1, "pnl": 1.0}]
    r = equity.pct_change().dropna()
    for bars_per_year in (4, 8760):
        m = calculate_metrics(pd.DataFrame({"equity": equity}), trades, 100.0, bars_per_year)
        assert m["sharpe_ratio"] == pytest.approx(r.mean() / r.std() * np.sqrt(bars_per_year))


# ---- 5. strategy config ----
def test_strategy_cfg_risk_follows_risk_per_trade():
    assert strategy_cfg({"risk_per_trade": 0.02, "rs75_strategy": {"bb_period": 30}}) == {
        "bb_period": 30, "risk_pct": 0.02}


# ---- 6. live loop: emergency stop and multi-coin ----
@pytest.fixture
def live(tmp_path, monkeypatch):
    shutil.copy(ROOT / "ars3_config.yaml", tmp_path)
    monkeypatch.chdir(tmp_path)
    sys.modules.pop("ars3_live_advanced_final", None)
    module = importlib.import_module("ars3_live_advanced_final")
    module.sent = []
    monkeypatch.setattr(module, "send_telegram", module.sent.append)
    yield module
    sys.modules.pop("ars3_live_advanced_final", None)


def test_emergency_stop_measures_drawdown_from_peak_and_alerts_once(live):
    live.current_balance = live.peak_balance = 12_000
    assert not live.check_emergency_stop()
    live.current_balance = 10_000  # flat versus initial capital, but -16.7% from the peak
    assert live.check_emergency_stop() and live.check_emergency_stop()
    assert len(live.sent) == 1
    live.current_balance = 11_500
    assert not live.check_emergency_stop()


def test_find_entry_scans_every_configured_coin(live, monkeypatch):
    live.COIN_SELECTION = "scan"
    frames = {s: random_walk_ohlcv(50) for s in live.COINS}
    monkeypatch.setattr(live, "load_df", frames.get)
    signal = [{"type": "BUY"}]
    monkeypatch.setattr(live, "generate_signals", lambda df, cfg, balance: signal if df is frames[live.COINS[2]] else [])
    symbol, df, signals = live.find_entry(1000)
    assert symbol == live.COINS[2] and signals == signal


# ---- 7. config options and persisted risk state ----
def reimport():
    sys.modules.pop("ars3_live_advanced_final", None)
    return importlib.import_module("ars3_live_advanced_final")


def test_defaults_are_peak_reference_and_first_coin_only(live, monkeypatch):
    assert (live.STOP_REFERENCE, live.COIN_SELECTION) == ("peak", "first")
    loaded = []
    monkeypatch.setattr(live, "load_df", lambda symbol: loaded.append(symbol))
    assert live.find_entry(1000) == (None, None, []) and loaded == live.COINS[:1]


def test_initial_reference_ignores_the_peak(live):
    live.STOP_REFERENCE = "initial"
    live.peak_balance, live.current_balance = 12_000, 10_000  # -16.7% from the peak, flat versus initial capital
    assert not live.check_emergency_stop()
    live.current_balance = 8_400
    assert live.check_emergency_stop()


def test_peak_and_paper_balance_survive_a_restart(live):
    live.update_balance(2_000)
    live.check_emergency_stop()
    live.update_balance(-1_000)
    restarted = reimport()
    assert (restarted.peak_balance, restarted.current_balance) == (12_000, 11_000)


def test_risk_state_from_the_other_mode_is_ignored(live):
    Path("risk_state.json").write_text('{"simulation": false, "balance": 5000, "peak": 99999}')
    restarted = reimport()
    assert (restarted.peak_balance, restarted.current_balance) == (restarted.INITIAL_CAPITAL,) * 2


@pytest.mark.parametrize("key", ["emergency_stop_reference", "coin_selection"])
def test_unknown_option_value_is_rejected(live, key):
    with pytest.raises(ValueError, match=key):
        live.validate_config({**live.config, key: "bogus"})
