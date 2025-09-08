import pandas as pd
import os
import time
import threading
from datetime import datetime
import schedule
import yaml
import json
import requests
import logging
import signal
import sys
from collections import deque
from dotenv import load_dotenv

# --- IMPORT COMPONENTS ---
from ars3_broker_ccxt import Broker
from ars3_strategy_rs75 import generate_signals as generate_range_signals, get_strategy_status
from ars3_strategy_trend import generate_signals as generate_trend_signals
from ars3_strategy_utils import MarketRegimeDetector, calculate_regime_indicators

# --- SETUP LOGGING ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('ars3_bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# --- LOAD CONFIG AND VALIDATE ---
load_dotenv()
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

try:
    with open("ars3_config.yaml", "r") as f:
        config = yaml.safe_load(f)
    
    # Bot settings with validation
    COINS = config.get("coins", ["BTC/USDT"])
    TIMEFRAME = config.get("timeframe", "1h")
    HTF_TIMEFRAME = config.get("htf_timeframe", "4h")
    INITIAL_CAPITAL = float(config.get("initial_capital", 10000))
    MAX_DD_PERCENT = float(config.get("max_dd_percent", 15))
    SIMULATION_MODE = config.get("simulation_mode", True)
    COMMISSION_RATE = float(config.get("commission_rate", 0.0004))
    MAX_DAILY_LOSS = float(config.get("max_daily_loss", INITIAL_CAPITAL * 0.05))

    IS_FUTURES = config.get("futures", False) # Added for futures check
    
    # Strategy configs
    RANGE_STRATEGY_CONFIG = config.get("rs75_strategy", {})
    TREND_STRATEGY_CONFIG = config.get("trend_strategy", {})
    REGIME_DETECTION_CONFIG = config.get("regime_detection", {})
    
    logger.info(f"Configuration loaded: {len(COINS)} symbols, Initial capital: ${INITIAL_CAPITAL:,.2f}")
    
except Exception as e:
    logger.critical(f"FATAL: Could not load or parse config file: {e}", exc_info=True)
    exit(1)

# --- GLOBAL VARIABLES WITH THREAD SAFETY ---
broker = None
best_coin = None
position = {}
current_balance = INITIAL_CAPITAL
position_lock = threading.RLock()
balance_lock = threading.RLock()
daily_pnl = 0.0
last_reset_date = datetime.now().date()
shutdown_event = threading.Event()
system_health = {"status": "initializing", "last_check": datetime.now()}

# Initialize Market Regime Detector
regime_detector = MarketRegimeDetector(REGIME_DETECTION_CONFIG)

# Data storage with thread-safe collections
equity_log = deque(maxlen=config.get("data_management", {}).get("max_equity_records", 10000))
trades_log = deque(maxlen=config.get("data_management", {}).get("max_trade_records", 5000))

POSITION_FILE = "position.json"
EMERGENCY_STOP_FILE = config.get("emergency", {}).get("kill_switch_file", "EMERGENCY_STOP")

# --- HELPER FUNCTIONS ---

def init_broker():
    """Initialize broker with proper error handling."""
    global broker
    try:
        broker = Broker(config_path="ars3_config.yaml")
        if broker:
            # Test connection
            test_result = broker.test_connection()
            if test_result.get("connected", False):
                logger.info(f"Broker initialized successfully: {test_result.get('message', 'Connected')}")
                return True
            else:
                logger.error(f"Broker connection test failed: {test_result.get('message', 'Unknown error')}")
                return False
        return False
    except Exception as e:
        logger.error(f"Failed to initialize broker: {e}")
        return False

def save_position(pos_data):
    """Thread-safe position saving with validation."""
    try:
        with position_lock:
            # Validate position data structure
            if pos_data and not isinstance(pos_data, dict):
                logger.error("Invalid position data type")
                return False
            
            # Convert datetime objects to strings for JSON serialization
            serializable_data = {}
            for key, value in pos_data.items():
                if isinstance(value, datetime):
                    serializable_data[key] = value.isoformat()
                elif isinstance(value, (int, float, str, bool)) or value is None:
                    serializable_data[key] = value
                else:
                    serializable_data[key] = str(value)
            
            with open(POSITION_FILE, "w") as f:
                json.dump(serializable_data, f, indent=4)
            
            logger.debug(f"Position saved: {len(serializable_data)} fields")
            return True
            
    except Exception as e:
        logger.error(f"Failed to save position: {e}", exc_info=True)
        return False

