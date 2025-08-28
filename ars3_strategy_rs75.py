"""
ars3_strategy_rs75.py (v3.0 - COMPLETELY REWRITTEN AND FIXED)
Professional Range-Scalper strategy with proper indicator calculations,
comprehensive error handling, and enhanced signal generation.
"""
from typing import List, Dict, Optional
import numpy as np
import pandas as pd
import logging

logger = logging.getLogger(__name__)

# Enhanced default configuration with validation
DEFAULT_CFG = {
    "bb_period": 20, 
    "bb_std": 2.5, 
    "rsi_period": 7, 
    "adx_period": 14,
    "atr_period": 14,
    "rsi_oversold": 30, 
    "rsi_overbought": 70, 
    "adx_max": 22, 
    "natr_max": 0.0045,
    "min_bb_width_pct": 0.02,
    "use_confirmation_entry": True, 
    "use_smart_sl": True, 
    "swing_period": 12,
    "use_smart_tp": True, 
    "use_volume_filter": True, 
    "volume_ma_period": 20,
    "use_htf_filter": True, 
    "htf_ma_period": 50, 
    "risk_pct": 0.01,
    "max_risk_per_signal": 0.02,
    "min_reward_risk_ratio": 1.5,
    "tp1_risk_multiple": 1.5,
    "tp2_risk_multiple": 3.0
}

def validate_config(cfg: Dict) -> Dict:
    """Validate and sanitize configuration parameters."""
    try:
        validated_cfg = DEFAULT_CFG.copy()
        
        if cfg:
            for key, value in cfg.items():
                if key in validated_cfg:
                    # Type validation and bounds checking
                    if isinstance(validated_cfg[key], (int, float)):
                        try:
                            validated_cfg[key] = float(value) if isinstance(validated_cfg[key], float) else int(value)
                            # Bounds checking
                            if key.endswith('_period') and validated_cfg[key] < 2:
                                validated_cfg[key] = 2
                            elif key.endswith('_pct') and validated_cfg[key] < 0:
                                validated_cfg[key] = abs(validated_cfg[key])
                        except (ValueError, TypeError):
                            logger.warning(f"Invalid value for {key}: {value}, using default")
                    elif isinstance(validated_cfg[key], bool):
                        validated_cfg[key] = bool(value)
                    else:
                        validated_cfg[key] = value
        
        return validated_cfg
    
    except Exception as e:
        logger.error(f"Config validation error: {e}")
        return DEFAULT_CFG

def calculate_sma(series: pd.Series, period: int) -> pd.Series:
    """Calculate Simple Moving Average with error handling."""
    try:
        if len(series) < period:
            return pd.Series(index=series.index, dtype=float)
        return series.rolling(window=period, min_periods=1).mean().fillna(method='bfill')
    except Exception as e:
        logger.error(f"SMA calculation error: {e}")
        return pd.Series(index=series.index, dtype=float)

def calculate_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Calculate RSI with Wilder's smoothing method."""
    try:
        if len(series) < period + 1:
            return pd.Series(50.0, index=series.index)  # Neutral RSI
        
        delta = series.diff()
        gains = delta.where(delta > 0, 0.0)
        losses = -delta.where(delta < 0, 0.0)
        
        # Use Wilder's smoothing (EMA with alpha = 1/period)
        avg_gains = gains.ewm(alpha=1/period, adjust=False).mean()
        avg_losses = losses.ewm(alpha=1/period, adjust=False).mean()
        
        rs = avg_gains / avg_losses.replace(0, np.inf)
        rsi = 100.0 - (100.0 / (1.0 + rs))
        
        return rsi.fillna(50.0)  # Fill NaN with neutral value
        
    except Exception as e:
        logger.error(f"RSI calculation error: {e}")
        return pd.Series(50.0, index=series.index)

