import numpy as np
import pandas as pd
import pytest


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """No test may see real or placeholder credentials from .env."""
    for name in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "BINANCE_API_KEY", "BINANCE_SECRET_KEY",
                 "BINANCE_API_SECRET", "BGEOMETRICS_API_KEY"):
        monkeypatch.delenv(name, raising=False)


def random_walk_ohlcv(n, seed=0, start="2024-01-01", freq="h"):
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.004, n)))
    open_ = np.r_[close[0], close[:-1]]
    return pd.DataFrame({
        "timestamp": pd.date_range(start, periods=n, freq=freq),
        "open": open_, "high": np.maximum(open_, close) * 1.002, "low": np.minimum(open_, close) * 0.998,
        "close": close, "volume": rng.uniform(1, 2, n),
    })