def load_position():
    """Load position with enhanced validation and error recovery."""
    if not os.path.exists(POSITION_FILE):
        logger.info("No position file found, starting fresh")
        return {}
    
    try:
        with open(POSITION_FILE, "r") as f:
            pos = json.load(f)
        
        if not pos or not isinstance(pos, dict):
            logger.info("Empty or invalid position data, starting fresh")
            return {}
        
        # Validate required fields
        required_fields = ['symbol', 'type', 'size', 'entry_price']
        missing_fields = [field for field in required_fields if field not in pos or pos[field] is None]
        
        if missing_fields:
            logger.warning(f"Position missing required fields: {missing_fields}, clearing position")
            return {}
        
        # Validate data types
        try:
            pos['size'] = float(pos['size'])
            pos['entry_price'] = float(pos['entry_price'])
            if 'stop_loss' in pos and pos['stop_loss'] is not None:
                pos['stop_loss'] = float(pos['stop_loss'])
            if 'tp1' in pos and pos['tp1'] is not None:
                pos['tp1'] = float(pos['tp1'])
            if 'tp2' in pos and pos['tp2'] is not None:
                pos['tp2'] = float(pos['tp2'])
        except (ValueError, TypeError) as e:
            logger.warning(f"Invalid position data types: {e}, clearing position")
            return {}
        
        # Convert timestamp back to datetime if present
        if 'timestamp' in pos and pos['timestamp']:
            try:
                pos['timestamp'] = datetime.fromisoformat(pos['timestamp'])
            except (ValueError, TypeError):
                pos['timestamp'] = datetime.now()
        
        logger.info(f"Position loaded: {pos['type']} {pos['size']:.6f} {pos['symbol']} at {pos['entry_price']:.4f}")
        return pos
        
    except (json.JSONDecodeError, FileNotFoundError) as e:
        logger.warning(f"Failed to load position file: {e}, starting fresh")
        return {}
    except Exception as e:
        logger.error(f"Unexpected error loading position: {e}")
        return {}

def send_telegram(msg, retry_count=3):
    """Send telegram message with enhanced retry logic and rate limiting."""
    if not BOT_TOKEN or not CHAT_ID:
        return False
    
    for attempt in range(retry_count):
        try:
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
            # Truncate message if too long
            if len(msg) > 4000:
                msg = msg[:3900] + "... [truncated]"
            
            response = requests.post(
                url, 
                data={
                    "chat_id": CHAT_ID, 
                    "text": f"🤖 ARS-3 Bot:\n{msg}",
                    "parse_mode": "Markdown"
                }, 
                timeout=15
            )
            
            if response.status_code == 200:
                logger.debug("Telegram message sent successfully")
                return True
            else:
                logger.warning(f"Telegram send failed (attempt {attempt+1}): Status {response.status_code}")
                
        except requests.RequestException as e:
            logger.error(f"Telegram request error (attempt {attempt+1}): {e}")
        except Exception as e:
            logger.error(f"Unexpected telegram error (attempt {attempt+1}): {e}")
        
        if attempt < retry_count - 1:
            time.sleep(2 ** attempt)  # Exponential backoff
    
    logger.error("Failed to send telegram message after all attempts")
    return False

def get_current_balance():
    """Thread-safe balance retrieval with broker fallback."""
    global current_balance
    
    with balance_lock:
        if SIMULATION_MODE:
            return current_balance
        
        try:
            if broker is None:
                logger.warning("Broker not initialized, using cached balance")
                return current_balance
            
            balance_info = broker.get_balance()
            if balance_info and isinstance(balance_info, dict):
                if 'free' in balance_info and 'USDT' in balance_info['free']:
                    new_balance = float(balance_info['free']['USDT'])
                    if new_balance > 0:  # Sanity check
                        current_balance = new_balance
                        logger.debug(f"Balance updated from broker: ${current_balance:.2f}")
                    else:
                        logger.warning("Received zero balance from broker, using cached value")
            else:
                logger.warning("Invalid balance response from broker")
            
            return current_balance
            
        except Exception as e:
            logger.error(f"Failed to get balance from broker: {e}, using cached value")
            return current_balance