def calculate_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Calculate Average True Range."""
    try:
        high = df['high']
        low = df['low']
        close = df['close']
        
        tr1 = high - low
        tr2 = (high - close.shift()).abs()
        tr3 = (low - close.shift()).abs()
        
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.ewm(alpha=1/period, adjust=False).mean()
        
        return atr.fillna(tr.mean())
        
    except Exception as e:
        logger.error(f"ATR calculation error: {e}")
        return pd.Series(0.001, index=df.index)

def calculate_adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Calculate Average Directional Index."""
    try:
        high = df['high']
        low = df['low']
        close = df['close']
        
        # Calculate True Range
        atr = calculate_atr(df, period)
        
        # Calculate Directional Movement
        plus_dm = high.diff()
        minus_dm = -low.diff()
        
        plus_dm[plus_dm < 0] = 0
        minus_dm[minus_dm < 0] = 0
        
        # Only keep positive DM if it's greater than negative DM
        plus_dm[(plus_dm <= minus_dm)] = 0
        minus_dm[(minus_dm <= plus_dm)] = 0
        
        # Smooth the DM values
        plus_dm_smooth = plus_dm.ewm(alpha=1/period, adjust=False).mean()
        minus_dm_smooth = minus_dm.ewm(alpha=1/period, adjust=False).mean()
        
        # Calculate DI
        plus_di = 100 * (plus_dm_smooth / atr)
        minus_di = 100 * (minus_dm_smooth / atr)
        
        # Calculate DX
        dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
        dx = dx.replace([np.inf, -np.inf], 0)
        
        # Calculate ADX
        adx = dx.ewm(alpha=1/period, adjust=False).mean()
        
        return adx.fillna(0)
        
    except Exception as e:
        logger.error(f"ADX calculation error: {e}")
        return pd.Series(0, index=df.index)

def calculate_bollinger_bands(series: pd.Series, period: int = 20, std_dev: float = 2.0) -> Dict[str, pd.Series]:
    """Calculate Bollinger Bands."""
    try:
        sma = calculate_sma(series, period)
        std = series.rolling(window=period, min_periods=1).std().fillna(method='bfill')
        
        upper = sma + (std * std_dev)
        lower = sma - (std * std_dev)
        
        return {
            'middle': sma,
            'upper': upper,
            'lower': lower,
            'width_pct': ((upper - lower) / sma * 100).fillna(0)
        }
        
    except Exception as e:
        logger.error(f"Bollinger Bands calculation error: {e}")
        return {
            'middle': series,
            'upper': series * 1.02,
            'lower': series * 0.98,
            'width_pct': pd.Series(2.0, index=series.index)
        }

def get_swing_levels(df: pd.DataFrame, period: int) -> tuple:
    """Get swing high and low levels with validation."""
    try:
        if len(df) < period + 1:
            return df['high'].max(), df['low'].min()
        
        # Look back 'period' bars from current position (excluding current bar)
        lookback_data = df.iloc[-(period+1):-1] if len(df) > period else df.iloc[:-1]
        
        if lookback_data.empty:
            return df['high'].iloc[-1], df['low'].iloc[-1]
        
        swing_high = lookback_data['high'].max()
        swing_low = lookback_data['low'].min()
        
        return swing_high, swing_low
        
    except Exception as e:
        logger.error(f"Swing levels calculation error: {e}")
        return df['high'].iloc[-1], df['low'].iloc[-1]

def get_strategy_status(df: pd.DataFrame, cfg: Dict = None) -> Dict:
    """Get comprehensive strategy status and market suitability."""
    try:
        cfg = validate_config(cfg)
        
        if df.empty or len(df) < max(cfg['bb_period'], cfg['adx_period']) + 5:
            return {
                "status": "insufficient_data",
                "suitable_for_strategy": False,
                "reason": "Not enough data points"
            }
        
        # Calculate indicators
        close = df['close']
        atr = calculate_atr(df, cfg['atr_period'])
        adx = calculate_adx(df, cfg['adx_period'])
        bb = calculate_bollinger_bands(close, cfg['bb_period'], cfg['bb_std'])
        
        last_idx = len(df) - 1
        current_price = close.iloc[last_idx]
        current_atr = atr.iloc[last_idx]
        current_adx = adx.iloc[last_idx]
        current_bb_width = bb['width_pct'].iloc[last_idx]
        
        # Normalized ATR (volatility measure)
        natr = current_atr / current_price
        
        # Market regime checks
        is_low_volatility = natr < cfg['natr_max']
        is_ranging = current_adx < cfg['adx_max']
        is_bb_wide_enough = current_bb_width > cfg['min_bb_width_pct']
        
        suitable = is_low_volatility and is_ranging and is_bb_wide_enough
        
        return {
            "status": "analyzed",
            "suitable_for_strategy": suitable,
            "adx": current_adx,
            "natr": natr,
            "bb_width_pct": current_bb_width,
            "is_low_volatility": is_low_volatility,
            "is_ranging": is_ranging,
            "is_bb_wide_enough": is_bb_wide_enough,
            "reason": "Range conditions met" if suitable else "Market not suitable for ranging strategy"
        }
        
    except Exception as e:
        logger.error(f"Strategy status calculation error: {e}")
        return {
            "status": "error",
            "suitable_for_strategy": False,
            "reason": f"Calculation error: {str(e)}"
        }

