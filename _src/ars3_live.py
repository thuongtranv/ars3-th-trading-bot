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
from typing import Optional, List

# --- IMPORT COMPONENTS ---
from ars3_broker_ccxt import Broker
from ars3_strategy_rs75 import generate_signals as generate_range_signals, get_strategy_status
from ars3_strategy_trend import generate_signals as generate_trend_signals
from ars3_strategy_utils import MarketRegimeDetector, calculate_regime_indicators

# --- SETUP LOGGING ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    encoding='utf-8',
    handlers=[
        logging.FileHandler('..\\logs\\ars3_bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# --- LOAD CONFIG AND VALIDATE ---
load_dotenv()
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

try:
    with open("ars3_config.yaml", "r", encoding="utf-8") as f:
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
        
def get_current_equity():
    """Thread-safe equity retrieval (balance + unrealized PNL) with broker fallback."""
    global current_balance  # Giữ current_balance cho simulation
    
    with balance_lock:
        if SIMULATION_MODE:
            # In simulation, equity = current_balance (updated by PNL)
            return current_balance
        
        try:
            if broker is None:
                logger.warning("Broker not initialized, using cached balance as equity")
                return current_balance
            
            # Use new broker method for accurate equity
            equity = broker.get_equity(best_coin)  # Pass symbol if needed
            logger.debug(f"Fetched real equity: {equity}")
            return equity
            
        except Exception as e:
            logger.error(f"Error fetching equity: {e}, using cached balance")
            return current_balance  # Fallback

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

def select_best_coin(coins_to_scan: List[str]) -> Optional[str]:
    """
    Scans a list of coins to find the one with the clearest market regime.
    Returns the symbol of the best coin, or None if no suitable coin is found.
    """
    logger.info("--- Selecting Best Coin for Trading Cycle ---")
    best_candidate = None
    highest_score = 0.0
    analysis_results = []

    for coin in coins_to_scan:
        try:
            logger.debug(f"Analyzing {coin} for regime clarity...")
            
            # Fetch data with timeout protection
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(broker.fetch_ohlcv, 250, coin)
                try:
                    ohlcv = future.result(timeout=8)  # 8 second timeout per coin
                except concurrent.futures.TimeoutError:
                    logger.warning(f"Timeout fetching data for {coin}")
                    continue
            
            if not ohlcv or len(ohlcv) < REGIME_DETECTION_CONFIG.get("min_data_points", 200):
                logger.warning(f"Insufficient data for {coin}: {len(ohlcv) if ohlcv else 0} candles")
                continue
            
            df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
            
            # Calculate regime indicators
            indicators = calculate_regime_indicators(df, REGIME_DETECTION_CONFIG)
            if not indicators:
                logger.warning(f"Failed to calculate indicators for {coin}")
                continue

            # Create temporary detector for scoring
            temp_detector = MarketRegimeDetector(REGIME_DETECTION_CONFIG)
            trend_score, range_score = temp_detector._calculate_scores(indicators)
            
            # Calculate clarity score (max of trend/range scores)
            clarity_score = max(trend_score, range_score)
            
            # Additional quality checks
            current_price = df['close'].iloc[-1]
            price_change_24h = (current_price - df['close'].iloc[-24]) / df['close'].iloc[-24] if len(df) >= 24 else 0
            
            # Penalize extreme volatility or stagnant markets
            if abs(price_change_24h) > 0.15:  # More than 15% change in 24h
                clarity_score *= 0.7
                logger.debug(f"{coin} - High volatility penalty applied: {abs(price_change_24h)*100:.1f}%")
            elif abs(price_change_24h) < 0.005:  # Less than 0.5% change
                clarity_score *= 0.8
                logger.debug(f"{coin} - Low activity penalty applied: {abs(price_change_24h)*100:.1f}%")
            
            analysis_results.append({
                'symbol': coin,
                'clarity_score': clarity_score,
                'trend_score': trend_score,
                'range_score': range_score,
                'regime': 'trending' if trend_score > range_score else 'ranging',
                'adx': indicators.get('adx', 0),
                'natr': indicators.get('natr', 0),
                'price_change_24h': price_change_24h
            })
            
            logger.debug(f"{coin} - Clarity: {clarity_score:.3f} (T:{trend_score:.3f}, R:{range_score:.3f})")

            if clarity_score > highest_score:
                highest_score = clarity_score
                best_candidate = coin
                
        except Exception as e:
            logger.error(f"Error analyzing coin {coin}: {e}")
            continue

    # Log analysis summary
    if analysis_results:
        logger.info("=== COIN ANALYSIS SUMMARY ===")
        for result in sorted(analysis_results, key=lambda x: x['clarity_score'], reverse=True):
            logger.info(f"{result['symbol']}: Score={result['clarity_score']:.3f}, "
                       f"Regime={result['regime']}, ADX={result['adx']:.1f}, "
                       f"24h Change={result['price_change_24h']*100:+.1f}%")

    # Use lower threshold and more flexible criteria
    min_threshold = max(
        REGIME_DETECTION_CONFIG.get("trend_on", 0.55) * 0.8,  # 80% of trend_on threshold
        0.4  # Absolute minimum
    )
    
    if highest_score > min_threshold:
        logger.info(f"✅ Best coin selected: {best_candidate} with score {highest_score:.3f}")
        return best_candidate
    else:
        logger.info(f"❌ No coin with sufficient clarity found. Highest score: {highest_score:.3f}, Required: {min_threshold:.3f}")
        
        # Fallback: return coin with highest score if any analysis was done
        if analysis_results:
            fallback_coin = analysis_results[0]['symbol']  # Already sorted by score
            logger.info(f"🔄 Using fallback coin: {fallback_coin}")
            return fallback_coin
            
        return None

# --- CORE TRADING LOGIC ---
def trading_loop():
    """Enhanced trading loop with comprehensive debugging and coin synchronization."""
    global position, best_coin, system_health
    
    if shutdown_event.is_set():
        return
    
    try:
        system_health["last_check"] = datetime.now()
        system_health["status"] = "running"
        
        # === COIN SYNCHRONIZATION AND VALIDATION ===
        if broker and best_coin:
            if broker.symbol != best_coin:
                logger.info(f"Syncing broker symbol: {broker.symbol} -> {best_coin}")
                broker.symbol = best_coin
        
        # Validate current best_coin
        if not best_coin:
            logger.error("No trading symbol available in trading_loop")
            system_health["status"] = "error: no symbol"
            return
            
        if best_coin not in COINS:
            logger.error(f"Invalid best_coin: {best_coin} not in configured coins: {COINS}")
            system_health["status"] = "error: invalid symbol"
            return
        
        # === EMERGENCY CONDITIONS CHECK ===
        is_emergency, emergency_reason = check_emergency_conditions()
        if is_emergency:
            logger.warning(f"Emergency condition detected: {emergency_reason}")
            system_health["status"] = f"emergency: {emergency_reason}"
            return
        
        logger.info(f"=== TRADING CYCLE START - {best_coin} ===")
        logger.info(f"Current position: {bool(position)}")
        logger.info(f"Current balance: ${get_current_balance():.2f}")
        logger.info(f"Daily P&L: ${daily_pnl:+.2f}")
        
        # === POSITION SYMBOL CONFLICT CHECK ===
        with position_lock:
            if position and position.get('symbol') != best_coin:
                position_symbol = position.get('symbol')
                logger.warning(f"Position symbol mismatch: Position={position_symbol}, Selected={best_coin}")
                # Ưu tiên position symbol để tránh confusion
                best_coin = position_symbol
                if broker:
                    broker.symbol = best_coin
                logger.info(f"Switching to position symbol: {best_coin}")
        
        # === MARKET DATA FETCHING ===
        max_retries = 3
        ohlcv_base = None
        
        logger.debug(f"Fetching market data for {best_coin}...")
        for attempt in range(max_retries):
            try:
                # Add timeout to broker call
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(broker.fetch_ohlcv, 300, best_coin)
                    ohlcv_base = future.result(timeout=12)  # 12 second timeout
                    
                if ohlcv_base and len(ohlcv_base) > 0:
                    logger.debug(f"Successfully fetched {len(ohlcv_base)} candles")
                    break
                else:
                    logger.warning(f"Empty data received (attempt {attempt + 1})")
                    
            except concurrent.futures.TimeoutError:
                logger.warning(f"Data fetch timeout (attempt {attempt + 1})")
            except Exception as e:
                logger.warning(f"Data fetch attempt {attempt + 1} failed: {e}")
            
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
        
        if not ohlcv_base:
            logger.error("Failed to fetch market data after all retries")
            system_health["status"] = "error: data fetch failed"
            return
        
        # === DATA PREPARATION ===
        df_base = pd.DataFrame(ohlcv_base, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df_base["timestamp"] = pd.to_datetime(df_base["timestamp"], unit="ms")
        
        if df_base.empty or len(df_base) < 50:
            logger.warning("Insufficient market data for analysis")
            return
        
        latest_candle = df_base.iloc[-1]
        current_price = latest_candle['close']
        current_high = latest_candle['high']
        current_low = latest_candle['low']
        
        logger.info(f"Market data - Price: {current_price:.4f}, High: {current_high:.4f}, Low: {current_low:.4f}")
        
        # === POSITION EXIT LOGIC (HIGHEST PRIORITY) ===
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
                    
                    logger.debug(f"Position details: Entry={entry_price:.4f}, Size={current_size:.6f}")
                    logger.debug(f"Exit levels: SL={sl_price:.4f}, TP1={tp1_price:.4f}, TP2={tp2_price:.4f}")
                    
                    exit_reason, exit_price, exit_percentage = None, None, 0.0
                    
                    # Priority 1: Stop Loss
                    if sl_price > 0:
                        if (is_long and current_low <= sl_price) or (not is_long and current_high >= sl_price):
                            exit_reason, exit_price, exit_percentage = "STOP_LOSS", sl_price, 1.0
                            logger.info(f"Stop loss triggered: {exit_price:.4f}")
                    
                    # Priority 2: Take Profit levels
                    if not exit_reason:
                        if tp1_price > 0 and not has_hit_tp1:
                            if (is_long and current_high >= tp1_price) or (not is_long and current_low <= tp1_price):
                                exit_reason, exit_price, exit_percentage = "TAKE_PROFIT_1", tp1_price, 0.5
                                logger.info(f"Take profit 1 triggered: {exit_price:.4f}")
                        elif tp2_price > 0 and has_hit_tp1:
                            if (is_long and current_high >= tp2_price) or (not is_long and current_low <= tp2_price):
                                exit_reason, exit_price, exit_percentage = "TAKE_PROFIT_2", tp2_price, 1.0
                                logger.info(f"Take profit 2 triggered: {exit_price:.4f}")
                    
                    # Execute exit if triggered
                    if exit_reason and exit_price > 0:
                        exit_size = current_size * exit_percentage
                        logger.info(f"Executing exit: {exit_reason}, Size: {exit_size:.6f}")
                        
                        # Calculate P&L
                        pnl = calculate_pnl(entry_price, exit_price, exit_size, position['type'], COMMISSION_RATE)
                        logger.info(f"Calculated P&L: ${pnl:+.2f}")
                        
                        # Update balance
                        update_balance_and_pnl(pnl)
                        
                        # Execute broker order (if live trading)
                        order_executed = True
                        if not SIMULATION_MODE and broker and exit_size > 0:
                            try:
                                side = 'sell' if is_long else 'buy'
                                logger.info(f"Executing live exit order: {side} {exit_size:.6f} {best_coin}")
                                order_result = broker.execute_order(best_coin, side, abs(exit_size))
                                if not order_result:
                                    logger.error("Failed to execute exit order in live trading")
                                    order_executed = False
                                else:
                                    logger.info(f"Live exit order executed: {order_result.get('id', 'N/A')}")
                            except Exception as e:
                                logger.error(f"Error executing live exit order: {e}")
                                order_executed = False
                        
                        if order_executed:
                            # Update position
                            if exit_percentage >= 1.0 or exit_reason in ["STOP_LOSS", "TAKE_PROFIT_2"]:
                                # Full exit
                                logger.info("Full position exit completed")
                                position = {}
                                save_position({})
                            else:
                                # Partial exit (TP1)
                                position['size'] = current_size * (1 - exit_percentage)
                                position['has_hit_tp1'] = True
                                logger.info(f"Partial exit completed, remaining size: {position['size']:.6f}")
                                save_position(position)
                            
                            # Log and notify
                            msg = f"Position Exit ({exit_reason})\n"
                            msg += f"Symbol: {best_coin}\n"
                            msg += f"Exit Price: {exit_price:.4f}\n"
                            msg += f"Size: {exit_size:.6f}\n"
                            msg += f"P&L: ${pnl:+.2f}\n"
                            msg += f"Balance: ${get_current_balance():.2f}"
                            
                            logger.info(f"EXIT EXECUTED: {msg.replace(chr(10), ', ')}")
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
                        else:
                            logger.error("Exit order failed, position remains open")
                        
                except Exception as e:
                    logger.error(f"Error in exit logic: {e}", exc_info=True)
        
        # === NEW ENTRY LOGIC (only if no position) ===
        with position_lock:
            if not position:
                logger.info("No position - checking for new entry signals...")
                
                try:
                    # Get HTF data if needed
                    df_htf = None
                    if (RANGE_STRATEGY_CONFIG.get("use_htf_filter", False) or 
                        TREND_STRATEGY_CONFIG.get("use_htf_filter", False)):
                        try:
                            logger.debug(f"Fetching HTF data ({HTF_TIMEFRAME})...")
                            original_tf = broker.timeframe
                            broker.timeframe = HTF_TIMEFRAME
                            ohlcv_htf = broker.fetch_ohlcv(limit=100, symbol=best_coin)
                            broker.timeframe = original_tf
                            
                            if ohlcv_htf:
                                df_htf = pd.DataFrame(ohlcv_htf, columns=["timestamp", "open", "high", "low", "close", "volume"])
                                df_htf["timestamp"] = pd.to_datetime(df_htf["timestamp"], unit="ms")
                                logger.debug(f"HTF data fetched: {len(df_htf)} candles")
                            else:
                                logger.warning("Failed to fetch HTF data")
                        except Exception as e:
                            logger.warning(f"HTF data fetch error: {e}")
                            df_htf = None
                    
                    # Determine market regime using professional detector
                    balance = get_current_balance()
                    regime = determine_market_regime(df_base)
                    
                    logger.info(f"Market Analysis:")
                    logger.info(f"  - Regime: {regime.upper()}")
                    logger.info(f"  - Balance: ${balance:.2f}")
                    logger.info(f"  - Data points: {len(df_base)}")
                    logger.info(f"  - Current price: {current_price:.4f}")
                    
                    if hasattr(regime_detector, 'last_scores') and regime_detector.last_scores:
                        scores = regime_detector.last_scores
                        logger.info(f"  - Regime scores: Trend={scores.get('trend_score', 0):.3f}, Range={scores.get('range_score', 0):.3f}")
                        logger.info(f"  - Market indicators: ADX={scores.get('adx', 0):.1f}, nATR={scores.get('natr', 0):.4f}")
                    
                    # Generate signals based on regime
                    signals = []
                    signal_generation_error = None
                    
                    if regime == "ranging":
                        logger.info("Generating RANGE signals...")
                        try:
                            # Check strategy suitability first
                            from ars3_strategy_rs75 import get_strategy_status
                            status = get_strategy_status(df_base, RANGE_STRATEGY_CONFIG)
                            logger.info(f"Range strategy status: {status}")
                            
                            if status.get('suitable_for_strategy', False):
                                signals = generate_range_signals(
                                    df_base, 
                                    cfg=RANGE_STRATEGY_CONFIG, 
                                    balance=balance, 
                                    df_htf=df_htf
                                )
                                logger.info(f"Range signals generated: {len(signals)}")
                            else:
                                logger.info(f"Range strategy not suitable: {status.get('reason', 'Unknown')}")
                                signal_generation_error = status.get('reason', 'Strategy not suitable')
                        except Exception as e:
                            logger.error(f"Error generating range signals: {e}", exc_info=True)
                            signal_generation_error = str(e)
                            signals = []
                    
                    elif regime == "trending":
                        logger.info("Generating TREND signals...")
                        try:
                            account_state = {
                                'balance': balance,
                                'equity': get_current_equity(),
                                'daily_loss': daily_pnl
                            }
                            logger.debug(f"Account state for trend strategy: {account_state}")
                            
                            signals = generate_trend_signals(
                                df_base, 
                                cfg=TREND_STRATEGY_CONFIG,
                                account_state=account_state
                            )
                            logger.info(f"Trend signals generated: {len(signals)}")
                        except Exception as e:
                            logger.error(f"Error generating trend signals: {e}", exc_info=True)
                            signal_generation_error = str(e)
                            signals = []
                    
                    elif regime == "compression":
                        logger.info("Market in compression - waiting for breakout")
                        signals = []
                        signal_generation_error = "Market in compression phase"
                    else:
                        logger.info(f"Undefined regime '{regime}' - no signals generated")
                        signals = []
                        signal_generation_error = f"Undefined regime: {regime}"
                    
                    # === SIGNAL PROCESSING AND VALIDATION ===
                    if signals and len(signals) > 0:
                        logger.info(f"SIGNALS FOUND: {len(signals)} signal(s)")
                        
                        for i, signal in enumerate(signals):
                            logger.info(f"  Signal {i+1}: {signal}")
                            
                            # Validate signal structure
                            required_fields = ['type', 'size', 'stop', 'tp1']
                            missing_fields = [field for field in required_fields if field not in signal or signal[field] is None]
                            
                            if missing_fields:
                                logger.error(f"Signal {i+1} missing required fields: {missing_fields}")
                                continue
                            
                            try:
                                signal_size = float(signal['size'])
                                signal_stop = float(signal['stop'])
                                signal_tp1 = float(signal['tp1'])
                                signal_type = str(signal['type']).upper()
                                
                                if signal_size <= 0:
                                    logger.error(f"Signal {i+1} invalid size: {signal_size}")
                                    continue
                                
                                if signal_type not in ['BUY', 'SELL']:
                                    logger.error(f"Signal {i+1} invalid type: {signal_type}")
                                    continue
                                
                                # Position sizing validation
                                entry_price = current_price
                                required_margin = signal_size * entry_price
                                available_balance = balance
                                
                                logger.info(f"  Position sizing analysis:")
                                logger.info(f"    - Entry price: {entry_price:.4f}")
                                logger.info(f"    - Signal size: {signal_size:.6f}")
                                logger.info(f"    - Required margin: ${required_margin:.2f}")
                                logger.info(f"    - Available balance: ${available_balance:.2f}")
                                logger.info(f"    - Balance usage: {required_margin/available_balance*100:.1f}%")
                                
                                # Risk validation
                                risk_distance = abs(entry_price - signal_stop)
                                risk_amount = signal_size * risk_distance
                                risk_pct = risk_amount / available_balance * 100
                                
                                logger.info(f"    - Stop loss: {signal_stop:.4f}")
                                logger.info(f"    - Risk distance: {risk_distance:.4f}")
                                logger.info(f"    - Risk amount: ${risk_amount:.2f}")
                                logger.info(f"    - Risk percentage: {risk_pct:.2f}%")
                                
                                if required_margin <= available_balance * 0.95:  # Don't use more than 95%
                                    logger.info(f"  Signal {i+1} VALIDATED - proceeding with execution")
                                    
                                    # Execute broker order (if live trading)
                                    order_success = True
                                    actual_entry_price = entry_price
                                    
                                    if not SIMULATION_MODE and broker:
                                        try:
                                            side = 'buy' if signal_type == 'BUY' else 'sell'
                                            logger.info(f"Executing live entry order: {side} {signal_size:.6f} {best_coin}")
                                            order_result = broker.execute_order(best_coin, side, signal_size)
                                            
                                            if order_result:
                                                logger.info(f"Live entry order executed: {order_result.get('id', 'N/A')}")
                                                # Update entry price from actual execution if available
                                                if 'price' in order_result and order_result['price']:
                                                    actual_entry_price = float(order_result['price'])
                                                    logger.info(f"Actual entry price: {actual_entry_price:.4f}")
                                            else:
                                                logger.error("Failed to execute entry order in live trading")
                                                order_success = False
                                        except Exception as e:
                                            logger.error(f"Error executing live entry order: {e}")
                                            order_success = False
                                    
                                    if order_success:
                                        # Create position
                                        new_position = {
                                            "symbol": best_coin,
                                            "type": signal_type,
                                            "size": signal_size,
                                            "entry_price": actual_entry_price,
                                            "stop_loss": signal_stop,
                                            "tp1": signal_tp1,
                                            "tp2": float(signal.get('tp2', signal_tp1 * 1.5)),
                                            "has_hit_tp1": False,
                                            "timestamp": datetime.now(),
                                            "strategy": regime,
                                            "regime_scores": regime_detector.last_scores.copy() if hasattr(regime_detector, 'last_scores') else {}
                                        }
                                        
                                        position = new_position
                                        save_position(position)
                                        
                                        # Log and notify
                                        msg = f"NEW POSITION OPENED\n"
                                        msg += f"Strategy: {regime.upper()}\n"
                                        msg += f"Signal: {signal_type} {best_coin}\n"
                                        msg += f"Entry: {actual_entry_price:.4f}\n"
                                        msg += f"Size: {signal_size:.6f}\n"
                                        msg += f"Stop Loss: {signal_stop:.4f}\n"
                                        msg += f"TP1: {signal_tp1:.4f}\n"
                                        msg += f"TP2: {new_position['tp2']:.4f}\n"
                                        
                                        # Add regime info if available
                                        if hasattr(regime_detector, 'last_scores') and regime_detector.last_scores:
                                            scores = regime_detector.last_scores
                                            msg += f"Regime Scores: T={scores.get('trend_score', 0):.2f}, R={scores.get('range_score', 0):.2f}\n"
                                        
                                        msg += f"Reason: {signal.get('reason', 'Signal generated')}"
                                        
                                        logger.info(f"POSITION OPENED: {msg.replace(chr(10), ', ')}")
                                        send_telegram(msg)
                                        
                                        # Record trade entry
                                        trade_record = {
                                            "timestamp": datetime.now(),
                                            "type": f"ENTRY_{signal_type}",
                                            "symbol": best_coin,
                                            "price": actual_entry_price,
                                            "size": signal_size,
                                            "balance": available_balance,
                                            "strategy": regime,
                                            "regime_scores": regime_detector.last_scores.copy() if hasattr(regime_detector, 'last_scores') else {},
                                            "reason": signal.get('reason', 'Signal generated')
                                        }
                                        trades_log.append(trade_record)
                                        
                                        # Only process first valid signal
                                        break
                                    else:
                                        logger.error(f"Signal {i+1} execution failed")
                                else:
                                    logger.warning(f"Signal {i+1} rejected - insufficient balance: Required ${required_margin:.2f}, Available ${available_balance:.2f}")
                            
                            except (ValueError, TypeError) as e:
                                logger.error(f"Signal {i+1} data conversion error: {e}")
                                continue
                    
                    else:
                        logger.info("NO SIGNALS GENERATED")
                        
                        # === DETAILED DIAGNOSIS FOR NO SIGNALS ===
                        logger.info("Diagnosing why no signals were generated...")
                        
                        if signal_generation_error:
                            logger.info(f"Signal generation issue: {signal_generation_error}")
                        
                        # Check regime detection thresholds
                        if hasattr(regime_detector, 'last_scores') and regime_detector.last_scores:
                            scores = regime_detector.last_scores
                            trend_threshold = REGIME_DETECTION_CONFIG.get('trend_on', 0.55)
                            range_threshold = REGIME_DETECTION_CONFIG.get('range_on', 0.55)
                            
                            logger.info(f"Regime Analysis:")
                            logger.info(f"  - Trend score: {scores.get('trend_score', 0):.3f} (need > {trend_threshold})")
                            logger.info(f"  - Range score: {scores.get('range_score', 0):.3f} (need > {range_threshold})")
                            
                            if max(scores.get('trend_score', 0), scores.get('range_score', 0)) < 0.4:
                                logger.warning("Very low regime clarity - consider lowering thresholds")
                        
                        # Strategy-specific diagnosis
                        if regime == "ranging":
                            try:
                                logger.info("Range Strategy Diagnosis:")
                                from ars3_strategy_rs75 import calculate_rsi, calculate_bollinger_bands, calculate_adx
                                
                                close = df_base['close']
                                rsi = calculate_rsi(close, RANGE_STRATEGY_CONFIG.get('rsi_period', 7))
                                bb = calculate_bollinger_bands(close, 
                                    RANGE_STRATEGY_CONFIG.get('bb_period', 20), 
                                    RANGE_STRATEGY_CONFIG.get('bb_std', 2.5))
                                adx = calculate_adx(df_base, RANGE_STRATEGY_CONFIG.get('adx_period', 14))
                                
                                current_rsi = rsi.iloc[-1]
                                current_adx = adx.iloc[-1]
                                bb_upper = bb['upper'].iloc[-1]
                                bb_lower = bb['lower'].iloc[-1]
                                bb_width = bb.get('width_pct', pd.Series([0])).iloc[-1]
                                
                                logger.info(f"  - RSI: {current_rsi:.1f} (OS<{RANGE_STRATEGY_CONFIG.get('rsi_oversold', 30)}, OB>{RANGE_STRATEGY_CONFIG.get('rsi_overbought', 70)})")
                                logger.info(f"  - ADX: {current_adx:.1f} (Max: {RANGE_STRATEGY_CONFIG.get('adx_max', 22)})")
                                logger.info(f"  - BB Width: {bb_width:.3f}% (Min: {RANGE_STRATEGY_CONFIG.get('min_bb_width_pct', 0.02)}%)")
                                logger.info(f"  - Price vs BB: Lower={bb_lower:.4f}, Price={current_price:.4f}, Upper={bb_upper:.4f}")
                                
                                # Check each condition
                                rsi_extreme = current_rsi < RANGE_STRATEGY_CONFIG.get('rsi_oversold', 30) or current_rsi > RANGE_STRATEGY_CONFIG.get('rsi_overbought', 70)
                                adx_ok = current_adx < RANGE_STRATEGY_CONFIG.get('adx_max', 22)
                                bb_width_ok = bb_width > RANGE_STRATEGY_CONFIG.get('min_bb_width_pct', 0.02)
                                
                                logger.info(f"  - Conditions: RSI_Extreme={rsi_extreme}, ADX_OK={adx_ok}, BB_Width_OK={bb_width_ok}")
                                
                                if not rsi_extreme:
                                    logger.info("    * RSI not in extreme zones - main reason for no range signals")
                                if not adx_ok:
                                    logger.info("    * ADX too high - market might be trending")
                                if not bb_width_ok:
                                    logger.info("    * Bollinger Bands too narrow")
                                
                            except Exception as e:
                                logger.error(f"Range diagnosis error: {e}")
                        
                        elif regime == "trending":
                            logger.info("Trend Strategy Diagnosis:")
                            logger.info("  - Check trend_strategy config parameters")
                            logger.info(f"  - ADX threshold: {TREND_STRATEGY_CONFIG.get('adx_threshold', 25)}")
                            logger.info(f"  - Min signal quality: {TREND_STRATEGY_CONFIG.get('min_signal_quality', 0.6)}")
                        
                except Exception as e:
                    logger.error(f"Error in entry logic: {e}", exc_info=True)
            else:
                logger.debug("Position exists - skipping entry logic")
        
        # === RECORD EQUITY AND SAVE DATA ===
        try:
            final_balance = get_current_balance()
            final_equity = get_current_equity()
            
            equity_record = {
                "timestamp": datetime.now(),
                "balance": final_balance,
                "equity": final_equity,
                "daily_pnl": daily_pnl,
                "has_position": bool(position),
                "symbol": best_coin,
                "regime": regime_detector.current_regime if hasattr(regime_detector, 'current_regime') else 'undefined'
            }
            equity_log.append(equity_record)
            
            logger.debug(f"Equity recorded: Balance=${final_balance:.2f}, Equity=${final_equity:.2f}")
            
            # Save data to files periodically
            if len(equity_log) % 10 == 0:  # Every 10 cycles
                try:
                    # Append new equity record
                    last_equity_record = equity_log[-1]
                    equity_df_new = pd.DataFrame([last_equity_record])
                    equity_filepath = "..\\trading\\equity_live.csv"
                    equity_df_new.to_csv(
                        equity_filepath, 
                        mode='a', 
                        header=not os.path.exists(equity_filepath), 
                        index=False
                    )
                    
                    # Save trades if any
                    if trades_log:
                        trades_df = pd.DataFrame(list(trades_log))
                        trades_df.to_csv("..\\trading\\trades_live.csv", index=False)
                        
                    logger.debug("Data files updated")
                except Exception as e:
                    logger.error(f"Error saving data files: {e}")
            
            system_health["status"] = "healthy"
            
        except Exception as e:
            logger.error(f"Error recording equity: {e}")
        
        logger.info(f"=== TRADING CYCLE COMPLETE - {best_coin} ===")
        
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
                send_telegram("WARNING: Trading loop appears inactive for 5 minutes")
                logger.warning("Trading loop appears stuck - no activity for 5 minutes")
            
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
    global position

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
                equity_df.to_csv("..\\trading\\equity_live_final.csv", index=False)
            
            if trades_log:
                trades_df = pd.DataFrame(list(trades_log))
                trades_df.to_csv("..\\trading\\trades_live_final.csv", index=False)
                
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

def trading_loop_with_timeout():
    """Wrapper for trading loop with timeout protection"""
    import concurrent.futures
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(trading_loop)
        try:
            # Timeout after 50 seconds to ensure 60-second cycle
            future.result(timeout=50)
        except concurrent.futures.TimeoutError:
            logger.error("Trading loop timeout - took longer than 50 seconds")
            send_telegram("⚠️ Trading loop timeout detected - check system")
            # Don't kill the thread, let it complete but log the issue
        except Exception as e:
            logger.error(f"Trading loop executor error: {e}")

# --- MAIN EXECUTION ---
if __name__ == "__main__":
    # Setup signal handlers
    signal.signal(signal.SIGINT, graceful_shutdown)
    signal.signal(signal.SIGTERM, graceful_shutdown)
    
    logger.info("=" * 60)
    logger.info("ARS-3 ENHANCED MULTI-STRATEGY TRADING BOT v2.1")
    logger.info("=" * 60)
    
    try:
        # Send startup notification
        mode = "SIMULATION" if SIMULATION_MODE else "LIVE TRADING"
        send_telegram(f"BOT STARTING UP\nMode: {mode}\nInitial Balance: ${INITIAL_CAPITAL:,.2f}\nCoins to scan: {', '.join(COINS)}\nRegime Detection: ENABLED")
        
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
            # QUAN TRỌNG: Set best_coin từ position đã có
            best_coin = position.get('symbol')
            if best_coin:
                logger.info(f"Setting best_coin from existing position: {best_coin}")
        
        # Set current balance
        current_balance = get_current_balance()
        logger.info(f"Initial balance: ${current_balance:,.2f}")
        
        # LOẠI BỎ VIỆC GÁN CỨNG best_coin
        # Để select_best_coin tự chọn trong mỗi cycle
        if not best_coin:  # Chỉ log, không gán cứng
            logger.info("No trading symbol pre-selected, will be chosen dynamically each cycle")
        else:
            logger.info(f"Current trading symbol: {best_coin}")
        
        # Initialize regime detector status
        logger.info(f"Regime detector initialized with config: {REGIME_DETECTION_CONFIG}")
        logger.info(f"Available coins for scanning: {COINS}")
        
        # KIỂM TRA CẤU HÌNH QUAN TRỌNG
        logger.info("=== CONFIGURATION CHECK ===")
        logger.info(f"Regime thresholds - Trend ON: {REGIME_DETECTION_CONFIG.get('trend_on', 0.55)}, Range ON: {REGIME_DETECTION_CONFIG.get('range_on', 0.55)}")
        if REGIME_DETECTION_CONFIG.get('trend_on', 0.55) > 0.5:
            logger.warning("⚠️  Trend threshold might be too high for signal generation!")
        if REGIME_DETECTION_CONFIG.get('range_on', 0.55) > 0.5:
            logger.warning("⚠️  Range threshold might be too high for signal generation!")
        
        # Start system monitor
        monitor_thread = threading.Thread(target=system_monitor, daemon=True)
        monitor_thread.start()
        logger.info("System monitor started")
        
        # CHẠY INITIAL COIN SELECTION
        logger.info("=== INITIAL COIN SELECTION ===")
        initial_best_coin = select_best_coin(COINS)
        if initial_best_coin:
            best_coin = initial_best_coin
            logger.info(f"Initial best coin selected: {best_coin}")
        else:
            logger.warning("No optimal coin found in initial selection")
            if COINS:
                best_coin = COINS[0]  # Fallback
                logger.info(f"Using fallback coin: {best_coin}")
            else:
                logger.critical("No coins configured for trading!")
                exit(1)
        
        # Run initial cycle với coin đã chọn
        logger.info("Running initial trading cycle...")
        try:
            trading_loop()
        except Exception as e:
            logger.error(f"Initial trading cycle failed: {e}")
        
        # Main trading loop with improved coin selection logic
        logger.info("Trading scheduler initialized. Loop will run every 60 seconds.")
        send_telegram(f"BOT FULLY OPERATIONAL\nMode: {mode}\nCoins: {', '.join(COINS)}\nCurrent symbol: {best_coin}")
        logger.info("=" * 60)
        
        last_loop_time = time.time()
        consecutive_errors = 0
        coin_selection_counter = 0  # Đếm chu kỳ để re-select coin
        COIN_RESELECTION_INTERVAL = 8  # Re-select mỗi 12 chu kỳ (12 phút nếu 1m timeframe)
        
        while not shutdown_event.is_set():
            start_time = time.time()
            
            try:
                # LOGIC CHỌN COIN CẢI TIẾN
                coin_selection_counter += 1
                should_reselect_coin = (
                    coin_selection_counter % COIN_RESELECTION_INTERVAL == 0 or  # Định kỳ
                    best_coin is None or  # Không có coin
                    consecutive_errors >= 2  # Nhiều lỗi liên tiếp
                )
                
                if should_reselect_coin and not position:  # Chỉ đổi coin khi không có position
                    logger.info("=== COIN RESELECTION TRIGGERED ===")
                    logger.info(f"Reason: Counter={coin_selection_counter}, Errors={consecutive_errors}, No position={not bool(position)}")
                    
                    new_best_coin = select_best_coin(COINS)
                    if new_best_coin:
                        if new_best_coin != best_coin:
                            logger.info(f"🔄 Switching from {best_coin} to {new_best_coin}")
                            send_telegram(f"🔄 Coin switch: {best_coin} → {new_best_coin}")
                        best_coin = new_best_coin
                        consecutive_errors = 0  # Reset error counter on successful selection
                    else:
                        logger.warning("No optimal coin found, keeping current selection")
                        if not best_coin and COINS:  # Fallback nếu hoàn toàn không có coin
                            best_coin = COINS[0]
                            logger.info(f"Emergency fallback to: {best_coin}")
                
                elif position:
                    # Nếu có position, ưu tiên coin của position đó
                    position_coin = position.get('symbol')
                    if position_coin and position_coin != best_coin:
                        logger.info(f"Prioritizing position coin: {position_coin}")
                        best_coin = position_coin
                
                # Đảm bảo broker sử dụng đúng symbol
                if broker and best_coin:
                    broker.symbol = best_coin
                
                # CHẠY TRADING LOOP
                if best_coin:
                    logger.debug(f"Running trading cycle for: {best_coin}")
                    trading_loop_with_timeout()
                    consecutive_errors = 0  # Reset on success
                else:
                    logger.error("No trading symbol available, skipping cycle")
                    consecutive_errors += 1
                    
            except Exception as e:
                logger.error(f"Trading loop error: {e}")
                send_telegram(f"Trading loop error: {str(e)[:150]}")
                consecutive_errors += 1
                
                # Emergency stop if too many consecutive errors
                if consecutive_errors >= 5:
                    logger.critical("Too many consecutive errors - creating emergency stop")
                    send_telegram("🚨 EMERGENCY: Multiple errors detected - stopping bot")
                    with open(EMERGENCY_STOP_FILE, 'w') as f:
                        f.write(f"Multiple errors at {datetime.now()}")
                    break
            
            # Ensure minimum time between loops
            elapsed = time.time() - start_time
            
            # Track if we're consistently slow
            if elapsed > 50:
                logger.warning(f"Trading loop took {elapsed:.1f}s - longer than expected")
                
            # Always wait for next minute mark
            next_run = last_loop_time + 60
            current_time = time.time()
            if current_time < next_run:
                sleep_time = next_run - current_time
                time.sleep(sleep_time)
            
            last_loop_time = time.time()
            
            # LOG TRẠNG THÁI MỖI 10 CHU KỲ
            if coin_selection_counter % 10 == 0:
                logger.info(f"=== STATUS UPDATE (Cycle {coin_selection_counter}) ===")
                logger.info(f"Current coin: {best_coin}")
                logger.info(f"Position: {'Yes' if position else 'No'}")
                logger.info(f"Balance: ${get_current_balance():.2f}")
                logger.info(f"Daily P&L: ${daily_pnl:.2f}")
                logger.info(f"Consecutive errors: {consecutive_errors}")
            
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received")
        graceful_shutdown(signal.SIGINT, None)
    except Exception as e:
        logger.critical(f"Fatal error in main execution: {e}", exc_info=True)
        send_telegram(f"FATAL ERROR: {e}")
        sys.exit(1)