def update_balance_and_pnl(pnl=0.0):
    """Thread-safe balance and P&L tracking with daily reset and limits."""
    global current_balance, daily_pnl, last_reset_date
    
    with balance_lock:
        current_date = datetime.now().date()
        
        # Daily reset logic
        if current_date != last_reset_date:
            logger.info(f"Daily reset: Previous day P&L: ${daily_pnl:.2f}")
            send_telegram(f"📊 *Daily Summary*\nYesterday P&L: ${daily_pnl:.2f}\nStarting fresh day!")
            daily_pnl = 0.0
            last_reset_date = current_date
        
        # Update balances
        current_balance += pnl
        daily_pnl += pnl
        
        logger.info(f"Balance updated: P&L ${pnl:.2f}, Daily P&L: ${daily_pnl:.2f}, Total: ${current_balance:.2f}")
        
        # Check daily loss limit
        if daily_pnl <= -MAX_DAILY_LOSS:
            logger.critical(f"EMERGENCY: Daily loss limit exceeded! Daily P&L: ${daily_pnl:.2f}")
            send_telegram(f"🚨 *EMERGENCY STOP*\nDaily loss limit exceeded: ${daily_pnl:.2f}\nTrading halted!")
            
            try:
                with open(EMERGENCY_STOP_FILE, 'w') as f:
                    f.write(f"Daily loss limit exceeded: ${daily_pnl:.2f} at {datetime.now()}")
                logger.info("Emergency stop file created")
            except Exception as e:
                logger.error(f"Failed to create emergency stop file: {e}")
        
        # Check maximum drawdown
        if current_balance < INITIAL_CAPITAL * (1 - MAX_DD_PERCENT / 100):
            logger.critical(f"EMERGENCY: Maximum drawdown exceeded! Balance: ${current_balance:.2f}")
            send_telegram(f"🚨 *MAXIMUM DRAWDOWN EXCEEDED*\nCurrent balance: ${current_balance:.2f}\nMax allowed: ${INITIAL_CAPITAL * (1 - MAX_DD_PERCENT / 100):.2f}")

def calculate_pnl(entry_price, exit_price, size, position_type, fee_rate):
    """Enhanced P&L calculation with validation."""
    try:
        # Input validation
        if any(x is None or not isinstance(x, (int, float)) for x in [entry_price, exit_price, size]):
            logger.error("Invalid P&L calculation inputs")
            return 0.0
        
        if entry_price <= 0 or exit_price <= 0 or size <= 0 or fee_rate < 0:
            logger.error("Invalid P&L calculation values")
            return 0.0
        
        # Calculate gross P&L
        if position_type.upper() == "BUY":
            gross_pnl = (exit_price - entry_price) * abs(size)
        elif position_type.upper() == "SELL":
            gross_pnl = (entry_price - exit_price) * abs(size)
        else:
            logger.error(f"Invalid position type: {position_type}")
            return 0.0
        
        # Calculate fees
        entry_value = entry_price * abs(size)
        exit_value = exit_price * abs(size)
        total_fees = (entry_value + exit_value) * fee_rate
        
        # Net P&L
        net_pnl = gross_pnl - total_fees
        
        logger.debug(f"P&L calculation: Gross=${gross_pnl:.2f}, Fees=${total_fees:.2f}, Net=${net_pnl:.2f}")
        return net_pnl
        
    except Exception as e:
        logger.error(f"Error calculating P&L: {e}")
        return 0.0

def determine_market_regime(df):
    """Enhanced market regime detection using professional detector."""
    try:
        if df is None or df.empty or len(df) < REGIME_DETECTION_CONFIG.get("min_data_points", 200):
            logger.debug("Insufficient data for regime detection")
            return "undefined"
        
        # Calculate regime indicators
        indicators = calculate_regime_indicators(df, REGIME_DETECTION_CONFIG)
        
        if indicators is None:
            logger.debug("Failed to calculate regime indicators")
            return "undefined"
        
        # Determine regime using professional detector
        regime = regime_detector.determine(indicators)
        
        # Log regime information with scores
        scores = regime_detector.last_scores
        logger.info(f"Market Regime: {regime.upper()}")
        logger.debug(f"Regime scores: Trend={scores.get('trend_score', 0):.3f}, "
                    f"Range={scores.get('range_score', 0):.3f}, "
                    f"ADX={scores.get('adx', 0):.1f}, "
                    f"nATR={scores.get('natr', 0):.4f}")
        
        return regime
        
    except Exception as e:
        logger.error(f"Error determining market regime: {e}")
        return "undefined"

