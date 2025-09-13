
import math
import numpy as np
import pandas as pd
import yaml
from typing import List, Dict, Optional, Any, Tuple
import logging
import os

logger = logging.getLogger(__name__)

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "ars3_config.yaml")

def load_strategy_config() -> Dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg_all = yaml.safe_load(f)
    return cfg_all.get("trend_strategy", {})

# -------------------------- ADVANCED INDICATORS --------------------------
def _ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()

def _sma(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window=window).mean()

def _atr(df: pd.DataFrame, length: int = 14) -> pd.Series:
    high, low, close = df['high'], df['low'], df['close']
    tr1 = high - low
    tr2 = (high - close.shift()).abs()
    tr3 = (low - close.shift()).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(window=length, min_periods=1).mean()

def _adx(df: pd.DataFrame, length: int = 14) -> pd.Series:
    high, low, close = df['high'], df['low'], df['close']
    tr = pd.concat([(high - low), (high - close.shift(1)).abs(), (low - close.shift(1)).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1/length, adjust=False).mean().replace(0, 1e-9)
    up, down = high.diff(), -low.diff()
    plus_dm_raw = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm_raw = np.where((down > up) & (down > 0), down, 0.0)
    plus_dm = pd.Series(plus_dm_raw, index=df.index)
    minus_dm = pd.Series(minus_dm_raw, index=df.index)
    plus_di = 100 * plus_dm.ewm(alpha=1/length, adjust=False).mean() / atr
    minus_di = 100 * minus_dm.ewm(alpha=1/length, adjust=False).mean() / atr
    dx = (abs(plus_di - minus_di) / (plus_di + minus_di + 1e-12)) * 100
    adx = dx.ewm(alpha=1/length, adjust=False).mean()
    return adx.fillna(0)

def _rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss.replace(0, 1e-9)
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(50)

def _macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> Tuple[pd.Series, pd.Series, pd.Series]:
    ema_fast = _ema(series, fast)
    ema_slow = _ema(series, slow)
    macd_line = ema_fast - ema_slow
    signal_line = _ema(macd_line, signal)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram

def _bollinger_bands(series: pd.Series, period: int = 20, std_dev: float = 2.0) -> Tuple[pd.Series, pd.Series, pd.Series]:
    sma = _sma(series, period)
    std = series.rolling(window=period).std()
    # FIXED: Added missing '+' operator
    upper = sma + (std * std_dev)
    lower = sma - (std * std_dev)
    return upper, sma, lower

def _stochastic(high: pd.Series, low: pd.Series, close: pd.Series, k_period: int = 14, d_period: int = 3) -> Tuple[pd.Series, pd.Series]:
    lowest_low = low.rolling(window=k_period).min()
    highest_high = high.rolling(window=k_period).max()
    k_percent = 100 * ((close - lowest_low) / (highest_high - lowest_low).replace(0, 1e-9))
    d_percent = k_percent.rolling(window=d_period).mean()
    return k_percent, d_percent

def _williams_r(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    highest_high = high.rolling(window=period).max()
    lowest_low = low.rolling(window=period).min()
    wr = -100 * ((highest_high - close) / (highest_high - lowest_low).replace(0, 1e-9))
    return wr

def _momentum(series: pd.Series, period: int = 10) -> pd.Series:
    return series / series.shift(period).replace(0, 1e-9) - 1

def _compute_volume_filter(df: pd.DataFrame, lookback: int = 20) -> pd.Series:
    if 'volume' not in df.columns: return pd.Series(1.0, index=df.index)
    vol = df['volume'].fillna(0)
    vol_avg = vol.rolling(window=lookback, min_periods=1).mean()
    return vol / (vol_avg + 1e-12)

def compute_advanced_indicators(df: pd.DataFrame, cfg: Dict) -> pd.DataFrame:
    df = df.copy()
    df['ema_fast'] = _ema(df['close'], cfg['fast_ema_period'])
    df['ema_slow'] = _ema(df['close'], cfg['slow_ema_period'])
    df['macro_ema'] = _ema(df['close'], cfg['macro_ema_period'])
    df['atr'] = _atr(df, cfg['atr_period'])
    df['adx'] = _adx(df, cfg['adx_period'])
    df['rsi'] = _rsi(df['close'], cfg['rsi_period'])
    df['macd'], df['macd_signal'], df['macd_hist'] = _macd(df['close'], cfg['macd_fast'], cfg['macd_slow'], cfg['macd_signal'])
    df['bb_upper'], df['bb_middle'], df['bb_lower'] = _bollinger_bands(df['close'], cfg['bb_period'], cfg['bb_std'])
    df['bb_width'] = (df['bb_upper'] - df['bb_lower']) / df['bb_middle'].replace(0, 1e-9)
    df['stoch_k'], df['stoch_d'] = _stochastic(df['high'], df['low'], df['close'], cfg['stoch_k_period'], cfg['stoch_d_period'])
    df['williams_r'] = _williams_r(df['high'], df['low'], df['close'], cfg['williams_r_period'])
    df['momentum'] = _momentum(df['close'], cfg['momentum_period'])
    df['vol_ratio'] = _compute_volume_filter(df, cfg['volume_lookback'])
    df['higher_low'] = (df['low'] > df['low'].shift(1))
    df['lower_high'] = (df['high'] < df['high'].shift(1))
    df['trend_strength'] = abs(df['ema_fast'] - df['ema_slow']) / df['close']
    df['htf_ema_fast'] = _ema(df['close'], cfg['htf_ema_fast_period'])
    df['htf_ema_slow'] = _ema(df['close'], cfg['htf_ema_slow_period'])
    return df

# -------------------------- ADVANCED HELPERS --------------------------
def _calculate_adaptive_risk(indicators: Dict, cfg: Dict) -> float:
    base_risk, max_risk = cfg['base_risk_pct'], cfg['max_risk_pct']
    adx_scale = min(1.0, indicators.get('adx', 0) / 50.0)
    vol_scale = min(1.0, indicators.get('bb_width', 0.02) / 0.05)
    trend_scale = min(1.0, indicators.get('trend_strength', 0.01) / 0.02)
    volume_scale = min(1.0, indicators.get('vol_ratio', 1.0) / 1.5)
    combined_scale = (adx_scale * 0.3 + vol_scale * 0.2 + trend_scale * 0.3 + volume_scale * 0.2)
    adaptive_risk = base_risk + (max_risk - base_risk) * combined_scale
    return min(max_risk, max(base_risk, adaptive_risk))

def _calculate_dynamic_stop_loss(df: pd.DataFrame, entry_idx: int, entry_price: float, side: str, cfg: Dict) -> float:
    current_atr = df.iloc[entry_idx]['atr']
    base_atr_mult = cfg['base_sl_atr_multiplier']
    bb_width = df.iloc[entry_idx]['bb_width']
    if bb_width > cfg['high_volatility_threshold']: atr_mult = base_atr_mult * cfg['high_vol_sl_multiplier']
    elif bb_width < cfg['low_volatility_threshold']: atr_mult = base_atr_mult * cfg['low_vol_sl_multiplier']
    else: atr_mult = base_atr_mult
    if df.iloc[entry_idx]['trend_strength'] > cfg['strong_trend_threshold']: atr_mult *= cfg['strong_trend_sl_multiplier']
    
    if side == 'BUY':
        lookback = cfg['sl_swing_lookback']
        start_idx = max(0, entry_idx - lookback)
        # FIXED: Changed entry_idx1 to entry_idx + 1
        swing_low = df.iloc[start_idx:entry_idx + 1]['low'].min()
        atr_stop = entry_price - (atr_mult * current_atr)
        return min(swing_low, atr_stop)
    else:
        lookback = cfg['sl_swing_lookback']
        start_idx = max(0, entry_idx - lookback)
        # FIXED: Changed entry_idx1 to entry_idx + 1
        swing_high = df.iloc[start_idx:entry_idx + 1]['high'].max()
        atr_stop = entry_price + (atr_mult * current_atr)
        return max(swing_high, atr_stop)

def _calculate_dynamic_take_profit(df: pd.DataFrame, entry_idx: int, entry_price: float, stop_loss: float, side: str, cfg: Dict) -> Tuple[float, float]:
    risk_distance = abs(entry_price - stop_loss)
    base_tp1_mult, base_tp2_mult = cfg['base_tp1_risk_multiple'], cfg['base_tp2_risk_multiple']
    adx_value = df.iloc[entry_idx]['adx']
    if adx_value > cfg['strong_adx_threshold']:
        tp1_mult, tp2_mult = base_tp1_mult * cfg['strong_adx_tp_multiplier'], base_tp2_mult * cfg['strong_adx_tp_multiplier']
    else:
        tp1_mult, tp2_mult = base_tp1_mult, base_tp2_mult
    bb_width = df.iloc[entry_idx]['bb_width']
    if bb_width > cfg['high_volatility_threshold']:
        tp1_mult *= cfg['high_vol_tp_multiplier']
        tp2_mult *= cfg['high_vol_tp_multiplier']
    if side == 'BUY':
        return entry_price + (risk_distance * tp1_mult), entry_price + (risk_distance * tp2_mult)
    else:
        return entry_price - (risk_distance * tp1_mult), entry_price - (risk_distance * tp2_mult)

def _calculate_position_size(entry_price: float, stop_loss: float, balance: float, risk_pct: float, cfg: Dict) -> float:
    risk_distance = abs(entry_price - stop_loss)
    if risk_distance <= 0: return 0.0
    risk_amount = balance * risk_pct
    base_size = risk_amount / risk_distance
    max_size = (balance * cfg['max_position_notional_pct']) / entry_price
    final_size = min(base_size, max_size)
    return final_size if final_size >= cfg.get('min_position_size', 0.0) else 0.0

def _calculate_signal_quality(df: pd.DataFrame, idx: int, side: str, cfg: Dict) -> float:
    row = df.iloc[idx]
    score = 0.0
    if side == 'BUY':
        if row['ema_fast'] > row['ema_slow'] and row['close'] > row['macro_ema']: score += 0.3
        if row['htf_ema_fast'] > row['htf_ema_slow']: score += 0.1
        if row['rsi'] > 50 and row['rsi'] < 70: score += 0.15
        if row['macd'] > row['macd_signal']: score += 0.1
        if row['higher_low']: score += 0.1
    else:
        if row['ema_fast'] < row['ema_slow'] and row['close'] < row['macro_ema']: score += 0.3
        if row['htf_ema_fast'] < row['htf_ema_slow']: score += 0.1
        if row['rsi'] < 50 and row['rsi'] > 30: score += 0.15
        if row['macd'] < row['macd_signal']: score += 0.1
        if row['lower_high']: score += 0.1
    if cfg['min_bb_width'] <= row['bb_width'] <= cfg['max_bb_width']: score += 0.2
    if row['vol_ratio'] >= cfg['min_volume_ratio']: score += 0.15
    return min(1.0, score)

# -------------------------- OPTIMIZED STRATEGY --------------------------
def generate_signals(
    df: pd.DataFrame,
    cfg: Optional[Dict] = None,
    account_state: Optional[Dict[str, Any]] = None
) -> List[Dict]:
    cfg = load_strategy_config()
    if len(df) < cfg['macro_ema_period']: return []
    ind = compute_advanced_indicators(df, cfg)
    signals: List[Dict] = []
    
    # REFINED: Only analyze the most recently closed candle for live trading
    i = len(ind) - 1
    if i < 1: return []

    row, prev = ind.iloc[i], ind.iloc[i - 1]
    
    equity = account_state.get('equity', 10000.0) if account_state else 10000.0
    if account_state and account_state.get('daily_loss', 0) <= -abs(cfg['max_daily_loss_pct']) * equity:
        return []

    close, open_p, low, high = row['close'], row['open'], row['low'], row['high']
    if (high - low) / max(close, 1e-12) > cfg['extreme_volatility_pct']: return []

    trending = row['adx'] >= cfg['adx_threshold']
    htf_long = not cfg['use_htf_filter'] or (row['htf_ema_fast'] > row['htf_ema_slow'])
    htf_short = not cfg['use_htf_filter'] or (row['htf_ema_fast'] < row['htf_ema_slow'])
    vol_ok = cfg['min_bb_width'] <= row['bb_width'] <= cfg['max_bb_width'] and row['vol_ratio'] >= cfg['min_volume_ratio']
    is_macro_bull = row['close'] > row['macro_ema']
    is_macro_bear = row['close'] < row['macro_ema']

    risk_pct = _calculate_adaptive_risk(row.to_dict(), cfg)

    # BUY SIGNAL
    if is_macro_bull and trending and htf_long and vol_ok:
        momentum_bullish = (row['rsi'] > 50 and row['rsi'] < 75 and row['macd'] > row['macd_signal'] and row['momentum'] > 0)
        pullback_entry = (low <= row['ema_fast'] and close > open_p and close > row['ema_fast'])
        if momentum_bullish and pullback_entry and _calculate_signal_quality(ind, i, 'BUY', cfg) >= cfg['min_signal_quality']:
            sl = _calculate_dynamic_stop_loss(ind, i, close, 'BUY', cfg)
            tp1, tp2 = _calculate_dynamic_take_profit(ind, i, close, sl, 'BUY', cfg)
            size = _calculate_position_size(close, sl, equity, risk_pct, cfg)
            if size > 0:
                signals.append({'type': 'BUY', 'price': float(close), 'size': size, 'stop': sl, 'tp1': tp1, 'tp2': tp2})

    # SELL SIGNAL
    if is_macro_bear and trending and htf_short and vol_ok:
        momentum_bearish = (row['rsi'] < 50 and row['rsi'] > 25 and row['macd'] < row['macd_signal'] and row['momentum'] < 0)
        pullback_entry = (high >= row['ema_fast'] and close < open_p and close < row['ema_fast'])
        if momentum_bearish and pullback_entry and _calculate_signal_quality(ind, i, 'SELL', cfg) >= cfg['min_signal_quality']:
            sl = _calculate_dynamic_stop_loss(ind, i, close, 'SELL', cfg)
            tp1, tp2 = _calculate_dynamic_take_profit(ind, i, close, sl, 'SELL', cfg)
            size = _calculate_position_size(close, sl, equity, risk_pct, cfg)
            if size > 0:
                signals.append({'type': 'SELL', 'price': float(close), 'size': size, 'stop': sl, 'tp1': tp1, 'tp2': tp2})

    return signals