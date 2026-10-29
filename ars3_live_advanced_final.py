import pandas as pd
import os
import time
from datetime import datetime
import schedule
import json
import logging

# Import strategy and broker
from ars3_broker_ccxt import Broker
from ars3_strategy_rs75 import generate_signals, strategy_cfg
from common.env import load_yaml
from common.log import setup_logging
from common.notify import send_telegram as notify

setup_logging("ars3_bot.log")
logger = logging.getLogger(__name__)

def validate_config(config):
    """Validate configuration parameters"""
    required_fields = ["coins", "timeframe", "initial_capital", "risk_per_trade"]
    for field in required_fields:
        if field not in config:
            raise ValueError(f"Missing required config field: {field}")
    
    if config["risk_per_trade"] <= 0 or config["risk_per_trade"] > 0.1:
        raise ValueError("risk_per_trade must be between 0 and 0.1 (10%)")
    
    if config["initial_capital"] <= 0:
        raise ValueError("initial_capital must be positive")

try:
    config = load_yaml("ars3_config.yaml")
    validate_config(config)
except Exception as e:
    logger.error(f"Configuration error: {e}")
    raise

# Main bot settings
COINS = config.get("coins", ["BTC/USDT"])
TIMEFRAME = config.get("timeframe", "1h")
INITIAL_CAPITAL = config.get("initial_capital", 10000)
MAX_DD_PERCENT = config.get("max_dd_percent", 15)
SIMULATION_MODE = config.get("simulation_mode", True)
STRATEGY_CONFIG = strategy_cfg(config)

# ---- Global Variables & Logs ----
broker = Broker(config_path="ars3_config.yaml")
equity_log = []
trades_log = []
current_balance = INITIAL_CAPITAL
peak_balance = INITIAL_CAPITAL
halted = False
position = {}  # FIXED: Global position state
POSITION_FILE = "position.json"
last_api_call = 0
MIN_API_INTERVAL = 5.0  # Minimum seconds between API calls

# ---- Rate limiting decorator ----
def rate_limit(min_interval=5.0):
    def decorator(func):
        def wrapper(*args, **kwargs):
            global last_api_call
            now = time.time()
            elapsed = now - last_api_call
            if elapsed < min_interval:
                time.sleep(min_interval - elapsed)
            last_api_call = time.time()
            return func(*args, **kwargs)
        return wrapper
    return decorator

# ---- State Management ----
def save_position(position_data):
    """Save position state to JSON file with error handling"""
    try:
        with open(POSITION_FILE, "w") as f:
            json.dump(position_data, f, indent=4, default=str)
        logger.info(f"Position saved: {position_data}")
    except Exception as e:
        logger.error(f"Failed to save position: {e}")

def load_position():
    """Load position state from JSON file"""
    if os.path.exists(POSITION_FILE):
        try:
            with open(POSITION_FILE, "r") as f:
                pos = json.load(f)
                logger.info(f"Position loaded: {pos}")
                return pos
        except (json.JSONDecodeError, Exception) as e:
            logger.warning(f"Failed to load position file: {e}")
            return {}
    return {}

def validate_position_size(size, balance, price, min_size=0.001):
    """Validate position size before execution"""
    if size is None or size <= 0:
        return False, "Position size must be positive"
    
    notional_value = size * price
    if notional_value > balance * 0.95:
        return False, "Position size too large for available balance"
    
    if size < min_size:
        return False, f"Position size below minimum: {min_size}"
    
    return True, "OK"

def send_telegram(msg):
    return notify(msg, prefix="🤖 ARS-3 Bot:\n")

# ---- Balance Management ----
def update_balance(pnl=0.0):
    """Update current balance after trades"""
    global current_balance
    current_balance += pnl
    logger.info(f"Balance updated: {current_balance:.2f} (PnL: {pnl:+.2f})")
    return current_balance

