"""
ars3_strategy_rs75.py (v2.1 - FIXED VERSION)
Range-Scalper (RS-75) strategy with bug fixes and improved stability
"""
from typing import List, Dict, Optional
import numpy as np
import pandas as pd
import logging

logger = logging.getLogger(__name__)

# Default configuration for RS-75 with validation
DEFAULT_CFG = {
    # Basic indicators
    "bb_period": 20,
    "bb_std": 2.5,
    "rsi_period": 7,
    "adx_period": 14,
    "sl_atr_multiplier": 1.7,
    "tp_atr_multiplier": 1.2,
    
    # Regime filters
    "adx_max": 20,
    "natr_max": 0.0040,
    
    # Expert features
    "use_confirmation_entry": True,
    "use_smart_sl": True,
    "swing_period": 10,
    "use_smart_tp": True,
    "use_volume_filter": True,
    "volume_ma_period": 20,
    "use_htf_filter": False,
    "htf_ma_period": 50,
    
    # Risk management
    "risk_pct": 0.01,
    "min_size": 0.001,
    "max_size_pct": 0.1,  # Max 10% of balance per trade
}

def validate_config(cfg: Dict) -> Dict:
    """Validate and sanitize configuration parameters."""
    validated = DEFAULT_CFG.copy()
    validated.update(cfg)
    
    # Ensure positive values
    for key in ["bb_period", "rsi_period", "adx_period", "swing_period", "volume_ma_period", "htf_ma_period"]:
        if key in validated and validated[key] <= 0:
            logger.warning(f"Invalid {key}: {validated[key]}, using default")
            validated[key] = DEFAULT_CFG[key]
    
    # Ensure valid percentages
    for key in ["risk_pct", "natr_max", "max_size_pct"]:
        if key in validated and (validated[key] <= 0 or validated[key] > 1):
            logger.warning(f"Invalid {key}: {validated[key]}, using default")
            validated[key] = DEFAULT_CFG[key]
    
    return validated

def safe_divide(numerator, denominator, default=0.0):
    """Safe division with default value for zero denominator."""
    try:
        return numerator / denominator if denominator != 0 else default
    except (TypeError, ZeroDivisionError):
        return default

def _get_indicators(df: pd.DataFrame, cfg: Dict) -> Dict:
    """Calculate all indicators with proper error handling."""
    try:
        indicators = {}
        
        # Ensure minimum data length
        min_length = max(cfg["bb_period"], cfg["adx_period"], cfg["rsi_period"], 50)
        if len(df) < min_length:
            logger.warning(f"Insufficient data length: {len(df)} < {min_length}")
            return {}
        
        # FIXED: ATR calculation with proper error handling
        try:
            high_low = df['high'] - df['low']
            high_close = (df['high'] - df['close'].shift()).abs()
            low_close = (df['low'] - df['close'].shift()).abs()
            tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
            indicators['atr'] = tr.rolling(cfg["adx_period"], min_periods=1).mean().fillna(0)
        except Exception as e:
            logger.error(f"ATR calculation error: {e}")
            indicators['atr'] = pd.Series([0.001] * len(df), index=df.index)
        
        # FIXED: ADX calculation with NaN handling
        try:
            up = df['high'].diff()
            down = df['low'].diff() * -1
            
            plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=df.index)
            minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=df.index)
            
            # Smooth directional movements
            period = cfg["adx_period"]
            plus_dm_smooth = plus_dm.ewm(alpha=1/period, adjust=False).mean()
            minus_dm_smooth = minus_dm.ewm(alpha=1/period, adjust=False).mean()
            
            # Calculate directional indicators
            atr_safe = indicators['atr'].replace(0, 0.0001)
            plus_di = 100 * plus_dm_smooth / atr_safe
            minus_di = 100 * minus_dm_smooth / atr_safe
            
            # Calculate DX and ADX
            di_sum = plus_di + minus_di
            di_diff = (plus_di - minus_di).abs()
            dx = pd.Series([safe_divide(diff, sum_val, 0) * 100 for diff, sum_val in zip(di_diff, di_sum)], index=df.index)
            
            indicators['adx'] = dx.ewm(alpha=1/period, adjust=False).mean().fillna(0)
            
        except Exception as e:
            logger.error(f"ADX calculation error: {e}")
            indicators['adx'] = pd.Series([10] * len(df), index=df.index)  # Default non-trending value

        # FIXED: RSI calculation with proper error handling
        try:
            delta = df['close'].diff()
            gain = delta.clip(lower=0)
            loss = (-delta).clip(lower=0)
            
            avg_gain = gain.rolling(cfg["rsi_period"], min_periods=1).mean()
            avg_loss = loss.rolling(cfg["rsi_period"], min_periods=1).mean()
            
            rs = pd.Series([safe_divide(g, l, 1) for g, l in zip(avg_gain, avg_loss)], index=df.index)
            indicators['rsi'] = (100 - (100 / (1 + rs))).fillna(50)
            
        except Exception as e:
            logger.error(f"RSI calculation error: {e}")
            indicators['rsi'] = pd.Series([50] * len(df), index=df.index)

        # FIXED: Bollinger Bands with error handling
        try:
            indicators['bb_mid'] = df['close'].rolling(cfg["bb_period"], min_periods=1).mean()
            bb_std = df['close'].rolling(cfg["bb_period"], min_periods=1).std().fillna(0)
            indicators['bb_upper'] = indicators['bb_mid'] + cfg["bb_std"] * bb_std
            indicators['bb_lower'] = indicators['bb_mid'] - cfg["bb_std"] * bb_std
            
        except Exception as e:
            logger.error(f"Bollinger Bands calculation error: {e}")
            # Fallback: use price as middle band
            indicators['bb_mid'] = df['close']
            indicators['bb_upper'] = df['close'] * 1.02
            indicators['bb_lower'] = df['close'] * 0.98
        
        # Volume MA (optional)
        if 'volume' in df.columns and cfg.get("use_volume_filter", False):
            try:
                indicators['volume_ma'] = df['volume'].rolling(cfg["volume_ma_period"], min_periods=1).mean()
            except Exception as e:
                logger.error(f"Volume MA calculation error: {e}")
                indicators['volume_ma'] = df['volume'] if 'volume' in df.columns else pd.Series([1] * len(df))

        return indicators
        
    except Exception as e:
        logger.error(f"Critical error in indicator calculation: {e}")
        return {}

