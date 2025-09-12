"""
ars3_strategy_trend_v8_optimized.py

Optimized Trend Following Strategy with Maximum Expectancy
- Advanced entry filters with momentum confirmation
- Multi-timeframe analysis
- Dynamic position sizing based on market conditions
- Advanced risk management with adaptive stops
- Market structure analysis
- Expectancy optimization features
"""

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
    return cfg_all.get("trend_strategy_optimized", {})

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
    
    # Convert numpy arrays back to pandas Series WITH THE CORRECT INDEX
    plus_dm = pd.Series(plus_dm_raw, index=df.index)
    minus_dm = pd.Series(minus_dm_raw, index=df.index)
    
    plus_di = 100 * plus_dm.ewm(alpha=1/length, adjust=False).mean() / atr
    minus_di = 100 * minus_dm.ewm(alpha=1/length, adjust=False).mean() / atr
    
    dx = (abs(plus_di - minus_di) / (plus_di + minus_di + 1e-12)) * 100
    adx = dx.ewm(alpha=1/length, adjust=False).mean()
    
    return adx.fillna(0)

def _rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Calculate RSI indicator."""
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(50)

def _macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """Calculate MACD indicator."""
    ema_fast = _ema(series, fast)
    ema_slow = _ema(series, slow)
    macd_line = ema_fast - ema_slow
    signal_line = _ema(macd_line, signal)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram

def _bollinger_bands(series: pd.Series, period: int = 20, std_dev: float = 2.0) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """Calculate Bollinger Bands."""
    sma = _sma(series, period)
    std = series.rolling(window=period).std()
    upper = sma + (std * std_dev)
    lower = sma - (std * std_dev)
    return upper, sma, lower

def _stochastic(high: pd.Series, low: pd.Series, close: pd.Series, k_period: int = 14, d_period: int = 3) -> Tuple[pd.Series, pd.Series]:
    """Calculate Stochastic Oscillator."""
    lowest_low = low.rolling(window=k_period).min()
    highest_high = high.rolling(window=k_period).max()
    k_percent = 100 * ((close - lowest_low) / (highest_high - lowest_low))
    d_percent = k_percent.rolling(window=d_period).mean()
    return k_percent, d_percent

def _williams_r(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Calculate Williams %R."""
    highest_high = high.rolling(window=period).max()
    lowest_low = low.rolling(window=period).min()
    wr = -100 * ((highest_high - close) / (highest_high - lowest_low))
    return wr

def _momentum(series: pd.Series, period: int = 10) -> pd.Series:
    """Calculate Momentum indicator."""
    return series / series.shift(period) - 1

def _compute_volume_filter(df: pd.DataFrame, lookback: int = 20) -> pd.Series:
    if 'volume' not in df.columns:
        return pd.Series(np.ones(len(df)), index=df.index)
    vol = df['volume'].fillna(0)
    vol_avg = vol.rolling(window=lookback, min_periods=1).mean()
    return vol / (vol_avg + 1e-12)

def _find_support_resistance(df: pd.DataFrame, lookback: int = 50, min_touches: int = 2) -> Tuple[List[float], List[float]]:
    """Find key support and resistance levels."""
    highs = df['high'].rolling(window=lookback, center=True).max()
    lows = df['low'].rolling(window=lookback, center=True).min()
    
    # Find pivot highs and lows
    pivot_highs = df[df['high'] == highs]['high'].dropna()
    pivot_lows = df[df['low'] == lows]['low'].dropna()
    
    # Group similar levels
    resistance_levels = []
    support_levels = []
    
    # Simple clustering of levels (can be improved with more sophisticated methods)
    if len(pivot_highs) > 0:
        resistance_levels = pivot_highs.tolist()
    if len(pivot_lows) > 0:
        support_levels = pivot_lows.tolist()
    
    return support_levels, resistance_levels