def get_current_balance():
    """Get current balance with error handling"""
    global current_balance
    if SIMULATION_MODE:
        return current_balance
    else:
        try:
            balance_info = broker.get_balance()
            real_balance = balance_info.get('free', {}).get('USDT', current_balance)
            current_balance = real_balance
            return real_balance
        except Exception as e:
            logger.error(f"Failed to get real balance: {e}")
            return current_balance

def check_emergency_stop():
    """Halt when balance is more than MAX_DD_PERCENT below its peak; alert once per breach."""
    global peak_balance, halted
    peak_balance = max(peak_balance, current_balance)
    drawdown_pct = (peak_balance - current_balance) / peak_balance * 100
    breached = drawdown_pct > MAX_DD_PERCENT
    if breached and not halted:
        logger.critical(f"Emergency stop triggered: drawdown {drawdown_pct:.2f}% from peak")
        send_telegram(f"🚨 EMERGENCY STOP: drawdown {drawdown_pct:.2f}% from peak")
    halted = breached
    return breached

# ---- Main Logic ----
def load_df(symbol):
    """Fetch and validate OHLCV for `symbol`; None when unusable."""
    ohlcv = broker.fetch_ohlcv(limit=200, symbol=symbol)
    if not ohlcv:
        logger.warning(f"{symbol}: no OHLCV data, skipping")
        return None
    df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    is_valid, error_msg = validate_ohlcv_data(df)
    if not is_valid:
        logger.warning(f"{symbol}: data validation failed: {error_msg}")
        return None
    return df

def find_entry(balance):
    """First coin, in config order, with an entry signal: (symbol, df, signals)."""
    for symbol in COINS:
        df = load_df(symbol)
        if df is None:
            continue
        signals = generate_signals(df, cfg=STRATEGY_CONFIG, balance=balance)
        if signals:
            return symbol, df, signals
    return None, None, []

def calculate_pnl(entry_price, exit_price, size, position_type):
    """Calculate PnL for a trade"""
    try:
        if position_type == "BUY":
            return (exit_price - entry_price) * abs(size)
        else:
            return (entry_price - exit_price) * abs(size)
    except (TypeError, ValueError) as e:
        logger.error(f"PnL calculation error: {e}")
        return 0.0

def validate_ohlcv_data(df):
    """Validate OHLCV data quality"""
    if df.empty or len(df) < 50:
        return False, "Insufficient data length"
    
    required_cols = ['open', 'high', 'low', 'close', 'volume']
    missing_cols = [col for col in required_cols if col not in df.columns]
    if missing_cols:
        return False, f"Missing columns: {missing_cols}"
    
    # Check for invalid prices
    if df['close'].isna().any() or (df['close'] <= 0).any():
        return False, "Invalid price data detected"
    
    # Check data consistency (high >= low, etc.)
    if (df['high'] < df['low']).any() or (df['high'] < df['close']).any() or (df['low'] > df['close']).any():
        return False, "Inconsistent OHLCV data"
    
    return True, "OK"

