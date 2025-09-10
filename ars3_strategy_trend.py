"""
ars3_strategy_trend_v7.py

Trending strategy (config moved to YAML)
"""

import math
import numpy as np
import pandas as pd
import yaml
from typing import List, Dict, Optional, Any
import logging
import os

logger = logging.getLogger(__name__)

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "ars3_config.yaml")

def load_strategy_config() -> Dict:
    with open(CONFIG_PATH, "r") as f:
        cfg_all = yaml.safe_load(f)
    return cfg_all.get("trend_strategy", {})

# -------------------------- INDICATORS --------------------------
def _ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()

def _atr(df: pd.DataFrame, length: int = 14) -> pd.Series:
    high, low, close = df['high'], df['low'], df['close']
    tr1 = high - low
    tr2 = (high - close.shift()).abs()
    tr3 = (low - close.shift()).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(window=length, min_periods=1).mean()

def _adx(df: pd.DataFrame, length: int = 14) -> pd.Series:
    """Calculates the ADX indicator with a fix for index alignment."""
    high, low, close = df['high'], df['low'], df['close']
    
    # Calculate True Range
    tr = pd.concat([
        (high - low),
        (high - close.shift(1)).abs(),
        (low - close.shift(1)).abs()
    ], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1/length, adjust=False).mean().replace(0, 1e-9)
    
    # Directional Movement
    up = high.diff()
    down = -low.diff()
    plus_dm_raw = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm_raw = np.where((down > up) & (down > 0), down, 0.0)
    
    # --- FIX START ---
    # Convert numpy arrays back to pandas Series WITH THE CORRECT INDEX
    plus_dm = pd.Series(plus_dm_raw, index=df.index)
    minus_dm = pd.Series(minus_dm_raw, index=df.index)
    # --- FIX END ---
    
    plus_di = 100 * plus_dm.ewm(alpha=1/length, adjust=False).mean() / atr
    minus_di = 100 * minus_dm.ewm(alpha=1/length, adjust=False).mean() / atr
    
    dx = (abs(plus_di - minus_di) / (plus_di + minus_di + 1e-12)) * 100
    adx = dx.ewm(alpha=1/length, adjust=False).mean()
    
    return adx.fillna(0)

def _compute_volume_filter(df: pd.DataFrame, lookback: int = 20) -> pd.Series:
    if 'volume' not in df.columns:
        return pd.Series(np.ones(len(df)), index=df.index)
    vol = df['volume'].fillna(0)
    vol_avg = vol.rolling(window=lookback, min_periods=1).mean()
    return vol / (vol_avg + 1e-12)

def compute_indicators(df: pd.DataFrame, cfg: Dict) -> pd.DataFrame:
    df = df.copy()
    df['ema_fast'] = _ema(df['close'], cfg['fast_ema_period'])
    df['ema_slow'] = _ema(df['close'], cfg['slow_ema_period'])
    df['macro_ema'] = _ema(df['close'], cfg['macro_ema_period'])
    df['atr'] = _atr(df, cfg['atr_period'])
    df['adx'] = _adx(df, cfg['adx_period'])
    df['vol_ratio'] = _compute_volume_filter(df)
    df['htf_fast'] = df['ema_fast']
    df['htf_slow'] = df['ema_slow']
    return df

# -------------------------- HELPERS --------------------------
def _within_session(ts: pd.Timestamp, cfg: Dict) -> bool:
    if cfg.get('trade_start_hour_utc') is None or cfg.get('trade_end_hour_utc') is None:
        return True
    h = ts.hour
    start, end = int(cfg['trade_start_hour_utc']), int(cfg['trade_end_hour_utc'])
    if start <= end:
        return start <= h < end
    return h >= start or h < end

def _adaptive_risk(adx_value: float, cfg: Dict) -> float:
    base, max_r, scale = cfg['base_risk_pct'], cfg['max_risk_pct'], cfg['adx_risk_scale_factor']
    extra = min(max_r - base, (adx_value / 100.0) * scale * 100.0)
    return min(max_r, base + extra)