def compute_advanced_indicators(df: pd.DataFrame, cfg: Dict) -> pd.DataFrame:
    """Compute all advanced indicators for the optimized strategy."""
    df = df.copy()
    
    # Basic trend indicators
    df['ema_fast'] = _ema(df['close'], cfg['fast_ema_period'])
    df['ema_slow'] = _ema(df['close'], cfg['slow_ema_period'])
    df['macro_ema'] = _ema(df['close'], cfg['macro_ema_period'])
    df['atr'] = _atr(df, cfg['atr_period'])
    df['adx'] = _adx(df, cfg['adx_period'])
    
    # Momentum indicators
    df['rsi'] = _rsi(df['close'], cfg['rsi_period'])
    df['macd'], df['macd_signal'], df['macd_hist'] = _macd(df['close'], 
                                                          cfg['macd_fast'], 
                                                          cfg['macd_slow'], 
                                                          cfg['macd_signal'])
    
    # Volatility indicators
    df['bb_upper'], df['bb_middle'], df['bb_lower'] = _bollinger_bands(df['close'], 
                                                                      cfg['bb_period'], 
                                                                      cfg['bb_std'])
    df['bb_width'] = (df['bb_upper'] - df['bb_lower']) / df['bb_middle']
    
    # Oscillator indicators
    df['stoch_k'], df['stoch_d'] = _stochastic(df['high'], df['low'], df['close'], 
                                               cfg['stoch_k_period'], cfg['stoch_d_period'])
    df['williams_r'] = _williams_r(df['high'], df['low'], df['close'], cfg['williams_r_period'])
    
    # Additional momentum
    df['momentum'] = _momentum(df['close'], cfg['momentum_period'])
    
    # Volume analysis
    df['vol_ratio'] = _compute_volume_filter(df, cfg['volume_lookback'])
    df['volume_sma'] = df['volume'].rolling(window=cfg['volume_sma_period']).mean()
    
    # Market structure
    df['higher_high'] = (df['high'] > df['high'].shift(1)) & (df['high'].shift(1) > df['high'].shift(2))
    df['higher_low'] = (df['low'] > df['low'].shift(1)) & (df['low'].shift(1) > df['low'].shift(2))
    df['lower_high'] = (df['high'] < df['high'].shift(1)) & (df['high'].shift(1) < df['high'].shift(2))
    df['lower_low'] = (df['low'] < df['low'].shift(1)) & (df['low'].shift(1) < df['low'].shift(2))
    
    # Trend strength
    df['trend_strength'] = abs(df['ema_fast'] - df['ema_slow']) / df['close']
    
    # Multi-timeframe simulation (using longer periods)
    df['htf_ema_fast'] = _ema(df['close'], cfg['htf_ema_fast_period'])
    df['htf_ema_slow'] = _ema(df['close'], cfg['htf_ema_slow_period'])
    
    return df

# -------------------------- ADVANCED HELPERS --------------------------
def _within_session(ts: pd.Timestamp, cfg: Dict) -> bool:
    if cfg.get('trade_start_hour_utc') is None or cfg.get('trade_end_hour_utc') is None:
        return True
    h = ts.hour
    start, end = int(cfg['trade_start_hour_utc']), int(cfg['trade_end_hour_utc'])
    if start <= end:
        return start <= h < end
    return h >= start or h < end

def _calculate_adaptive_risk(indicators: Dict, cfg: Dict) -> float:
    """Calculate adaptive risk based on multiple market conditions."""
    base_risk = cfg['base_risk_pct']
    max_risk = cfg['max_risk_pct']
    
    # ADX-based risk scaling
    adx_value = indicators.get('adx', 0)
    adx_scale = min(1.0, adx_value / 50.0)  # Scale up to 50 ADX
    
    # Volatility-based risk scaling
    bb_width = indicators.get('bb_width', 0.02)
    vol_scale = min(1.0, bb_width / 0.05)  # Scale up to 5% BB width
    
    # Trend strength scaling
    trend_strength = indicators.get('trend_strength', 0.01)
    trend_scale = min(1.0, trend_strength / 0.02)  # Scale up to 2% trend strength
    
    # Volume confirmation scaling
    vol_ratio = indicators.get('vol_ratio', 1.0)
    volume_scale = min(1.0, vol_ratio / 1.5)  # Scale up to 1.5x volume
    
    # Combine all factors
    combined_scale = (adx_scale * 0.3 + vol_scale * 0.2 + trend_scale * 0.3 + volume_scale * 0.2)
    
    # Calculate final risk
    risk_range = max_risk - base_risk
    adaptive_risk = base_risk + (risk_range * combined_scale)
    
    return min(max_risk, max(base_risk, adaptive_risk))