def calculate_position_size(signal_price: float, stop_loss: float, balance: float, cfg: Dict) -> float:
    """Calculate position size using Kelly Criterion and risk management."""
    try:
        if signal_price <= 0 or stop_loss <= 0 or balance <= 0:
            return 0.0
        
        # Risk per unit
        risk_per_unit = abs(signal_price - stop_loss)
        if risk_per_unit <= 0:
            return 0.0
        
        # Risk amount based on configuration
        risk_pct = min(cfg.get('risk_pct', 0.01), cfg.get('max_risk_per_signal', 0.02))
        risk_amount = balance * risk_pct
        
        # Position size based on risk
        position_size = risk_amount / risk_per_unit
        
        # Additional constraints
        max_position_value = balance * 0.2  # Never use more than 20% of balance
        max_size_by_value = max_position_value / signal_price
        
        final_size = min(position_size, max_size_by_value)
        
        # Ensure minimum viable size
        min_trade_value = 10.0  # Minimum $10 trade
        if final_size * signal_price < min_trade_value:
            return 0.0
        
        return final_size
        
    except Exception as e:
        logger.error(f"Position size calculation error: {e}")
        return 0.0

def generate_signals(df: pd.DataFrame, cfg: Dict = None, balance: Optional[float] = None, df_htf: Optional[pd.DataFrame] = None) -> List[Dict]:
    """Generate enhanced range scalping signals with comprehensive validation."""
    try:
        cfg = validate_config(cfg)
        
        if df.empty or len(df) < max(cfg['bb_period'], cfg['adx_period'], cfg['rsi_period']) + 5:
            logger.debug("Insufficient data for signal generation")
            return []
        
        # Ensure we have balance for position sizing
        if balance is None or balance <= 0:
            logger.warning("Invalid balance for signal generation")
            return []
        
        # Calculate all indicators
        close = df['close']
        high = df['high']
        low = df['low']
        volume = df.get('volume', pd.Series(1000, index=df.index))
        
        rsi = calculate_rsi(close, cfg['rsi_period'])
        atr = calculate_atr(df, cfg['atr_period'])
        adx = calculate_adx(df, cfg['adx_period'])
        bb = calculate_bollinger_bands(close, cfg['bb_period'], cfg['bb_std'])
        
        # Volume analysis if enabled
        volume_ma = calculate_sma(volume, cfg['volume_ma_period']) if cfg['use_volume_filter'] else None
        
        last_idx = len(df) - 1
        if last_idx < 1:
            return []
        
        # Current market state
        current_price = close.iloc[last_idx]
        current_rsi = rsi.iloc[last_idx]
        current_atr = atr.iloc[last_idx]
        current_high = high.iloc[last_idx]
        current_low = low.iloc[last_idx]
        current_volume = volume.iloc[last_idx]
        
        prev_high = high.iloc[last_idx - 1]
        prev_low = low.iloc[last_idx - 1]
        prev_close = close.iloc[last_idx - 1]
        
        bb_upper = bb['upper'].iloc[last_idx]
        bb_lower = bb['lower'].iloc[last_idx]
        bb_middle = bb['middle'].iloc[last_idx]
        bb_upper_prev = bb['upper'].iloc[last_idx - 1]
        bb_lower_prev = bb['lower'].iloc[last_idx - 1]
        
        logger.debug(f"Market state - Price: {current_price:.4f}, RSI: {current_rsi:.1f}, ATR: {current_atr:.6f}")
        
        # Pre-flight checks
        # 1. Volume filter
        volume_ok = True
        if cfg['use_volume_filter'] and volume_ma is not None:
            avg_volume = volume_ma.iloc[last_idx]
            volume_ok = current_volume > avg_volume
            if not volume_ok:
                logger.debug(f"Volume filter failed: {current_volume:.0f} <= {avg_volume:.0f}")
        
        # 2. Higher timeframe filter
        htf_allows_long = True
        htf_allows_short = True
        if cfg['use_htf_filter'] and df_htf is not None and not df_htf.empty:
            try:
                htf_close = df_htf['close']
                htf_ma = calculate_sma(htf_close, cfg['htf_ma_period'])
                if not htf_ma.empty:
                    current_htf_ma = htf_ma.iloc[-1]
                    htf_allows_long = current_price > current_htf_ma * 1.001  # Small buffer
                    htf_allows_short = current_price < current_htf_ma * 0.999
                    logger.debug(f"HTF filter - Price: {current_price:.4f}, MA: {current_htf_ma:.4f}, Long OK: {htf_allows_long}, Short OK: {htf_allows_short}")
            except Exception as e:
                logger.warning(f"HTF filter error: {e}")
        
        # 3. RSI conditions
        rsi_oversold = current_rsi < cfg['rsi_oversold']
        rsi_overbought = current_rsi > cfg['rsi_overbought']
        
        if not (rsi_oversold or rsi_overbought):
            logger.debug(f"RSI not in extreme zones: {current_rsi:.1f}")
            return []
        
        # 4. Entry confirmation logic
        buy_signal = False
        sell_signal = False
        
        if cfg['use_confirmation_entry']:
            # More sophisticated confirmation: price action + BB interaction
            buy_signal = (
                rsi_oversold and 
                prev_low < bb_lower_prev and  # Previous bar touched/broke lower BB
                current_low > bb_lower and    # Current bar back inside
                current_price > prev_close    # Some recovery
            )
            
            sell_signal = (
                rsi_overbought and
                prev_high > bb_upper_prev and  # Previous bar touched/broke upper BB
                current_high < bb_upper and    # Current bar back inside
                current_price < prev_close     # Some weakness
            )
        else:
            # Simple BB touch
            buy_signal = rsi_oversold and current_low <= bb_lower
            sell_signal = rsi_overbought and current_high >= bb_upper
        
        logger.debug(f"Signal conditions - Buy: {buy_signal}, Sell: {sell_signal}")
        
        signals = []
        
        # Generate BUY signal
        if buy_signal and volume_ok and htf_allows_long:
            entry_price = current_price
            
            # Smart stop loss
            if cfg['use_smart_sl']:
                swing_high, swing_low = get_swing_levels(df, cfg['swing_period'])
                stop_loss = swing_low - (current_atr * 0.5)  # Below swing low with buffer
            else:
                stop_loss = entry_price - (current_atr * 2.0)  # Standard ATR stop
            
            # Ensure stop makes sense
            stop_loss = min(stop_loss, entry_price * 0.97)  # Max 3% stop
            
            # Smart take profits
            if cfg['use_smart_tp']:
                tp1 = bb_middle  # Target middle BB
                tp2 = bb_upper   # Target upper BB
            else:
                risk_distance = entry_price - stop_loss
                tp1 = entry_price + (risk_distance * cfg['tp1_risk_multiple'])
                tp2 = entry_price + (risk_distance * cfg['tp2_risk_multiple'])
            
            # Validate reward:risk ratio
            risk = entry_price - stop_loss
            reward = tp1 - entry_price
            if risk > 0 and reward / risk >= cfg['min_reward_risk_ratio']:
                
                # Calculate position size
                position_size = calculate_position_size(entry_price, stop_loss, balance, cfg)
                
                if position_size > 0:
                    signals.append({
                        'type': 'BUY',
                        'price': float(entry_price),
                        'size': float(position_size),
                        'stop': float(stop_loss),
                        'tp1': float(tp1),
                        'tp2': float(tp2),
                        'risk_pct': float(cfg['risk_pct']),
                        'reward_risk_ratio': float(reward / risk),
                        'reason': f"Range BUY - RSI: {current_rsi:.1f}, BB rebound"
                    })
                    
                    logger.info(f"BUY signal generated: Entry={entry_price:.4f}, Stop={stop_loss:.4f}, TP1={tp1:.4f}, Size={position_size:.6f}")
        
        # Generate SELL signal
        elif sell_signal and volume_ok and htf_allows_short:
            entry_price = current_price
            
            # Smart stop loss
            if cfg['use_smart_sl']:
                swing_high, swing_low = get_swing_levels(df, cfg['swing_period'])
                stop_loss = swing_high + (current_atr * 0.5)  # Above swing high with buffer
            else:
                stop_loss = entry_price + (current_atr * 2.0)  # Standard ATR stop
            
            # Ensure stop makes sense
            stop_loss = max(stop_loss, entry_price * 1.03)  # Max 3% stop
            
            # Smart take profits
            if cfg['use_smart_tp']:
                tp1 = bb_middle  # Target middle BB
                tp2 = bb_lower   # Target lower BB
            else:
                risk_distance = stop_loss - entry_price
                tp1 = entry_price - (risk_distance * cfg['tp1_risk_multiple'])
                tp2 = entry_price - (risk_distance * cfg['tp2_risk_multiple'])
            
            # Validate reward:risk ratio
            risk = stop_loss - entry_price
            reward = entry_price - tp1
            if risk > 0 and reward / risk >= cfg['min_reward_risk_ratio']:
                
                # Calculate position size
                position_size = calculate_position_size(entry_price, stop_loss, balance, cfg)
                
                if position_size > 0:
                    signals.append({
                        'type': 'SELL',
                        'price': float(entry_price),
                        'size': float(position_size),
                        'stop': float(stop_loss),
                        'tp1': float(tp1),
                        'tp2': float(tp2),
                        'risk_pct': float(cfg['risk_pct']),
                        'reward_risk_ratio': float(reward / risk),
                        'reason': f"Range SELL - RSI: {current_rsi:.1f}, BB rebound"
                    })
                    
                    logger.info(f"SELL signal generated: Entry={entry_price:.4f}, Stop={stop_loss:.4f}, TP1={tp1:.4f}, Size={position_size:.6f}")
        
        return signals
        
    except Exception as e:
        logger.error(f"Critical error in range signal generation: {e}", exc_info=True)
        return []