# -------------------------- STRATEGY --------------------------
def generate_signals(
    df: pd.DataFrame,
    cfg: Optional[Dict] = None,
    account_state: Optional[Dict[str, Any]] = None
) -> List[Dict]:
    base_cfg = load_strategy_config()
    cfg = dict(base_cfg, **(cfg or {}))  # merge user override

    df = df.copy()
    if not isinstance(df.index, pd.DatetimeIndex):
        if 'timestamp' in df.columns:
            df = df.set_index(pd.to_datetime(df['timestamp']))
        else:
            raise ValueError("DataFrame must have DatetimeIndex or 'timestamp'")

    ind = compute_indicators(df, cfg)
    signals: List[Dict] = []
    cooldown = 0
    balance = account_state.get('balance') if account_state else None
    equity = account_state.get('equity', balance) if account_state else balance
    daily_loss = account_state.get('daily_loss', 0.0) if account_state else 0.0
    max_daily_loss = cfg['max_daily_loss_pct']
    if equity and daily_loss <= -abs(max_daily_loss) * equity:
        logger.warning("Daily loss threshold exceeded; no new trades today.")
        return []

    last_bull, last_bear = -1, -1
    for i in range(1, len(ind)):
        if cooldown > 0:
            cooldown -= 1
            continue

        row, prev, ts = ind.iloc[i], ind.iloc[i - 1], ind.index[i]
        if not _within_session(ts, cfg):
            continue

        close, open_p, low, high = row['close'], row['open'], row['low'], row['high']
        # Extreme volatility filter
        if (high - low) / max(close, 1e-12) > cfg['extreme_volatility_pct']:
            continue

        if prev['ema_fast'] <= prev['ema_slow'] and row['ema_fast'] > row['ema_slow']:
            last_bull = i
        if prev['ema_fast'] >= prev['ema_slow'] and row['ema_fast'] < row['ema_slow']:
            last_bear = i

        trending = row['adx'] >= cfg['adx_threshold']
        adx_window = ind['adx'].iloc[max(0, i-20):i+1]
        if adx_window.median() < cfg['adx_threshold']:
            continue

        htf_long = not cfg['use_htf_filter'] or (row['htf_fast'] > row['htf_slow'])
        htf_short = not cfg['use_htf_filter'] or (row['htf_fast'] < row['htf_slow'])
        vol_ok = not cfg['use_volatility_filter'] or (
            cfg['volatility_min_pct'] <= (row['atr'] / (row['close'] + 1e-12)) <= cfg['volatility_max_pct']
        )
        vol_ok = vol_ok and (
            not cfg['use_volume_filter'] or (row.get('vol_ratio', 1.0) <= cfg['volume_spike_multiplier'])
        )

        is_macro_bull, is_macro_bear = row['close'] > row['macro_ema'], row['close'] < row['macro_ema']
        risk_pct = _adaptive_risk(row['adx'], cfg)
        if balance is not None and equity is not None:
            risk_pct = min(cfg['max_risk_pct'], risk_pct)
        else:
            risk_pct = cfg['base_risk_pct']

        # BUY
        if is_macro_bull and trending and htf_long and vol_ok and last_bull > 0 and (i - last_bull) <= cfg['pullback_max_bars_since_cross']:
            if low <= row['ema_fast'] and close > open_p and close > row['ema_fast']:
                swing_low = ind['low'][max(0, i - cfg['sl_swing_period']): i + 1].min()
                sl = float(swing_low - cfg['sl_atr_buffer_multiplier'] * row['atr'])
                risk_dist = max(1e-8, abs(close - sl))
                risk_amt = (equity or 0.0) * risk_pct
                raw_size = risk_amt / risk_dist if risk_dist > 0 else 0
                max_notional = (equity or 0.0) * cfg['max_position_notional_pct']
                size = min(raw_size, max_notional / close if close > 0 else raw_size)
                if size > 0:
                    tp1 = float(close + risk_dist * cfg['tp1_risk_multiple'])
                    tp2 = float(close + risk_dist * cfg['tp2_risk_multiple'])
                    half = size * 0.5
                    signals.append({
                        'type': 'BUY', 'price': float(close), 'size': half, 'stop': sl, 'tp1': tp1,
                        'exit_mode': 'partial_tp1', 'atr': float(row['atr']),
                        'meta': {'reason': 'pullback'}
                    })
                    signals.append({
                        'type': 'BUY', 'price': float(close), 'size': size - half, 'stop': sl, 'tp2': tp2,
                        'exit_mode': 'trail_atr', 'atr': float(row['atr']),
                        'meta': {
                            'reason': 'pullback',
                            'dynamic_trail': True,
                            'trail_wide': cfg['dynamic_trail_multiplier_wide'],
                            'trail_tight': cfg['dynamic_trail_multiplier_tight'],
                            'rr_trigger': cfg['dynamic_trail_rr_threshold']
                        }
                    })
                    cooldown = cfg['cooldown_bars']

        # SELL
        if is_macro_bear and trending and htf_short and vol_ok and last_bear > 0 and (i - last_bear) <= cfg['pullback_max_bars_since_cross']:
            if high >= row['ema_fast'] and close < open_p and close < row['ema_fast']:
                swing_high = ind['high'][max(0, i - cfg['sl_swing_period']): i + 1].max()
                sl = float(swing_high + cfg['sl_atr_buffer_multiplier'] * row['atr'])
                risk_dist = max(1e-8, abs(close - sl))
                risk_amt = (equity or 0.0) * risk_pct
                raw_size = risk_amt / risk_dist if risk_dist > 0 else 0
                max_notional = (equity or 0.0) * cfg['max_position_notional_pct']
                size = min(raw_size, max_notional / close if close > 0 else raw_size)
                if size > 0:
                    tp1 = float(close - risk_dist * cfg['tp1_risk_multiple'])
                    tp2 = float(close - risk_dist * cfg['tp2_risk_multiple'])
                    half = size * 0.5
                    signals.append({
                        'type': 'SELL', 'price': float(close), 'size': half, 'stop': sl, 'tp1': tp1,
                        'exit_mode': 'partial_tp1', 'atr': float(row['atr']),
                        'meta': {'reason': 'pullback'}
                    })
                    signals.append({
                        'type': 'SELL', 'price': float(close), 'size': size - half, 'stop': sl, 'tp2': tp2,
                        'exit_mode': 'trail_atr', 'atr': float(row['atr']),
                        'meta': {
                            'reason': 'pullback',
                            'dynamic_trail': True,
                            'trail_wide': cfg['dynamic_trail_multiplier_wide'],
                            'trail_tight': cfg['dynamic_trail_multiplier_tight'],
                            'rr_trigger': cfg['dynamic_trail_rr_threshold']
                        }
                    })
                    cooldown = cfg['cooldown_bars']

    return signals