def _calculate_dynamic_stop_loss(df: pd.DataFrame, entry_idx: int, entry_price: float, 
                                side: str, cfg: Dict) -> float:
    """Calculate dynamic stop loss based on market structure and volatility."""
    current_atr = df.iloc[entry_idx]['atr']
    current_price = df.iloc[entry_idx]['close']
    
    # Base ATR multiplier
    base_atr_mult = cfg['base_sl_atr_multiplier']
    
    # Adjust based on volatility
    bb_width = df.iloc[entry_idx]['bb_width']
    if bb_width > cfg['high_volatility_threshold']:
        atr_mult = base_atr_mult * cfg['high_vol_sl_multiplier']
    elif bb_width < cfg['low_volatility_threshold']:
        atr_mult = base_atr_mult * cfg['low_vol_sl_multiplier']
    else:
        atr_mult = base_atr_mult
    
    # Adjust based on trend strength
    trend_strength = df.iloc[entry_idx]['trend_strength']
    if trend_strength > cfg['strong_trend_threshold']:
        atr_mult *= cfg['strong_trend_sl_multiplier']
    
    # Calculate stop loss
    if side == 'BUY':
        # Look for recent swing low
        lookback = cfg['sl_swing_lookback']
        start_idx = max(0, entry_idx - lookback)
        swing_low = df.iloc[start_idx:entry_idx+1]['low'].min()
        atr_stop = entry_price - (atr_mult * current_atr)
        sl = min(swing_low, atr_stop)
    else:
        # Look for recent swing high
        lookback = cfg['sl_swing_lookback']
        start_idx = max(0, entry_idx - lookback)
        swing_high = df.iloc[start_idx:entry_idx+1]['high'].max()
        atr_stop = entry_price + (atr_mult * current_atr)
        sl = max(swing_high, atr_stop)
    
    return sl

def _calculate_dynamic_take_profit(df: pd.DataFrame, entry_idx: int, entry_price: float, 
                                  stop_loss: float, side: str, cfg: Dict) -> Tuple[float, float]:
    """Calculate dynamic take profit levels based on market conditions."""
    risk_distance = abs(entry_price - stop_loss)
    
    # Base risk multiples
    base_tp1_mult = cfg['base_tp1_risk_multiple']
    base_tp2_mult = cfg['base_tp2_risk_multiple']
    
    # Adjust based on ADX (trend strength)
    adx_value = df.iloc[entry_idx]['adx']
    if adx_value > cfg['strong_adx_threshold']:
        tp1_mult = base_tp1_mult * cfg['strong_adx_tp_multiplier']
        tp2_mult = base_tp2_mult * cfg['strong_adx_tp_multiplier']
    else:
        tp1_mult = base_tp1_mult
        tp2_mult = base_tp2_mult
    
    # Adjust based on volatility
    bb_width = df.iloc[entry_idx]['bb_width']
    if bb_width > cfg['high_volatility_threshold']:
        tp1_mult *= cfg['high_vol_tp_multiplier']
        tp2_mult *= cfg['high_vol_tp_multiplier']
    
    # Calculate take profit levels
    if side == 'BUY':
        tp1 = entry_price + (risk_distance * tp1_mult)
        tp2 = entry_price + (risk_distance * tp2_mult)
    else:
        tp1 = entry_price - (risk_distance * tp1_mult)
        tp2 = entry_price - (risk_distance * tp2_mult)
    
    return tp1, tp2

def _calculate_position_size(entry_price: float, stop_loss: float, balance: float, 
                           risk_pct: float, cfg: Dict) -> float:
    """Calculate optimal position size with advanced risk management."""
    risk_distance = abs(entry_price - stop_loss)
    if risk_distance <= 0:
        return 0.0
    
    # Base position sizing
    risk_amount = balance * risk_pct
    base_size = risk_amount / risk_distance
    
    # Apply maximum position size limit
    max_position_value = balance * cfg['max_position_notional_pct']
    max_size = max_position_value / entry_price
    
    # Apply minimum position size
    min_size = cfg['min_position_size']
    
    final_size = min(base_size, max_size)
    final_size = max(final_size, min_size)
    
    return final_size