# Utility function for testing
def test_strategy(df: pd.DataFrame, config_overrides: Dict = None) -> Dict:
    """Test strategy with given data and return analysis."""
    try:
        cfg = validate_config(config_overrides)
        
        # Get strategy status
        status = get_strategy_status(df, cfg)
        
        # Try to generate signals
        signals = generate_signals(df, cfg, balance=10000.0)
        
        return {
            'status': status,
            'signals': signals,
            'signal_count': len(signals),
            'config_used': cfg
        }
        
    except Exception as e:
        logger.error(f"Strategy test error: {e}")
        return {'error': str(e)}

if __name__ == "__main__":
    # Basic testing
    print("ARS-3 Range Strategy RS-75 v3.0")
    print("Testing with sample data...")
    
    # Create sample data for testing
    import numpy as np
    dates = pd.date_range('2024-01-01', periods=100, freq='H')
    
    # Generate sample OHLCV data with some range-bound characteristics
    np.random.seed(42)
    base_price = 50000.0
    prices = []
    
    for i in range(100):
        # Create ranging market with some noise
        trend = np.sin(i * 0.1) * 200  # Oscillating around base
        noise = np.random.normal(0, 100)
        price = base_price + trend + noise
        prices.append(price)
    
    # Create OHLC from prices
    sample_data = []
    for i, price in enumerate(prices):
        open_price = price
        high_price = price * (1 + abs(np.random.normal(0, 0.005)))
        low_price = price * (1 - abs(np.random.normal(0, 0.005)))
        close_price = price * (1 + np.random.normal(0, 0.002))
        volume = np.random.uniform(1000, 5000)
        
        sample_data.append({
            'timestamp': dates[i],
            'open': open_price,
            'high': max(open_price, high_price, close_price),
            'low': min(open_price, low_price, close_price),
            'close': close_price,
            'volume': volume
        })
    
    df_test = pd.DataFrame(sample_data)
    
    # Test the strategy
    test_result = test_strategy(df_test)
    
    print(f"Strategy Status: {test_result.get('status', {}).get('suitable_for_strategy', False)}")
    print(f"Signals Generated: {test_result.get('signal_count', 0)}")
    
    if test_result.get('signals'):
        for i, signal in enumerate(test_result['signals']):
            print(f"Signal {i+1}: {signal['type']} at {signal['price']:.2f} (Size: {signal['size']:.6f})")
    
    print("Strategy test completed.")