@rate_limit(min_interval=MIN_API_INTERVAL)
def trading_loop():
    """Main trading loop with comprehensive error handling"""
    global equity_log, trades_log, current_balance, position
    
    try:
        # FIXED: Only load position once if empty
        if not position:
            position = load_position()
            if position:
                logger.info(f"Resumed with active position: {position}")
                send_telegram(f"Bot restarted. Resumed position: {position.get('symbol', 'Unknown')} ({position.get('type', 'Unknown')})")

        # Emergency stop check
        if check_emergency_stop():
            logger.critical("Trading halted due to emergency stop")
            return

        # 1. Manage the open position on its own symbol
        if position:
            df = load_df(position["symbol"])
            if df is None:
                return
            current_price = df['close'].iloc[-1]
            current_high = df['high'].iloc[-1]
            current_low = df['low'].iloc[-1]
            try:
                sl_price = position.get("stop_loss", 0)
                tp1_price = position.get("tp1", 0)
                tp2_price = position.get("tp2", 0)
                is_long = position.get('type') == 'BUY'
                has_hit_tp1 = position.get("has_hit_tp1", False)
                entry_price = position.get("entry_price", current_price)
                
                # Check stop loss with intrabar prices
                stop_triggered = False
                if is_long and current_low <= sl_price:
                    stop_triggered = True
                    exit_price = sl_price
                elif not is_long and current_high >= sl_price:
                    stop_triggered = True
                    exit_price = sl_price
                
                # Check take profits
                tp1_triggered = False
                tp2_triggered = False
                
                if not has_hit_tp1:
                    if is_long and current_high >= tp1_price:
                        tp1_triggered = True
                        exit_price = tp1_price
                    elif not is_long and current_low <= tp1_price:
                        tp1_triggered = True
                        exit_price = tp1_price
                elif has_hit_tp1:
                    if is_long and current_high >= tp2_price:
                        tp2_triggered = True
                        exit_price = tp2_price
                    elif not is_long and current_low <= tp2_price:
                        tp2_triggered = True
                        exit_price = tp2_price
                
                # Execute exits
                if stop_triggered:
                    close_side = 'sell' if is_long else 'buy'
                    size = position.get('size', 0)
                    pnl = calculate_pnl(entry_price, exit_price, size, position['type'])
                    
                    msg = f"🛑 STOP LOSS HIT for {position['symbol']} at {exit_price:.4f}"
                    logger.info(msg)
                    send_telegram(msg)
                    
                    if not SIMULATION_MODE:
                        order = broker.execute_order(position['symbol'], close_side, abs(size))
                        if not order:
                            logger.error("Failed to execute stop loss order")
                            return
                    
                    update_balance(pnl)
                    trades_log.append({
                        "timestamp": datetime.now(),
                        "symbol": position['symbol'],
                        "type": f"CLOSE_{position['type']}",
                        "price": exit_price,
                        "size": size,
                        "pnl": pnl,
                        "reason": "Stop Loss"
                    })
                    position = {}
                    save_position({})
                    
                elif tp1_triggered:
                    size = position.get('size', 0)
                    half_size = size * 0.5
                    pnl = calculate_pnl(entry_price, exit_price, half_size, position['type'])
                    
                    msg = f"🎯 TP1 HIT for {position['symbol']} at {exit_price:.4f} (50% closed)"
                    logger.info(msg)
                    send_telegram(msg)
                    
                    if not SIMULATION_MODE:
                        close_side = 'sell' if is_long else 'buy'
                        order = broker.execute_order(position['symbol'], close_side, abs(half_size))
                        if not order:
                            logger.error("Failed to execute TP1 order")
                            return
                    
                    position['size'] = half_size
                    position['has_hit_tp1'] = True
                    save_position(position)
                    
                    update_balance(pnl)
                    trades_log.append({
                        "timestamp": datetime.now(),
                        "symbol": position['symbol'],
                        "type": f"TP1_{position['type']}",
                        "price": exit_price,
                        "size": half_size,
                        "pnl": pnl,
                        "reason": "Take Profit 1"
                    })
                    
                elif tp2_triggered:
                    remaining_size = position.get('size', 0)
                    pnl = calculate_pnl(entry_price, exit_price, remaining_size, position['type'])
                    
                    msg = f"🎯 TP2 HIT for {position['symbol']} at {exit_price:.4f} (Position closed)"
                    logger.info(msg)
                    send_telegram(msg)
                    
                    if not SIMULATION_MODE:
                        close_side = 'sell' if is_long else 'buy'
                        order = broker.execute_order(position['symbol'], close_side, abs(remaining_size))
                        if not order:
                            logger.error("Failed to execute TP2 order")
                            return
                    
                    update_balance(pnl)
                    trades_log.append({
                        "timestamp": datetime.now(),
                        "symbol": position['symbol'],
                        "type": f"TP2_{position['type']}",
                        "price": exit_price,
                        "size": remaining_size,
                        "pnl": pnl,
                        "reason": "Take Profit 2"
                    })
                    position = {}
                    save_position({})
                    
            except Exception as e:
                logger.error(f"Error processing position exits: {e}")

        # 2. While flat, scan every coin for a new entry
        if not position:
            try:
                balance = get_current_balance()
                symbol, df, signals = find_entry(balance)

                if signals:
                    signal = signals[0]
                    current_price = df['close'].iloc[-1]

                    # FIXED: Validate position size
                    is_valid, error_msg = validate_position_size(
                        signal.get('size'), balance, current_price
                    )
                    if not is_valid:
                        logger.warning(f"Invalid position size: {error_msg}")
                        return

                    # Calculate TP2 if missing
                    tp2 = signal.get('tp2')
                    if tp2 is None:
                        if signal['type'] == 'BUY':
                            tp2 = signal['price'] + (signal['tp1'] - signal['price']) * 2
                        else:
                            tp2 = signal['price'] - (signal['price'] - signal['tp1']) * 2

                    msg = (f"🎯 New Signal: {signal['type']} {symbol}\n"
                           f"Price: {current_price:.4f}\n"  # FIXED: Use current price
                           f"Stop: {signal['stop']:.4f}\n"
                           f"TP1: {signal['tp1']:.4f}\n"
                           f"TP2: {tp2:.4f}\n"
                           f"Reason: {signal['reason']}")
                    logger.info(msg)
                    send_telegram(msg)
                    
                    # Execute trade
                    side = 'buy' if signal['type'] == 'BUY' else 'sell'
                    size = signal['size']
                    actual_price = current_price  # Default execution price
                    
                    if not SIMULATION_MODE:
                        order = broker.execute_order(symbol, side, size)
                        if not order:
                            logger.error("Order execution failed")
                            return
                        # FIXED: Use actual execution price if available
                        actual_price = order.get('price', current_price)

                    # FIXED: Save position with actual execution price
                    position = {
                        "symbol": symbol,
                        "type": signal['type'],
                        "size": size,
                        "entry_price": actual_price,  # Use actual price
                        "stop_loss": signal['stop'],
                        "tp1": signal['tp1'],
                        "tp2": tp2,
                        "has_hit_tp1": False,
                        "timestamp": datetime.now().isoformat()
                    }
                    save_position(position)
                    
                    trades_log.append({
                        "timestamp": datetime.now(),
                        "symbol": symbol,
                        "type": signal['type'],
                        "price": actual_price,
                        "size": size,
                        "reason": signal['reason']
                    })
                    
            except Exception as e:
                logger.error(f"Error processing entry signals: {e}")

        # 3. Log equity and save data
        try:
            current_equity = get_current_balance()
            equity_log.append({"timestamp": datetime.now(), "equity": current_equity})
            
            # Save logs with error handling
            pd.DataFrame(equity_log).to_csv("equity_live.csv", index=False)
            if trades_log:
                pd.DataFrame(trades_log).to_csv("trades_live.csv", index=False)
                
        except Exception as e:
            logger.error(f"Error saving logs: {e}")

    except Exception as e:
        error_msg = f"Critical error in trading loop: {e}"
        logger.error(error_msg, exc_info=True)
        send_telegram(f"🚨 CRITICAL ERROR\n{error_msg}")

# ---- Main Execution ----
if __name__ == "__main__":
    logger.info("ARS-3 Bot starting...")
    send_telegram("Bot is starting up...")
    
    try:
        trading_loop()
        schedule.every(1).minutes.do(trading_loop)

        logger.info("Bot started successfully. Waiting for scheduled tasks...")
        
        while True:
            try:
                schedule.run_pending()
                time.sleep(1)
            except KeyboardInterrupt:
                logger.info("Bot stopped by user")
                save_position(position)
                break
            except Exception as e:
                logger.error(f"Scheduler error: {e}", exc_info=True)
                send_telegram(f"🔥 SCHEDULER ERROR: {e}")
                time.sleep(10)
                
    except Exception as e:
        logger.critical(f"Bot startup failed: {e}", exc_info=True)
        send_telegram(f"💥 BOT STARTUP FAILED: {e}")