# -------------------------- SIGNAL QUALITY SCORING --------------------------
def _calculate_signal_quality(df: pd.DataFrame, idx: int, side: str, cfg: Dict) -> float:
    """Calculate signal quality score (0-1) based on multiple factors."""
    row = df.iloc[idx]
    score = 0.0
    
    # Trend alignment (30% weight)
    if side == 'BUY':
        if row['ema_fast'] > row['ema_slow'] and row['close'] > row['macro_ema']:
            score += 0.3
        if row['htf_ema_fast'] > row['htf_ema_slow']:
            score += 0.1
    else:
        if row['ema_fast'] < row['ema_slow'] and row['close'] < row['macro_ema']:
            score += 0.3
        if row['htf_ema_fast'] < row['htf_ema_slow']:
            score += 0.1
    
    # Momentum confirmation (25% weight)
    if side == 'BUY':
        if row['rsi'] > 50 and row['rsi'] < 70:  # Not overbought
            score += 0.15
        if row['macd'] > row['macd_signal']:
            score += 0.1
    else:
        if row['rsi'] < 50 and row['rsi'] > 30:  # Not oversold
            score += 0.15
        if row['macd'] < row['macd_signal']:
            score += 0.1
    
    # Volatility confirmation (20% weight)
    if cfg['min_bb_width'] <= row['bb_width'] <= cfg['max_bb_width']:
        score += 0.2
    
    # Volume confirmation (15% weight)
    if row['vol_ratio'] >= cfg['min_volume_ratio']:
        score += 0.15
    
    # Market structure (10% weight)
    if side == 'BUY' and row['higher_low']:
        score += 0.1
    elif side == 'SELL' and row['lower_high']:
        score += 0.1
    
    return min(1.0, score)

