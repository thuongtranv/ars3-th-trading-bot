#!/usr/bin/env python3
"""
integration_test.py
Test script to verify that all components work together properly
"""

import sys
import os
import pandas as pd
import numpy as np
import yaml
from datetime import datetime, timedelta

def test_config_loading():
    """Test configuration loading"""
    print("Testing configuration loading...")
    try:
        with open("ars3_config.yaml", "r") as f:
            config = yaml.safe_load(f)
        
        # Check required sections
        required_sections = ['regime_detection', 'rs75_strategy', 'trend_strategy', 'risk_management']
        missing_sections = [s for s in required_sections if s not in config]
        
        if missing_sections:
            print(f"❌ Missing config sections: {missing_sections}")
            return False
        
        # Check regime detection config
        regime_config = config['regime_detection']
        required_regime_keys = ['min_data_points', 'adx_period', 'trend_on', 'range_on']
        missing_keys = [k for k in required_regime_keys if k not in regime_config]
        
        if missing_keys:
            print(f"❌ Missing regime detection keys: {missing_keys}")
            return False
        
        print("✅ Configuration loading passed")
        return True, config
        
    except Exception as e:
        print(f"❌ Config loading failed: {e}")
        return False, None

def test_strategy_utils():
    """Test strategy utilities and regime detection"""
    print("Testing strategy utilities...")
    try:
        from ars3_strategy_utils import MarketRegimeDetector, calculate_regime_indicators
        
        # Create test data
        dates = pd.date_range('2024-01-01', periods=300, freq='H')
        np.random.seed(42)
        
        # Generate sample OHLCV data
        base_price = 50000
        prices = []
        volumes = []
        
        for i in range(300):
            # Create different market regimes
            if i < 100:  # Trending phase
                trend = i * 50
                noise = np.random.normal(0, 200)
            elif i < 200:  # Ranging phase
                trend = np.sin(i * 0.1) * 300
                noise = np.random.normal(0, 100)
            else:  # Compression phase
                trend = 0
                noise = np.random.normal(0, 50)
            
            price = base_price + trend + noise
            prices.append(max(price, 1000))  # Ensure positive prices
            volumes.append(np.random.uniform(1000, 5000))
        
        # Create OHLC from prices
        sample_data = []
        for i, price in enumerate(prices):
            open_price = price
            volatility = 0.01 if i >= 200 else 0.02  # Lower volatility in compression
            high_price = price * (1 + abs(np.random.normal(0, volatility)))
            low_price = price * (1 - abs(np.random.normal(0, volatility)))
            close_price = price * (1 + np.random.normal(0, volatility/2))
            
            sample_data.append({
                'timestamp': dates[i],
                'open': open_price,
                'high': max(open_price, high_price, close_price),
                'low': min(open_price, low_price, close_price),
                'close': close_price,
                'volume': volumes[i]
            })
        
        df = pd.DataFrame(sample_data)
        
        # Test regime detection
        regime_config = {
            'min_data_points': 200,
            'adx_period': 14,
            'atr_period': 14,
            'slope_ema_period': 200,
            'slope_period': 50,
            'bb_period': 20,
            'vol_period': 50,
            'adx_low': 18,
            'adx_high': 25,
            'natr_low': 0.003,
            'natr_high': 0.006,
            'slope_abs_low': 0.00005,
            'slope_abs_high': 0.00015,
            'trend_on': 0.60,
            'trend_off': 0.45,
            'range_on': 0.60,
            'range_off': 0.45,
            'bb_width_low': 0.015,
            'cooldown_bars': 3
        }
        
        detector = MarketRegimeDetector(regime_config)
        
        # Test indicator calculation
        indicators = calculate_regime_indicators(df, regime_config)
        if indicators is None:
            print("❌ Failed to calculate regime indicators")
            return False
        
        print(f"✅ Calculated indicators: {list(indicators.keys())}")
        
        # Test regime determination
        regime = detector.determine(indicators)
        print(f"✅ Detected regime: {regime}")
        
        # Test multiple regime detections
        regimes_detected = set()
        for i in range(220, 300, 10):  # Test different windows
            window_df = df.iloc[:i]
            if len(window_df) >= regime_config['min_data_points']:
                indicators = calculate_regime_indicators(window_df, regime_config)
                if indicators:
                    regime = detector.determine(indicators)
                    regimes_detected.add(regime)
        
        print(f"✅ Detected regimes over time: {regimes_detected}")
        print("✅ Strategy utilities test passed")
        return True, df, regime_config
        
    except Exception as e:
        print(f"❌ Strategy utilities test failed: {e}")
        import traceback
        traceback.print_exc()
        return False, None, None

