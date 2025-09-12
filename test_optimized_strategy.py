"""
test_optimized_strategy.py

Simple test script for the optimized trend strategy
"""

import pandas as pd
import numpy as np
import yaml
import logging
from datetime import datetime, timedelta

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def create_sample_data(periods: int = 500) -> pd.DataFrame:
    """Create sample OHLCV data for testing."""
    np.random.seed(42)  # For reproducible results
    
    # Generate price data with trend
    base_price = 50000
    trend = np.linspace(0, 0.2, periods)  # 20% uptrend
    noise = np.random.normal(0, 0.02, periods)  # 2% daily volatility
    
    prices = base_price * (1 + trend + noise)
    
    # Generate OHLC data
    data = []
    for i, close in enumerate(prices):
        # Generate realistic OHLC from close price
        volatility = abs(np.random.normal(0, 0.01))  # 1% intraday volatility
        
        high = close * (1 + volatility)
        low = close * (1 - volatility)
        
        # Open price (previous close with gap)
        if i == 0:
            open_price = close
        else:
            gap = np.random.normal(0, 0.005)  # 0.5% gap
            open_price = prices[i-1] * (1 + gap)
        
        # Ensure OHLC consistency
        high = max(high, open_price, close)
        low = min(low, open_price, close)
        
        # Volume (random but realistic)
        volume = np.random.uniform(1000, 5000)
        
        data.append({
            'timestamp': datetime.now() - timedelta(hours=periods-i),
            'open': open_price,
            'high': high,
            'low': low,
            'close': close,
            'volume': volume
        })
    
    return pd.DataFrame(data)

def test_optimized_strategy():
    """Test the optimized trend strategy."""
    print("=" * 80)
    print("TESTING OPTIMIZED TREND STRATEGY")
    print("=" * 80)
    
    # Create sample data
    print("Creating sample data...")
    df = create_sample_data(500)
    print(f"Created {len(df)} bars of sample data")
    print(f"Price range: ${df['close'].min():.2f} - ${df['close'].max():.2f}")
    
    # Test strategy
    print("\nTesting optimized strategy...")
    try:
        from ars3_strategy_trend_v8_optimized import generate_signals
        
        # Mock account state
        account_state = {
            'balance': 10000.0,
            'equity': 10000.0,
            'daily_loss': 0.0
        }
        
        # Generate signals
        signals = generate_signals(df, account_state=account_state)
        
        print(f"Generated {len(signals)} signals")
        
        if signals:
            print("\nSignal Details:")
            for i, signal in enumerate(signals[:5]):  # Show first 5 signals
                print(f"Signal {i+1}:")
                print(f"  Type: {signal['type']}")
                print(f"  Price: ${signal['price']:.2f}")
                print(f"  Size: {signal['size']:.6f}")
                print(f"  Stop: ${signal['stop']:.2f}")
                if 'tp1' in signal:
                    print(f"  TP1: ${signal['tp1']:.2f}")
                if 'tp2' in signal:
                    print(f"  TP2: ${signal['tp2']:.2f}")
                if 'meta' in signal:
                    meta = signal['meta']
                    print(f"  Signal Quality: {meta.get('signal_quality', 'N/A'):.2f}")
                    print(f"  ADX: {meta.get('adx', 'N/A'):.1f}")
                    print(f"  RSI: {meta.get('rsi', 'N/A'):.1f}")
                print()
        else:
            print("No signals generated - this could be due to:")
            print("- Market conditions not meeting entry criteria")
            print("- Signal quality filters being too strict")
            print("- Insufficient data for indicator calculations")
        
        # Test with different configurations
        print("\nTesting with relaxed filters...")
        relaxed_config = {
            'min_signal_quality': 0.5,  # Lower quality threshold
            'adx_threshold': 20,        # Lower ADX threshold
            'min_volume_ratio': 1.0     # Lower volume requirement
        }
        
        relaxed_signals = generate_signals(df, cfg=relaxed_config, account_state=account_state)
        print(f"With relaxed filters: {len(relaxed_signals)} signals")
        
        return True
        
    except ImportError as e:
        print(f"Error importing optimized strategy: {e}")
        return False
    except Exception as e:
        print(f"Error testing strategy: {e}")
        return False

def test_indicators():
    """Test individual indicator calculations."""
    print("\n" + "=" * 80)
    print("TESTING INDICATOR CALCULATIONS")
    print("=" * 80)
    
    # Create sample data
    df = create_sample_data(200)
    
    try:
        from ars3_strategy_trend_v8_optimized import compute_advanced_indicators, load_strategy_config
        
        config = load_strategy_config()
        indicators_df = compute_advanced_indicators(df, config)
        
        print("Indicator calculation successful!")
        print(f"DataFrame shape: {indicators_df.shape}")
        print(f"Columns: {list(indicators_df.columns)}")
        
        # Show latest values
        latest = indicators_df.iloc[-1]
        print(f"\nLatest indicator values:")
        print(f"  Close: ${latest['close']:.2f}")
        print(f"  EMA Fast: ${latest['ema_fast']:.2f}")
        print(f"  EMA Slow: ${latest['ema_slow']:.2f}")
        print(f"  ADX: {latest['adx']:.1f}")
        print(f"  RSI: {latest['rsi']:.1f}")
        print(f"  MACD: {latest['macd']:.4f}")
        print(f"  BB Width: {latest['bb_width']:.4f}")
        print(f"  Volume Ratio: {latest['vol_ratio']:.2f}")
        print(f"  Trend Strength: {latest['trend_strength']:.4f}")
        
        return True
        
    except Exception as e:
        print(f"Error testing indicators: {e}")
        return False

def main():
    """Main test function."""
    print("Starting optimized trend strategy tests...")
    
    # Test indicators first
    indicator_success = test_indicators()
    
    # Test strategy
    strategy_success = test_optimized_strategy()
    
    # Summary
    print("\n" + "=" * 80)
    print("TEST SUMMARY")
    print("=" * 80)
    
    if indicator_success:
        print("✅ Indicator calculations: PASSED")
    else:
        print("❌ Indicator calculations: FAILED")
    
    if strategy_success:
        print("✅ Strategy testing: PASSED")
    else:
        print("❌ Strategy testing: FAILED")
    
    if indicator_success and strategy_success:
        print("\n🎉 All tests passed! The optimized strategy is ready for backtesting.")
    else:
        print("\n⚠️  Some tests failed. Please check the implementation.")
    
    return indicator_success and strategy_success

if __name__ == "__main__":
    main()