# -------------------------- OPTIMIZED STRATEGY --------------------------
def generate_signals(
    df: pd.DataFrame,
    cfg: Optional[Dict] = None,
    account_state: Optional[Dict[str, Any]] = None
) -> List[Dict]:
    """Generate optimized trend following signals with maximum expectancy."""
    base_cfg = load_strategy_config()
    cfg = dict(base_cfg, **(cfg or {}))  # merge user override

    df = df.copy()
    if not isinstance(df.index, pd.DatetimeIndex):
        if 'timestamp' in df.columns:
            df = df.set_index(pd.to_datetime(df['timestamp']))
        else:
            raise ValueError("DataFrame must have DatetimeIndex or 'timestamp'")

    # Compute advanced indicators
    ind = compute_advanced_indicators(df, cfg)
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

        # Update crossover tracking
        if prev['ema_fast'] <= prev['ema_slow'] and row['ema_fast'] > row['ema_slow']:
            last_bull = i
        if prev['ema_fast'] >= prev['ema_slow'] and row['ema_fast'] < row['ema_slow']:
            last_bear = i

        # Basic trend and strength filters
        trending = row['adx'] >= cfg['adx_threshold']
        strong_trend = row['adx'] >= cfg['strong_adx_threshold']
        
        # ADX window confirmation
        adx_window = ind['adx'].iloc[max(0, i-20):i+1]
        if adx_window.median() < cfg['adx_threshold']:
            continue

        # Multi-timeframe alignment
        htf_long = not cfg['use_htf_filter'] or (row['htf_ema_fast'] > row['htf_ema_slow'])
        htf_short = not cfg['use_htf_filter'] or (row['htf_ema_fast'] < row['htf_ema_slow'])
        
        # Volatility filters
        vol_ok = cfg['min_bb_width'] <= row['bb_width'] <= cfg['max_bb_width']
        vol_ok = vol_ok and (row['vol_ratio'] >= cfg['min_volume_ratio'])
        
        # Market structure filters
        is_macro_bull = row['close'] > row['macro_ema']
        is_macro_bear = row['close'] < row['macro_ema']
        
        # Calculate adaptive risk
        indicators = {
            'adx': row['adx'],
            'bb_width': row['bb_width'],
            'trend_strength': row['trend_strength'],
            'vol_ratio': row['vol_ratio']
        }
        risk_pct = _calculate_adaptive_risk(indicators, cfg)
        
        if balance is not None and equity is not None:
            risk_pct = min(cfg['max_risk_pct'], risk_pct)
        else:
            risk_pct = cfg['base_risk_pct']

        # BUY SIGNAL GENERATION
        if (is_macro_bull and trending and htf_long and vol_ok and 
            last_bull > 0 and (i - last_bull) <= cfg['pullback_max_bars_since_cross']):
            
            # Enhanced entry conditions
            momentum_bullish = (row['rsi'] > 50 and row['rsi'] < 70 and 
                              row['macd'] > row['macd_signal'] and
                              row['momentum'] > 0)
            
            pullback_entry = (low <= row['ema_fast'] and close > open_p and 
                            close > row['ema_fast'])
            
            if momentum_bullish and pullback_entry:
                # Calculate signal quality
                signal_quality = _calculate_signal_quality(ind, i, 'BUY', cfg)
                
                # Only trade high-quality signals
                if signal_quality >= cfg['min_signal_quality']:
                    # Calculate dynamic stop loss and take profits
                    sl = _calculate_dynamic_stop_loss(ind, i, close, 'BUY', cfg)
                    tp1, tp2 = _calculate_dynamic_take_profit(ind, i, close, sl, 'BUY', cfg)
                    
                    # Calculate position size
                    size = _calculate_position_size(close, sl, equity or 0.0, risk_pct, cfg)
                    
                    if size > 0:
                        # Split position for partial exits
                        half = size * 0.5
                        
                        # First position (TP1)
                        signals.append({
                            'type': 'BUY', 'price': float(close), 'size': half, 
                            'stop': sl, 'tp1': tp1,
                            'exit_mode': 'partial_tp1', 'atr': float(row['atr']),
                            'meta': {
                                'reason': 'optimized_pullback',
                                'signal_quality': signal_quality,
                                'adx': float(row['adx']),
                                'rsi': float(row['rsi']),
                                'bb_width': float(row['bb_width'])
                            }
                        })
                        
                        # Second position (TP2 with trailing)
                        signals.append({
                            'type': 'BUY', 'price': float(close), 'size': size - half, 
                            'stop': sl, 'tp2': tp2,
                            'exit_mode': 'trail_atr', 'atr': float(row['atr']),
                            'meta': {
                                'reason': 'optimized_pullback',
                                'signal_quality': signal_quality,
                                'dynamic_trail': True,
                                'trail_wide': cfg['dynamic_trail_multiplier_wide'],
                                'trail_tight': cfg['dynamic_trail_multiplier_tight'],
                                'rr_trigger': cfg['dynamic_trail_rr_threshold']
                            }
                        })
                        
                        cooldown = cfg['cooldown_bars']

        # SELL SIGNAL GENERATION
        if (is_macro_bear and trending and htf_short and vol_ok and 
            last_bear > 0 and (i - last_bear) <= cfg['pullback_max_bars_since_cross']):
            
            # Enhanced entry conditions
            momentum_bearish = (row['rsi'] < 50 and row['rsi'] > 30 and 
                              row['macd'] < row['macd_signal'] and
                              row['momentum'] < 0)
            
            pullback_entry = (high >= row['ema_fast'] and close < open_p and 
                            close < row['ema_fast'])
            
            if momentum_bearish and pullback_entry:
                # Calculate signal quality
                signal_quality = _calculate_signal_quality(ind, i, 'SELL', cfg)
                
                # Only trade high-quality signals
                if signal_quality >= cfg['min_signal_quality']:
                    # Calculate dynamic stop loss and take profits
                    sl = _calculate_dynamic_stop_loss(ind, i, close, 'SELL', cfg)
                    tp1, tp2 = _calculate_dynamic_take_profit(ind, i, close, sl, 'SELL', cfg)
                    
                    # Calculate position size
                    size = _calculate_position_size(close, sl, equity or 0.0, risk_pct, cfg)
                    
                    if size > 0:
                        # Split position for partial exits
                        half = size * 0.5
                        
                        # First position (TP1)
                        signals.append({
                            'type': 'SELL', 'price': float(close), 'size': half, 
                            'stop': sl, 'tp1': tp1,
                            'exit_mode': 'partial_tp1', 'atr': float(row['atr']),
                            'meta': {
                                'reason': 'optimized_pullback',
                                'signal_quality': signal_quality,
                                'adx': float(row['adx']),
                                'rsi': float(row['rsi']),
                                'bb_width': float(row['bb_width'])
                            }
                        })
                        
                        # Second position (TP2 with trailing)
                        signals.append({
                            'type': 'SELL', 'price': float(close), 'size': size - half, 
                            'stop': sl, 'tp2': tp2,
                            'exit_mode': 'trail_atr', 'atr': float(row['atr']),
                            'meta': {
                                'reason': 'optimized_pullback',
                                'signal_quality': signal_quality,
                                'dynamic_trail': True,
                                'trail_wide': cfg['dynamic_trail_multiplier_wide'],
                                'trail_tight': cfg['dynamic_trail_multiplier_tight'],
                                'rr_trigger': cfg['dynamic_trail_rr_threshold']
                            }
                        })
                        
                        cooldown = cfg['cooldown_bars']

    return signals