def get_swing_levels(df: pd.DataFrame, period: int = 10) -> tuple:
    """Get recent swing high and low levels."""
    try:
        if len(df) < period + 1:
            return df['high'].max(), df['low'].min()
        
        # Look back for swing levels
        recent_high = df['high'].iloc[-period:].max()
        recent_low = df['low'].iloc[-period:].min()
        
        return recent_high, recent_low
        
    except Exception as e:
        logger.error(f"Error calculating swing levels: {e}")
        # Fallback to current high/low
        return df['high'].iloc[-1], df['low'].iloc[-1]

def calculate_position_size(balance: float, entry_price: float, stop_price: float, cfg: Dict) -> float:
    """Calculate position size with validation."""
    try:
        if balance <= 0 or entry_price <= 0 or stop_price <= 0:
            return 0.0
        
        risk_amount = balance * cfg['risk_pct']
        risk_per_unit = abs(entry_price - stop_price)
        
        if risk_per_unit <= 0:
            return 0.0
        
        size = risk_amount / risk_per_unit
        
        # Apply size limits
        max_size = balance * cfg.get('max_size_pct', 0.1) / entry_price
        min_size = cfg.get('min_size', 0.001)
        
        size = max(min_size, min(size, max_size))
        
        return round(size, 6)  # Round to 6 decimal places
        
    except Exception as e:
        logger.error(f"Position size calculation error: {e}")
        return 0.0

def validate_signal(signal: Dict, current_price: float) -> tuple:
    """Validate signal parameters."""
    try:
        required_fields = ['type', 'price', 'stop', 'tp1', 'size']
        for field in required_fields:
            if field not in signal or signal[field] is None:
                return False, f"Missing or null field: {field}"
        
        # Check signal type
        if signal['type'] not in ['BUY', 'SELL']:
            return False, f"Invalid signal type: {signal['type']}"
        
        # Check prices are positive
        for price_field in ['price', 'stop', 'tp1']:
            if signal[price_field] <= 0:
                return False, f"Invalid {price_field}: {signal[price_field]}"
        
        # Check size is positive
        if signal['size'] <= 0:
            return False, f"Invalid size: {signal['size']}"
        
        # Check stop loss logic
        if signal['type'] == 'BUY':
            if signal['stop'] >= signal['price']:
                return False, "Stop loss should be below entry price for BUY"
            if signal['tp1'] <= signal['price']:
                return False, "Take profit should be above entry price for BUY"
        else:  # SELL
            if signal['stop'] <= signal['price']:
                return False, "Stop loss should be above entry price for SELL"
            if signal['tp1'] >= signal['price']:
                return False, "Take profit should be below entry price for SELL"
        
        # Check price proximity to current price
        price_diff_pct = abs(signal['price'] - current_price) / current_price * 100
        if price_diff_pct > 5:  # Signal price shouldn't be too far from current price
            return False, f"Signal price too far from current price: {price_diff_pct:.2f}%"
        
        return True, "Valid signal"
        
    except Exception as e:
        return False, f"Signal validation error: {e}"

