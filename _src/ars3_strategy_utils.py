"""
ars3_strategy_utils.py
Contains advanced helper functions and classes for strategy execution,
including the professional-grade MarketRegimeDetector.
"""
import pandas as pd
import numpy as np
import logging

logger = logging.getLogger(__name__)

def _calculate_slope(series, period=50):
    """Calculate the normalized slope of a series."""
    if len(series) < period: return 0.0
    y = series.iloc[-period:].values
    x = np.arange(period)
    try:
        A = np.vstack([x, np.ones(len(x))]).T
        m, _ = np.linalg.lstsq(A, y, rcond=None)[0]
        avg = np.mean(y)
        return m / avg if avg != 0 else 0.0
    except (np.linalg.LinAlgError, ValueError):
        return 0.0

def calculate_regime_indicators(df: pd.DataFrame, cfg: dict) -> dict:
    """
    Calculates all indicators needed for regime detection from a dataframe.
    This should be called once per trading loop cycle.
    Returns a dictionary with the latest values of the indicators.
    """
    try:
        if df is None or len(df) < cfg.get("min_data_points", 200):
            return None

        # --- Indicator Calculations ---
        close = df['close']
        
        # ATR and nATR
        high_low = df['high'] - df['low']
        high_close = (df['high'] - df['close'].shift()).abs()
        low_close = (df['low'] - df['close'].shift()).abs()
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        atr = tr.rolling(cfg.get("atr_period", 14)).mean()
        natr = (atr / close).iloc[-1]

        # ADX
        adx_period = cfg.get("adx_period", 14)
        up = df['high'].diff(); down = df['low'].diff() * -1
        plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=df.index)
        minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=df.index)
        atr_safe = atr.replace(0, 0.0001)
        plus_di = 100 * plus_dm.ewm(alpha=1/adx_period, adjust=False).mean() / atr_safe
        minus_di = 100 * minus_dm.ewm(alpha=1/adx_period, adjust=False).mean() / atr_safe
        di_sum = plus_di + minus_di
        di_diff = (plus_di - minus_di).abs()
        dx = pd.Series([ (d / s * 100) if s > 0 else 0 for d, s in zip(di_diff, di_sum)], index=df.index)
        adx = dx.ewm(alpha=1/adx_period, adjust=False).mean()

        # EMA Slope
        ema_long = close.ewm(span=cfg.get("slope_ema_period", 200), adjust=False).mean()
        slope = _calculate_slope(ema_long, period=cfg.get("slope_period", 50))

        # Bollinger Band Width
        bb_period = cfg.get("bb_period", 20)
        mid = close.rolling(bb_period).mean()
        std = close.rolling(bb_period).std()
        upper = mid + 2 * std
        lower = mid - 2 * std
        bb_width = ((upper - lower) / mid).iloc[-1]

        # Volume Z-Score
        vol_z = 0.0
        if 'volume' in df.columns:
            vol_period = cfg.get("vol_period", 50)
            if len(df) > vol_period:
                vol = df['volume']
                vma = vol.rolling(vol_period).mean()
                vsd = vol.rolling(vol_period).std().replace(0, np.nan)
                vol_z = ((vol - vma) / vsd).iloc[-1]
            
        return {
            "adx": adx.iloc[-1],
            "natr": natr,
            "slope": slope,
            "bb_width": bb_width if pd.notna(bb_width) else 0.0,
            "vol_z": vol_z if pd.notna(vol_z) else 0.0
        }
    except Exception as e:
        logger.error(f"Error in calculate_regime_indicators: {e}", exc_info=True)
        return None

class MarketRegimeDetector:
    def __init__(self, cfg):
        self.cfg = cfg
        self.current_regime = "undefined"
        self.cooldown_counter = 0
        self.last_scores = {}

    def _scale(self, value, low, high):
        """Scale a value to a 0-1 score."""
        if high == low: return 0.5
        return np.clip((value - low) / (high - low), 0, 1)

    def _calculate_scores(self, indicators):
        """Calculate trend and range scores based on indicators."""
        adx = indicators['adx']
        natr = indicators['natr']
        slope_abs = abs(indicators['slope'])
        vol_z = indicators['vol_z']

        adx_lo, adx_hi = self.cfg.get("adx_low", 18), self.cfg.get("adx_high", 25)
        natr_lo, natr_hi = self.cfg.get("natr_low", 0.003), self.cfg.get("natr_high", 0.006)
        slope_lo, slope_hi = self.cfg.get("slope_abs_low", 0.00005), self.cfg.get("slope_abs_high", 0.00015)

        s_adx = self._scale(adx, adx_lo, adx_hi)
        s_natr = self._scale(natr, natr_lo, natr_hi)
        s_slope = self._scale(slope_abs, slope_lo, slope_hi)
        s_vol = self._scale(vol_z, -1.0, 2.0)
        
        # === FIX START: Use weights from config file ===
        trend_w = self.cfg.get("trend_weights", {"adx": 0.5, "natr": 0.2, "slope": 0.2, "volume": 0.1})
        range_w = self.cfg.get("range_weights", {"adx": 0.5, "natr": 0.3, "slope": 0.2})

        trend_score = (s_adx * trend_w.get('adx', 0.5) + 
                       s_natr * trend_w.get('natr', 0.2) + 
                       s_slope * trend_w.get('slope', 0.2) + 
                       s_vol * trend_w.get('volume', 0.1))
                       
        range_score = ((1 - s_adx) * range_w.get('adx', 0.5) + 
                       (1 - s_natr) * range_w.get('natr', 0.3) + 
                       (1 - s_slope) * range_w.get('slope', 0.2))
        # === FIX END ===
        
        self.last_scores = {
            "trend_score": round(trend_score, 3), "range_score": round(range_score, 3),
            **{k: round(v, 5) for k, v in indicators.items()}
        }
        return trend_score, range_score

    def determine(self, indicators):
        """Determine the current market regime with hysteresis and cooldown."""
        if self.cooldown_counter > 0:
            self.cooldown_counter -= 1
            return self.current_regime

        bbw_compress = self.cfg.get("bb_width_low", 0.015)
        natr_compress = self.cfg.get("natr_low", 0.003)
        if indicators['bb_width'] < bbw_compress and indicators['natr'] < natr_compress:
            new_regime = "compression"
        else:
            trend_score, range_score = self._calculate_scores(indicators)
            
            trend_on, trend_off = self.cfg.get("trend_on", 0.35), self.cfg.get("trend_off", 0.45)
            range_on, range_off = self.cfg.get("range_on", 0.35), self.cfg.get("range_off", 0.45)

            if self.current_regime == "trending":
                if trend_score < trend_off and range_score > range_on:
                    new_regime = "ranging"
                else: new_regime = "trending"
            elif self.current_regime == "ranging":
                if range_score < range_off and trend_score > trend_on:
                    new_regime = "trending"
                else: new_regime = "ranging"
            else:
                if trend_score > trend_on: new_regime = "trending"
                elif range_score > range_on: new_regime = "ranging"
                else: new_regime = "undefined"

        if self.current_regime != new_regime:
            self.cooldown_counter = self.cfg.get("cooldown_bars", 3)
            logger.info(f"Market regime changed from '{self.current_regime}' -> '{new_regime}'. Scores: {self.last_scores}")
        
        self.current_regime = new_regime
        return self.current_regime