"""
ars3_strategy_trend.py (v1.0 - Complete Expert Version)
Trend-Following strategy for ARS-3.
- Uses EMA crossover for trend direction.
- Uses ADX for trend strength confirmation.
- Uses ATR-based stop loss for exits.
"""
from typing import List, Dict, Optional
import numpy as np
import pandas as pd
import logging

logger = logging.getLogger(__name__)

# Default configuration for the Trend strategy
DEFAULT_CFG = {
    "fast_ema_period": 12,
    "slow_ema_period": 26,
    "adx_period": 14,
    "adx_threshold": 25,
    "atr_period": 14,
    "atr_sl_multiplier": 2.5,
    "risk_pct": 0.01,
}

def _get_indicators(df: pd.DataFrame, cfg: Dict) -> Dict:
    """Calculates all necessary indicators for the trend strategy."""
    try:
        indicators = {}
        # EMAs
        indicators['fast_ema'] = df['close'].ewm(span=cfg["fast_ema_period"], adjust=False).mean()
        indicators['slow_ema'] = df['close'].ewm(span=cfg["slow_ema_period"], adjust=False).mean()
        
        # ATR
        high_low = df['high'] - df['low']
        high_close = (df['high'] - df['close'].shift()).abs()
        low_close = (df['low'] - df['close'].shift()).abs()
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        indicators['atr'] = tr.rolling(cfg["atr_period"], min_periods=1).mean().fillna(0)

        # ADX
        up = df['high'].diff()
        down = df['low'].diff() * -1
        plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=df.index)
        minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=df.index)
        plus_dm_smooth = plus_dm.ewm(alpha=1/cfg["adx_period"], adjust=False).mean()
        minus_dm_smooth = minus_dm.ewm(alpha=1/cfg["adx_period"], adjust=False).mean()
        atr_safe = indicators['atr'].replace(0, 0.0001)
        plus_di = 100 * plus_dm_smooth / atr_safe
        minus_di = 100 * minus_dm_smooth / atr_safe
        di_sum = plus_di + minus_di
        di_diff = (plus_di - minus_di).abs()
        dx = pd.Series([ (d / s * 100) if s > 0 else 0 for d, s in zip(di_diff, di_sum)], index=df.index)
        indicators['adx'] = dx.ewm(alpha=1/cfg["adx_period"], adjust=False).mean().fillna(0)
        
        return indicators
    except Exception as e:
        logger.error(f"Trend indicator calculation error: {e}", exc_info=True)
        return {}

def generate_signals(df: pd.DataFrame, cfg: Dict = None, balance: Optional[float] = None) -> List[Dict]:
    """Generate trend-following signals."""
    try:
        cfg = dict(DEFAULT_CFG, **(cfg or {}))
        if len(df) < max(cfg['slow_ema_period'], cfg['adx_period']) + 5:
            return []

        indicators = _get_indicators(df, cfg)
        if not indicators: return []

        last_idx = len(df) - 1
        if last_idx < 1: return []

        last_close = df['close'].iat[last_idx]
        last_atr = indicators['atr'].iat[last_idx]
        last_adx = indicators['adx'].iat[last_idx]
        fast_ema = indicators['fast_ema']
        slow_ema = indicators['slow_ema']

        is_trending = last_adx > cfg['adx_threshold']
        
        bullish_cross = (fast_ema.iat[last_idx-1] <= slow_ema.iat[last_idx-1]) and \
                        (fast_ema.iat[last_idx] > slow_ema.iat[last_idx])
                        
        bearish_cross = (fast_ema.iat[last_idx-1] >= slow_ema.iat[last_idx-1]) and \
                        (fast_ema.iat[last_idx] < slow_ema.iat[last_idx])
        
        signals = []

        def create_signal(signal_type: str):
            entry = last_close
            sl_distance = cfg['atr_sl_multiplier'] * last_atr
            
            if signal_type == "BUY":
                stop = entry - sl_distance
                tp1 = entry + sl_distance * 1.5
                tp2 = entry + sl_distance * 3.0
            else: # SELL
                stop = entry + sl_distance
                tp1 = entry - sl_distance * 1.5
                tp2 = entry - sl_distance * 3.0

            risk_per_unit = abs(entry - stop)
            size = None
            if risk_per_unit > 0 and balance is not None:
                risk_amount = balance * cfg['risk_pct']
                size = risk_amount / risk_per_unit

            if size and size > 0:
                signals.append({
                    "type": signal_type, "price": float(entry), "size": size,
                    "stop": float(stop), "tp1": float(tp1), "tp2": float(tp2),
                    "reason": f"Trend EMA Cross | ADX: {last_adx:.1f}"
                })

        if is_trending:
            if bullish_cross:
                create_signal("BUY")
            elif bearish_cross:
                create_signal("SELL")
        
        return signals
        
    except Exception as e:
        logger.error(f"Critical error in trend signal generation: {e}", exc_info=True)
        return []