def test_strategy_integration(df, config):
    """Test strategy integration with regime detection"""
    print("Testing strategy integration...")
    try:
        from ars3_strategy_rs75 import generate_signals as generate_range_signals
        from ars3_strategy_trend import generate_signals as generate_trend_signals
        from ars3_strategy_utils import MarketRegimeDetector, calculate_regime_indicators
        
        regime_detector = MarketRegimeDetector(config['regime_detection'])
        
        # Test range strategy
        range_signals = generate_range_signals(
            df.iloc[-100:],  # Use last 100 bars
            cfg=config['rs75_strategy'],
            balance=10000.0
        )
        
        print(f"✅ Range strategy generated {len(range_signals)} signals")
        
        # Test trend strategy  
        trend_signals = generate_trend_signals(
            df.iloc[-100:],
            cfg=config['trend_strategy'],
            balance=10000.0
        )
        
        print(f"✅ Trend strategy generated {len(trend_signals)} signals")
        
        # Test regime-based strategy selection
        indicators = calculate_regime_indicators(df, config['regime_detection'])
        if indicators:
            regime = regime_detector.determine(indicators)
            print(f"✅ Would use {regime} regime -> {'range' if regime == 'ranging' else 'trend' if regime == 'trending' else 'none'} strategy")
        
        print("✅ Strategy integration test passed")
        return True
        
    except Exception as e:
        print(f"❌ Strategy integration test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_backtest_integration():
    """Test backtest engine integration"""
    print("Testing backtest integration...")
    try:
        from ars3_backtest import BacktestEngine, load_config
        
        # Load config
        config = load_config("ars3_config.yaml")
        
        # Create sample data
        np.random.seed(42)
        dates = pd.date_range('2024-01-01', periods=500, freq='H')
        base_price = 50000
        
        sample_data = []
        price = base_price
        
        for i in range(500):
            change = np.random.normal(0, 0.01)  # 1% volatility
            price = price * (1 + change)
            
            open_price = price
            high_price = price * (1 + abs(np.random.normal(0, 0.005)))
            low_price = price * (1 - abs(np.random.normal(0, 0.005)))
            close_price = price * (1 + np.random.normal(0, 0.002))
            
            sample_data.append({
                'timestamp': dates[i],
                'open': open_price,
                'high': max(open_price, high_price, close_price),
                'low': min(open_price, low_price, close_price),
                'close': close_price,
                'volume': np.random.uniform(1000, 5000)
            })
        
        df = pd.DataFrame(sample_data)
        
        # Initialize backtest engine
        engine = BacktestEngine(config, initial_balance=10000)
        
        # Run short backtest
        print("Running regime-aware backtest...")
        results = engine.run_backtest(df, strategy_type='auto')
        
        print(f"✅ Backtest completed:")
        print(f"  Final Balance: ${results['final_balance']:.2f}")
        print(f"  Total Return: {results['total_return_pct']:.2f}%")
        print(f"  Total Trades: {results['total_trades']}")
        print(f"  Win Rate: {results['win_rate_pct']:.1f}%")
        
        if 'regime_analysis' in results:
            regime_analysis = results['regime_analysis']
            if 'regime_distribution' in regime_analysis:
                print(f"  Regime Distribution: {regime_analysis['regime_distribution']}")
        
        print("✅ Backtest integration test passed")
        return True
        
    except Exception as e:
        print(f"❌ Backtest integration test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

def main():
    """Run all integration tests"""
    print("=" * 60)
    print("ARS-3 INTEGRATION TEST SUITE")
    print("=" * 60)
    
    tests_passed = 0
    total_tests = 4
    
    # Test 1: Configuration
    result = test_config_loading()
    if isinstance(result, tuple):
        passed, config = result
        if passed:
            tests_passed += 1
    else:
        config = None
    
    # Test 2: Strategy Utils
    result = test_strategy_utils()
    if isinstance(result, tuple):
        passed, df, regime_config = result
        if passed:
            tests_passed += 1
    else:
        df, regime_config = None, None
    
    # Test 3: Strategy Integration
    if config and df:
        if test_strategy_integration(df, config):
            tests_passed += 1
    else:
        print("⏭️  Skipping strategy integration test (dependencies failed)")
    
    # Test 4: Backtest Integration  
    if test_backtest_integration():
        tests_passed += 1
    
    # Summary
    print("\n" + "=" * 60)
    print("INTEGRATION TEST RESULTS")
    print("=" * 60)
    print(f"Tests Passed: {tests_passed}/{total_tests}")
    
    if tests_passed == total_tests:
        print("✅ ALL TESTS PASSED - Project ready for deployment!")
        return 0
    else:
        print(f"❌ {total_tests - tests_passed} TESTS FAILED - Fix issues before deployment")
        return 1

if __name__ == "__main__":
    sys.exit(main())