def generate_signals(df: pd.DataFrame, cfg: Dict = None, balance: Optional[float] = None, df_htf: Optional[pd.DataFrame] = None) -> List[Dict]:
    """Generate signals with comprehensive error handling and validation."""
    try:
        # Validate inputs
        if df is None or df.empty:
            logger.warning("Empty dataframe provided")
            return []
        
        cfg = validate_config(cfg or {})
        balance = balance or 10000
        
        df = df.copy().reset_index(drop=True)
        min_required_length = max(cfg["bb_period"], cfg["adx_period"], cfg["rsi_period"], cfg.get("swing_period", 10)) + 5
        
        if len(df) < min_required_length:
            logger.warning(f"Insufficient data: {len(df)} < {min_required_length}")
            return []

        # Calculate indicators
        indicators = _get_indicators(df, cfg)
        if not indicators:
            logger.error("Failed to calculate indicators")
            return []
        
        # Get latest values with safety checks
        last_idx = len(df) - 1
        if last_idx < 1:
            return []
        
        try:
            last_close = float(df['close'].iat[last_idx])
            last_atr = float(indicators['atr'].iat[last_idx])
            last_adx = float(indicators['adx'].iat[last_idx])
            last_rsi = float(indicators['rsi'].iat[last_idx])
            last_bb_upper = float(indicators['bb_upper'].iat[last_idx])
            last_bb_lower = float(indicators['bb_lower'].iat[last_idx])
            last_bb_mid = float(indicators['bb_mid'].iat[last_idx])
            
        except (IndexError, ValueError, TypeError) as e:
            logger.error(f"Error extracting indicator values: {e}")
            return []
        
        # FIXED: Regime filter with safety checks
        is_low_volatility = (last_atr / last_close) < cfg["natr_max"] if last_close > 0 else False
        is_not_trending = last_adx < cfg["adx_max"]
        is_range_market = is_low_volatility and is_not_trending
        
        if not is_range_market:
            logger.debug(f"Not a ranging market: volatility={last_atr/last_close:.4f}, ADX={last_adx:.2f}")
            return []
        
        # Volume filter
        volume_ok = True
        if cfg["use_volume_filter"] and 'volume_ma' in indicators:
            try:
                current_volume = float(df['volume'].iat[last_idx])
                avg_volume = float(indicators['volume_ma'].iat[last_idx])
                volume_ok = current_volume > avg_volume
            except Exception as e:
                logger.warning(f"Volume filter error: {e}")
                volume_ok = True
        
        if not volume_ok:
            logger.debug("Volume filter failed")
            return []
        
        # HTF trend filter
        htf_allows_long = True
        htf_allows_short = True
        if cfg["use_htf_filter"] and df_htf is not None and not df_htf.empty:
            try:
                htf_ma = df_htf['close'].rolling(cfg['htf_ma_period']).mean()
                last_htf_ma = float(htf_ma.iloc[-1])
                htf_allows_long = last_close > last_htf_ma
                htf_allows_short = last_close < last_htf_ma
            except Exception as e:
                logger.warning(f"HTF filter error: {e}")
        
        # Entry conditions
        buy_oversold = last_rsi < 30
        sell_overbought = last_rsi > 70
        
        # FIXED: Confirmation entry logic with bounds checking
        buy_trigger = False
        sell_trigger = False
        
        try:
            if cfg["use_confirmation_entry"] and last_idx >= 1:
                # Previous bar data
                prev_low = float(df['low'].iat[last_idx-1])
                prev_high = float(df['high'].iat[last_idx-1])
                prev_bb_lower = float(indicators['bb_lower'].iat[last_idx-1])
                prev_bb_upper = float(indicators['bb_upper'].iat[last_idx-1])
                
                # Confirmation logic: previous bar broke band, current bar closed back inside
                buy_trigger = (prev_low < prev_bb_lower) and (last_close > last_bb_lower)
                sell_trigger = (prev_high > prev_bb_upper) and (last_close < last_bb_upper)
            else:
                # Original touch logic
                buy_trigger = float(df['low'].iat[last_idx]) <= last_bb_lower
                sell_trigger = float(df['high'].iat[last_idx]) >= last_bb_upper
                
        except Exception as e:
            logger.error(f"Entry trigger calculation error: {e}")
            return []
        
        # Generate signals
        signals = []
        
        def create_signal(signal_type: str) -> Optional[Dict]:
            try:
                entry = last_close
                
                # Smart stop loss
                if cfg["use_smart_sl"]:
                    swing_high, swing_low = get_swing_levels(df, cfg["swing_period"])
                    stop = swing_low * 0.999 if signal_type == "BUY" else swing_high * 1.001
                else:
                    # ATR-based stop loss
                    stop = entry - cfg["sl_atr_multiplier"] * last_atr if signal_type == "BUY" else entry + cfg["sl_atr_multiplier"] * last_atr
                
                # Smart take profit
                if cfg["use_smart_tp"]:
                    tp1 = last_bb_mid
                else:
                    # ATR-based take profit
                    tp1 = entry + cfg["tp_atr_multiplier"] * last_atr if signal_type == "BUY" else entry - cfg["tp_atr_multiplier"] * last_atr
                
                # Calculate position size
                size = calculate_position_size(balance, entry, stop, cfg)
                
                signal = {
                    "type": signal_type,
                    "price": float(entry),
                    "size": float(size) if size > 0 else None,
                    "stop": float(stop),
                    "tp1": float(tp1),
                    "reason": f"RS75_v2.1 {'Conf' if cfg['use_confirmation_entry'] else 'Touch'} | RSI: {last_rsi:.1f} | ADX: {last_adx:.1f} | Vol: {volume_ok}"
                }
                
                # Validate signal before returning
                is_valid, error_msg = validate_signal(signal, last_close)
                if not is_valid:
                    logger.warning(f"Invalid signal generated: {error_msg}")
                    return None
                
                return signal
                
            except Exception as e:
                logger.error(f"Error creating {signal_type} signal: {e}")
                return None
        
        # Check final conditions and create signals
        if buy_trigger and buy_oversold and htf_allows_long:
            signal = create_signal("BUY")
            if signal:
                signals.append(signal)
                logger.info(f"BUY signal generated: {signal}")
        
        elif sell_trigger and sell_overbought and htf_allows_short:
            signal = create_signal("SELL")
            if signal:
                signals.append(signal)
                logger.info(f"SELL signal generated: {signal}")
        
        return signals
        
    except Exception as e:
        logger.error(f"Critical error in signal generation: {e}", exc_info=True)
        return []