def check_emergency_conditions():
    """Check various emergency conditions that should halt trading."""
    try:
        # Check emergency stop file
        if os.path.exists(EMERGENCY_STOP_FILE):
            return True, "Emergency stop file exists"
        
        # Check broker connectivity (if not simulation)
        if not SIMULATION_MODE and broker:
            try:
                test_result = broker.test_connection()
                if not test_result.get("connected", False):
                    return True, f"Broker disconnected: {test_result.get('message', 'Unknown error')}"
            except Exception as e:
                return True, f"Broker test failed: {e}"
        
        # Check balance sanity
        with balance_lock:
            if current_balance <= 0:
                return True, "Balance is zero or negative"
            
            if current_balance < INITIAL_CAPITAL * 0.1:  # Less than 10% of initial
                return True, f"Balance too low: ${current_balance:.2f}"
        
        return False, "All checks passed"
        
    except Exception as e:
        logger.error(f"Error checking emergency conditions: {e}")
        return True, f"Emergency check error: {e}"

# --- CORE TRADING LOGIC ---
def trading_loop():
    """Enhanced trading loop with professional regime detection."""
    global position, best_coin, system_health
    
    if shutdown_event.is_set():
        return
    
    try:
        system_health["last_check"] = datetime.now()
        system_health["status"] = "running"
        
        # Emergency conditions check
        is_emergency, emergency_reason = check_emergency_conditions()
        if is_emergency:
            logger.warning(f"Emergency condition detected: {emergency_reason}")
            system_health["status"] = f"emergency: {emergency_reason}"
            return
        
        if not best_coin:
            logger.warning("No trading symbol selected, selecting first available")
            best_coin = COINS[0] if COINS else None
            if not best_coin:
                logger.error("No trading symbols configured")
                return
        
        if not broker:
            logger.error("Broker not initialized")
            return
        
        logger.info(f"--- Trading Cycle for {best_coin} ---")
        
        # 1. Fetch market data with retry logic
        max_retries = 3
        ohlcv_base = None
        
        for attempt in range(max_retries):
            try:
                ohlcv_base = broker.fetch_ohlcv(limit=300, symbol=best_coin)  # Increased for regime detection
                if ohlcv_base:
                    break
                logger.warning(f"Empty data received (attempt {attempt + 1})")
            except Exception as e:
                logger.warning(f"Data fetch attempt {attempt + 1} failed: {e}")
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
        
        if not ohlcv_base:
            logger.error("Failed to fetch market data after all retries")
            return
        
        # Prepare DataFrame
        df_base = pd.DataFrame(ohlcv_base, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df_base["timestamp"] = pd.to_datetime(df_base["timestamp"], unit="ms")
        
        if df_base.empty or len(df_base) < 50:
            logger.warning("Insufficient market data for analysis")
            return
        
        latest_candle = df_base.iloc[-1]
        current_price = latest_candle['close']
        current_high = latest_candle['high']
        current_low = latest_candle['low']
        
        logger.debug(f"Current price: {current_price:.4f}, High: {current_high:.4f}, Low: {current_low:.4f}")
        
        # 2. Position Exit Logic (HIGHEST PRIORITY)
        with position_lock:
            if position:
                logger.info(f"Checking exit conditions for {position.get('type', 'UNKNOWN')} position")
                
                try:
                    sl_price = float(position.get("stop_loss", 0))
                    tp1_price = float(position.get("tp1", 0))
                    tp2_price = float(position.get("tp2", 0))
                    is_long = position.get('type', '').upper() == 'BUY'
                    has_hit_tp1 = position.get("has_hit_tp1", False)
                    entry_price = float(position.get("entry_price", 0))
                    current_size = float(position.get("size", 0))
                    
                    exit_reason, exit_price, exit_percentage = None, None, 0.0
                    
                    # Priority 1: Stop Loss
                    if sl_price > 0:
                        if (is_long and current_low <= sl_price) or (not is_long and current_high >= sl_price):
                            exit_reason, exit_price, exit_percentage = "STOP_LOSS", sl_price, 1.0
                    
                    # Priority 2: Take Profit levels
                    if not exit_reason:
                        if tp1_price > 0 and not has_hit_tp1:
                            if (is_long and current_high >= tp1_price) or (not is_long and current_low <= tp1_price):
                                exit_reason, exit_price, exit_percentage = "TAKE_PROFIT_1", tp1_price, 0.5
                        elif tp2_price > 0 and has_hit_tp1:
                            if (is_long and current_high >= tp2_price) or (not is_long and current_low <= tp2_price):
                                exit_reason, exit_price, exit_percentage = "TAKE_PROFIT_2", tp2_price, 1.0
                    
                    # Execute exit if triggered
                    if exit_reason and exit_price > 0:
                        exit_size = current_size * exit_percentage
                        
                        # Calculate P&L
                        pnl = calculate_pnl(entry_price, exit_price, exit_size, position['type'], COMMISSION_RATE)
                        
                        # Update balance
                        update_balance_and_pnl(pnl)
                        
                        # Execute broker order (if live trading)
                        if not SIMULATION_MODE and broker and exit_size > 0:
                            try:
                                side = 'sell' if is_long else 'buy'
                                order_result = broker.execute_order(best_coin, side, abs(exit_size))
                                if not order_result:
                                    logger.error("Failed to execute exit order in live trading")
                                else:
                                    logger.info(f"Live exit order executed: {order_result.get('id', 'N/A')}")
                            except Exception as e:
                                logger.error(f"Error executing live exit order: {e}")
                        
                        # Update position
                        if exit_percentage >= 1.0 or exit_reason in ["STOP_LOSS", "TAKE_PROFIT_2"]:
                            # Full exit
                            position = {}
                            save_position({})
                        else:
                            # Partial exit (TP1)
                            position['size'] = current_size * (1 - exit_percentage)
                            position['has_hit_tp1'] = True
                            save_position(position)
                        
                        # Log and notify
                        msg = f"Position Exit ({exit_reason})\n"
                        msg += f"Symbol: {best_coin}\n"
                        msg += f"Exit Price: {exit_price:.4f}\n"
                        msg += f"Size: {exit_size:.6f}\n"
                        msg += f"P&L: ${pnl:+.2f}\n"
                        msg += f"Balance: ${get_current_balance():.2f}"
                        
                        logger.info(msg.replace('\n', ', '))
                        send_telegram(msg)
                        
                        # Record trade
                        trade_record = {
                            "timestamp": datetime.now(),
                            "type": exit_reason,
                            "symbol": best_coin,
                            "price": exit_price,
                            "size": exit_size,
                            "pnl": pnl,
                            "balance": get_current_balance(),
                            "reason": f"{exit_reason} triggered"
                        }
                        trades_log.append(trade_record)
                        
                except Exception as e:
                    logger.error(f"Error in exit logic: {e}")
        
        # 3. New Entry Logic (only if no position)
        with position_lock:
            if not position:
                logger.debug("Checking for new entry signals")
                
                try:
                    # Get HTF data if needed
                    df_htf = None
                    if RANGE_STRATEGY_CONFIG.get("use_htf_filter", False):
                        try:
                            original_tf = broker.timeframe
                            broker.timeframe = HTF_TIMEFRAME
                            ohlcv_htf = broker.fetch_ohlcv(limit=100, symbol=best_coin)
                            broker.timeframe = original_tf
                            
                            if ohlcv_htf:
                                df_htf = pd.DataFrame(ohlcv_htf, columns=["timestamp", "open", "high", "low", "close", "volume"])
                                df_htf["timestamp"] = pd.to_datetime(df_htf["timestamp"], unit="ms")
                        except Exception as e:
                            logger.warning(f"Failed to get HTF data: {e}")
                            df_htf = None
                    
                    # Determine market regime using professional detector
                    balance = get_current_balance()
                    regime = determine_market_regime(df_base)
                    
                    logger.info(f"Market regime: {regime.upper()}")
                    
                    # Generate signals based on regime
                    signals = []
                    if regime == "ranging":
                        try:
                            signals = generate_range_signals(
                                df_base, 
                                cfg=RANGE_STRATEGY_CONFIG, 
                                balance=balance, 
                                df_htf=df_htf
                            )
                        except Exception as e:
                            logger.error(f"Error generating range signals: {e}")
                            signals = []
                    elif regime == "trending":
                        try:
                            signals = generate_trend_signals(
                                df_base, 
                                cfg=TREND_STRATEGY_CONFIG, 
                                balance=balance
                            )
                        except Exception as e:
                            logger.error(f"Error generating trend signals: {e}")
                            signals = []
                    elif regime == "compression":
                        logger.info("Market in compression - waiting for breakout")
                        signals = []
                    else:
                        logger.debug(f"Undefined regime - no signals generated")
                        signals = []
                    
                    # Process valid signals
                    if signals and len(signals) > 0:
                        signal = signals[0]  # Take first valid signal
                        
                        # Validate signal structure
                        required_fields = ['type', 'size', 'stop', 'tp1']
                        if all(field in signal and signal[field] is not None for field in required_fields):
                            signal_size = float(signal['size'])
                            
                            if signal_size > 0:
                                entry_price = current_price
                                
                                # Validate signal makes sense
                                required_margin = signal_size * entry_price
                                available_balance = get_current_balance()
                                
                                if required_margin <= available_balance * 0.95:  # Don't use more than 95%
                                    
                                    # Execute broker order (if live trading)
                                    order_success = True
                                    if not SIMULATION_MODE and broker:
                                        try:
                                            side = 'buy' if signal['type'].upper() == 'BUY' else 'sell'
                                            order_result = broker.execute_order(best_coin, side, signal_size)
                                            if order_result:
                                                logger.info(f"Live entry order executed: {order_result.get('id', 'N/A')}")
                                                # Update entry price from actual execution if available
                                                if 'price' in order_result and order_result['price']:
                                                    entry_price = float(order_result['price'])
                                            else:
                                                logger.error("Failed to execute entry order in live trading")
                                                order_success = False
                                        except Exception as e:
                                            logger.error(f"Error executing live entry order: {e}")
                                            order_success = False
                                    
                                    if order_success:
                                        # Create position
                                        position = {
                                            "symbol": best_coin,
                                            "type": signal['type'].upper(),
                                            "size": signal_size,
                                            "entry_price": entry_price,
                                            "stop_loss": float(signal['stop']),
                                            "tp1": float(signal['tp1']),
                                            "tp2": float(signal.get('tp2', signal['tp1'] * 1.5)),
                                            "has_hit_tp1": False,
                                            "timestamp": datetime.now(),
                                            "strategy": regime,
                                            "regime_scores": regime_detector.last_scores.copy()
                                        }
                                        
                                        save_position(position)
                                        
                                        # Log and notify
                                        msg = f"NEW POSITION OPENED\n"
                                        msg += f"Strategy: {regime.upper()}\n"
                                        msg += f"Signal: {signal['type']} {best_coin}\n"
                                        msg += f"Entry: {entry_price:.4f}\n"
                                        msg += f"Size: {signal_size:.6f}\n"
                                        msg += f"Stop Loss: {signal['stop']:.4f}\n"
                                        msg += f"TP1: {signal['tp1']:.4f}\n"
                                        msg += f"TP2: {position['tp2']:.4f}\n"
                                        
                                        # Add regime info
                                        scores = regime_detector.last_scores
                                        msg += f"Regime Scores: T={scores.get('trend_score', 0):.2f}, R={scores.get('range_score', 0):.2f}\n"
                                        msg += f"Reason: {signal.get('reason', 'Signal generated')}"
                                        
                                        logger.info(msg.replace('\n', ', '))
                                        send_telegram(msg)
                                        
                                        # Record trade entry
                                        trade_record = {
                                            "timestamp": datetime.now(),
                                            "type": f"ENTRY_{signal['type']}",
                                            "symbol": best_coin,
                                            "price": entry_price,
                                            "size": signal_size,
                                            "balance": available_balance,
                                            "strategy": regime,
                                            "regime_scores": scores,
                                            "reason": signal.get('reason', 'Signal generated')
                                        }
                                        trades_log.append(trade_record)
                                else:
                                    logger.warning(f"Insufficient balance for signal: Required ${required_margin:.2f}, Available ${available_balance:.2f}")
                            else:
                                logger.warning(f"Invalid signal size: {signal_size}")
                        else:
                            missing_fields = [f for f in required_fields if f not in signal or signal[f] is None]
                            logger.warning(f"Signal missing required fields: {missing_fields}")
                    else:
                        logger.debug("No valid entry signals found")
                        
                except Exception as e:
                    logger.error(f"Error in entry logic: {e}")
        
        # 4. Record equity and save data
        try:
            final_balance = get_current_balance()
            equity_record = {
                "timestamp": datetime.now(),
                "equity": final_balance,
                "daily_pnl": daily_pnl,
                "has_position": bool(position),
                "regime": regime_detector.current_regime if 'regime_detector' in globals() else 'undefined'
            }
            equity_log.append(equity_record)
            
            # Save data to files periodically
            if len(equity_log) % 10 == 0:  # Every 10 cycles
                try:
                    equity_df = pd.DataFrame(list(equity_log))
                    equity_df.to_csv("equity_live.csv", index=False)
                    
                    if trades_log:
                        trades_df = pd.DataFrame(list(trades_log))
                        trades_df.to_csv("trades_live.csv", index=False)
                        
                    logger.debug("Data files updated")
                except Exception as e:
                    logger.error(f"Error saving data files: {e}")
            
            system_health["status"] = "healthy"
            
        except Exception as e:
            logger.error(f"Error recording equity: {e}")
        
    except Exception as e:
        logger.error(f"Critical error in trading loop: {e}", exc_info=True)
        send_telegram(f"CRITICAL ERROR in trading loop: {str(e)[:200]}")
        system_health["status"] = f"error: {str(e)[:100]}"

# --- MONITORING AND HEALTH CHECKS ---
def system_monitor():
    """System health monitoring in separate thread."""
    while not shutdown_event.is_set():
        try:
            # Check system health
            current_time = datetime.now()
            last_check = system_health.get("last_check", current_time)
            
            if (current_time - last_check).total_seconds() > 300:  # 5 minutes
                logger.warning("Trading loop appears stuck - no activity for 5 minutes")
                send_telegram("WARNING: Trading loop appears inactive for 5 minutes")
            
            # Check file sizes and disk space
            try:
                import psutil
                disk_usage = psutil.disk_usage('.')
                free_gb = disk_usage.free / (1024**3)
                if free_gb < 1:
                    logger.warning(f"Low disk space: {free_gb:.1f}GB free")
                    send_telegram(f"WARNING: Low disk space - {free_gb:.1f}GB free")
            except ImportError:
                pass
            except Exception as e:
                logger.debug(f"Disk space check error: {e}")
            
            # Memory usage check (basic)
            try:
                if len(equity_log) > 5000:
                    logger.info("Trimming equity log to prevent memory issues")
                    # Keep only recent 2500 records
                    while len(equity_log) > 2500:
                        equity_log.popleft()
                
                if len(trades_log) > 2000:
                    logger.info("Trimming trades log to prevent memory issues")
                    while len(trades_log) > 1000:
                        trades_log.popleft()
            except Exception as e:
                logger.error(f"Memory management error: {e}")
            
            time.sleep(60)  # Check every minute
            
        except Exception as e:
            logger.error(f"System monitor error: {e}")
            time.sleep(60)

# --- GRACEFUL SHUTDOWN ---
def graceful_shutdown(signum, frame):
    """Enhanced graceful shutdown handler."""
    logger.warning(f"Shutdown signal {signum} received. Initiating graceful shutdown...")
    send_telegram("SHUTDOWN INITIATED - Bot is shutting down gracefully...")
    
    shutdown_event.set()
    
    try:
        with position_lock:
            if position and not SIMULATION_MODE:
                force_close = config.get("emergency", {}).get("force_flatten_on_shutdown", True)
                if force_close and broker:
                    logger.info("Attempting to close open position due to shutdown...")
                    try:
                        side = 'sell' if position['type'].upper() == 'BUY' else 'buy'
                        size = abs(float(position.get('size', 0)))
                        
                        if size > 0:
                            order_result = broker.execute_order(best_coin, side, size)
                            if order_result:
                                logger.info("Position closed successfully on shutdown")
                                send_telegram("Position closed due to shutdown")
                            else:
                                logger.error("Failed to close position on shutdown")
                                send_telegram("WARNING: Failed to close position on shutdown")
                    except Exception as e:
                        logger.error(f"Error closing position on shutdown: {e}")
                        send_telegram(f"ERROR: Could not close position on shutdown: {e}")
                
                # Clear position file
                position = {}
                save_position({})
        
        # Final data save
        try:
            if equity_log:
                equity_df = pd.DataFrame(list(equity_log))
                equity_df.to_csv("equity_live_final.csv", index=False)
            
            if trades_log:
                trades_df = pd.DataFrame(list(trades_log))
                trades_df.to_csv("trades_live_final.csv", index=False)
                
            logger.info("Final data backup completed")
        except Exception as e:
            logger.error(f"Error in final data save: {e}")
        
        # Cleanup broker
        if broker:
            try:
                broker.cleanup()
            except Exception as e:
                logger.error(f"Error cleaning up broker: {e}")
        
        logger.info("Graceful shutdown completed")
        send_telegram("Shutdown completed successfully")
        
    except Exception as e:
        logger.error(f"Error during shutdown: {e}")
        send_telegram(f"ERROR during shutdown: {e}")
    
    sys.exit(0)

# --- MAIN EXECUTION ---
if __name__ == "__main__":
    # Setup signal handlers
    signal.signal(signal.SIGINT, graceful_shutdown)
    signal.signal(signal.SIGTERM, graceful_shutdown)
    
    logger.info("=" * 60)
    logger.info("ARS-3 ENHANCED MULTI-STRATEGY TRADING BOT v2.0")
    logger.info("=" * 60)
    
    try:
        # Send startup notification
        mode = "SIMULATION" if SIMULATION_MODE else "LIVE TRADING"
        send_telegram(f"BOT STARTING UP\nMode: {mode}\nInitial Balance: ${INITIAL_CAPITAL:,.2f}\nRegime Detection: ENABLED")
        
        # Thêm cảnh báo về Futures
        if IS_FUTURES and not SIMULATION_MODE:
            warning_msg = "WARNING: Futures mode is enabled, but the bot is NOT equipped to handle leverage and liquidations. This is extremely risky. Proceed with caution."
            logger.warning(warning_msg)
            send_telegram(warning_msg)
        
        # Initialize broker
        if not init_broker():
            logger.critical("Failed to initialize broker, exiting")
            exit(1)
        
        # Load existing position
        position = load_position()
        if position:
            logger.info(f"Resumed existing position: {position['type']} {position['size']:.6f} {position['symbol']}")
            send_telegram(f"Resumed position: {position['type']} {position['size']:.6f} {position['symbol']}")
        
        # Set current balance
        current_balance = get_current_balance()
        logger.info(f"Initial balance: ${current_balance:,.2f}")
        
        # Select trading symbol
        best_coin = COINS[0] if COINS else None
        if not best_coin:
            logger.critical("No trading symbols configured")
            exit(1)
        
        broker.symbol = best_coin
        logger.info(f"Selected trading symbol: {best_coin}")
        
        # Initialize regime detector status
        logger.info(f"Regime detector initialized with config: {REGIME_DETECTION_CONFIG}")
        
        # Start system monitor
        monitor_thread = threading.Thread(target=system_monitor, daemon=True)
        monitor_thread.start()
        logger.info("System monitor started")
        
        # Run initial cycle
        logger.info("Running initial trading cycle...")
        trading_loop()
        
        # Main trading loop with stable timing
        logger.info("Trading scheduler initialized. Loop will run every 60 seconds.")
        send_telegram(f"BOT FULLY OPERATIONAL\nSymbol: {best_coin}\nMode: {mode}")
        logger.info("=" * 60)
        
        while not shutdown_event.is_set():
            start_time = time.time()
            
            try:
                trading_loop()
            except Exception as e:
                logger.error(f"Trading loop error: {e}")
                send_telegram(f"Trading loop error: {str(e)[:150]}")
            
            # Ensure consistent timing
            elapsed = time.time() - start_time
            sleep_time = max(0, 60 - elapsed)
            
            if sleep_time > 0:
                time.sleep(sleep_time)
            else:
                logger.warning(f"Trading loop took {elapsed:.1f}s - longer than 60s interval")
            
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received")
        graceful_shutdown(signal.SIGINT, None)
    except Exception as e:
        logger.critical(f"Fatal error in main execution: {e}", exc_info=True)
        send_telegram(f"FATAL ERROR: {e}")
        sys.exit(1)