# Additional utility functions
def get_strategy_status(df: pd.DataFrame, cfg: Dict = None) -> Dict:
    """Get current strategy status and market conditions."""
    try:
        cfg = validate_config(cfg or {})
        
        if df.empty or len(df) < 50:
            return {"status": "insufficient_data", "message": "Not enough data"}
        
        indicators = _get_indicators(df, cfg)
        if not indicators:
            return {"status": "calculation_error", "message": "Failed to calculate indicators"}
        
        last_idx = len(df) - 1
        last_close = df['close'].iat[last_idx]
        last_atr = indicators['atr'].iat[last_idx]
        last_adx = indicators['adx'].iat[last_idx]
        last_rsi = indicators['rsi'].iat[last_idx]
        
        # Market regime
        is_low_vol = (last_atr / last_close) < cfg["natr_max"]
        is_ranging = last_adx < cfg["adx_max"]
        
        status = {
            "status": "ranging" if (is_low_vol and is_ranging) else "trending",
            "rsi": round(last_rsi, 2),
            "adx": round(last_adx, 2),
            "volatility": round((last_atr / last_close) * 100, 3),
            "bb_position": "above" if last_close > indicators['bb_upper'].iat[last_idx] else "below" if last_close < indicators['bb_lower'].iat[last_idx] else "inside",
            "suitable_for_strategy": is_low_vol and is_ranging
        }
        
        return status
        
    except Exception as e:
        return {"status": "error", "message": str(e)}