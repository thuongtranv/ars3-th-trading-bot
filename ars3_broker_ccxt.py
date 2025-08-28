import ccxt
import os
from dotenv import load_dotenv
import yaml
import time
import random
import logging
import threading
from typing import Optional, Dict, List

logger = logging.getLogger(__name__)

class Broker:
    def __init__(self, config_path="ars3_config.yaml"):
        load_dotenv()
        
        # Load configuration
        try:
            with open(config_path, "r") as f:
                self.config = yaml.safe_load(f)
        except Exception as e:
            logger.warning(f"Error loading config: {e}. Using default config.")
            self.config = {}

        # Exchange setup
        self.exchange_id = self.config.get("exchange", "binance")
        self.symbol = self.config.get("coins", ["BTC/USDT"])[0]
        self.timeframe = self.config.get("timeframe", "1h")
        self.simulation_mode = self.config.get("simulation_mode", True)
        self.testnet = self.config.get("testnet", True)
        self.is_futures = self.config.get("futures", True)
        
        # Connection state
        self.exchange = None
        self.connected = False
        self.last_heartbeat = None
        self.connection_lock = threading.Lock()
        
        # Rate limiting
        self.last_request_time = 0
        self.min_request_interval = 1.0  # Minimum 1 second between requests
        
        # Initialize exchange
        self._initialize_exchange()
        
        # Start heartbeat monitoring
        if not self.simulation_mode and self.exchange:
            self._start_heartbeat_monitoring()

    def _initialize_exchange(self):
        """Initialize exchange connection with proper error handling"""
        try:
            # Get API credentials
            api_key = os.getenv("BINANCE_API_KEY")
            api_secret = os.getenv("BINANCE_API_SECRET")
            
            if not api_key or not api_secret:
                if not self.simulation_mode:
                    logger.warning("No API credentials found. Forcing simulation mode.")
                    self.simulation_mode = True
                return
            
            if self.simulation_mode:
                logger.info("Running in simulation mode")
                return
            
            # Initialize exchange with proper configuration
            exchange_class = getattr(ccxt, self.exchange_id)
            exchange_config = {
                "apiKey": api_key,
                "secret": api_secret,
                "enableRateLimit": True,
                "timeout": 30000,
                "options": {}
            }
            
            # Set market type before initialization
            if self.is_futures:
                exchange_config["options"]["defaultType"] = "future"
            
            # Configure testnet
            if self.testnet:
                if self.exchange_id == "binance":
                    exchange_config["sandbox"] = True
                    exchange_config["urls"] = {
                        'api': {
                            'public': 'https://testnet.binance.vision/api/v3',
                            'private': 'https://testnet.binance.vision/api/v3',
                        }
                    }
                elif self.exchange_id == "bybit":
                    exchange_config["sandbox"] = True
                    exchange_config["urls"] = {
                        'api': 'https://api-testnet.bybit.com'
                    }
            
            self.exchange = exchange_class(exchange_config)
            
            # Test connection
            self._test_connection()
            
        except Exception as e:
            logger.error(f"Exchange initialization failed: {e}")
            logger.info("Falling back to SIMULATION MODE")
            self.simulation_mode = True
            self.exchange = None
            self.connected = False

    def _test_connection(self):
        """Test exchange connection with retry logic"""
        max_retries = 3
        for attempt in range(max_retries):
            try:
                # Load markets
                self.exchange.load_markets()
                
                # Test with a simple API call
                self.exchange.fetch_ticker(self.symbol)
                
                self.connected = True
                self.last_heartbeat = time.time()
                
                mode = "TESTNET" if self.testnet else "LIVE"
                logger.info(f"✅ Exchange connected: {self.exchange_id} ({mode})")
                return True
                
            except ccxt.NetworkError as e:
                logger.warning(f"Network error on attempt {attempt + 1}: {e}")
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
                    continue
                    
            except ccxt.ExchangeError as e:
                logger.error(f"Exchange error: {e}")
                break
                
            except Exception as e:
                logger.error(f"Unexpected connection error: {e}")
                break
        
        # If all attempts failed
        self.connected = False
        raise Exception("Failed to establish exchange connection after retries")

    def _start_heartbeat_monitoring(self):
        """Start heartbeat monitoring in separate thread"""
        def heartbeat_worker():
            while not self.simulation_mode and self.exchange:
                try:
                    time.sleep(60)  # Check every minute
                    if time.time() - self.last_heartbeat > 300:  # 5 minutes timeout
                        logger.warning("Connection heartbeat timeout, reconnecting...")
                        self._reconnect()
                except Exception as e:
                    logger.error(f"Heartbeat monitoring error: {e}")
        
        heartbeat_thread = threading.Thread(target=heartbeat_worker, daemon=True)
        heartbeat_thread.start()

    def _reconnect(self):
        """Reconnect to exchange"""
        with self.connection_lock:
            try:
                logger.info("Attempting to reconnect...")
                self.connected = False
                self._initialize_exchange()
                if self.connected:
                    logger.info("Reconnection successful")
                else:
                    logger.error("Reconnection failed")
            except Exception as e:
                logger.error(f"Reconnection error: {e}")

    def _rate_limit_check(self):
        """Implement client-side rate limiting"""
        current_time = time.time()
        time_since_last = current_time - self.last_request_time
        if time_since_last < self.min_request_interval:
            sleep_time = self.min_request_interval - time_since_last
            time.sleep(sleep_time)
        self.last_request_time = time.time()

    def fetch_ohlcv(self, limit=200, symbol=None) -> List:
        """Fetch OHLCV data with comprehensive error handling"""
        if not symbol:
            symbol = self.symbol
            
        if self.simulation_mode or not self.exchange:
            return self._generate_simulation_data(limit)
        
        self._rate_limit_check()
        
        max_retries = 3
        for attempt in range(max_retries):
            try:
                with self.connection_lock:
                    if not self.connected:
                        logger.warning("Exchange not connected, attempting reconnect...")
                        self._reconnect()
                        if not self.connected:
                            return self._generate_simulation_data(limit)
                    
                    data = self.exchange.fetch_ohlcv(symbol, timeframe=self.timeframe, limit=limit)
                    
                    if not data:
                        logger.warning("Empty OHLCV data received")
                        return []
                    
                    # Validate data structure
                    validated_data = []
                    for i, candle in enumerate(data):
                        if len(candle) != 6:
                            continue
                        
                        timestamp, open_p, high, low, close, volume = candle
                        
                        # Basic validation
                        if any(x is None or (isinstance(x, (int, float)) and x <= 0) 
                               for x in [open_p, high, low, close]):
                            continue
                        
                        if high < low or high < max(open_p, close) or low > min(open_p, close):
                            continue
                        
                        validated_data.append(candle)
                    
                    self.last_heartbeat = time.time()
                    logger.info(f"Fetched {len(validated_data)} valid candles for {symbol}")
                    return validated_data
                
            except ccxt.NetworkError as e:
                logger.warning(f"Network error on attempt {attempt + 1}: {e}")
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
                    continue
                logger.error("Max retries exceeded for network errors")
                
            except ccxt.ExchangeError as e:
                logger.error(f"Exchange error: {e}")
                self.connected = False
                
            except Exception as e:
                logger.error(f"Unexpected error fetching OHLCV: {e}")
        
        # Fallback to simulation data
        logger.warning("Falling back to simulation data")
        return self._generate_simulation_data(limit)

    def _generate_simulation_data(self, limit):
        """Generate realistic simulation data with proper volatility modeling"""
        try:
            data = []
            now = int(time.time() * 1000)
            
            # Get timeframe in milliseconds
            timeframe_ms = {
                '1m': 60 * 1000,
                '5m': 5 * 60 * 1000,
                '15m': 15 * 60 * 1000,
                '1h': 60 * 60 * 1000,
                '4h': 4 * 60 * 60 * 1000,
                '1d': 24 * 60 * 60 * 1000,
            }.get(self.timeframe, 60 * 60 * 1000)
            
            # Starting price based on symbol
            if 'BTC' in self.symbol:
                price = 45000 + random.uniform(-5000, 5000)
            elif 'ETH' in self.symbol:
                price = 2500 + random.uniform(-500, 500)
            else:
                price = 100 + random.uniform(-20, 20)
            
            for i in range(limit):
                timestamp = now - (limit - i) * timeframe_ms
                
                # Generate realistic price movement with volatility clustering
                volatility = 0.01 + 0.005 * random.random()  # Base volatility + random component
                change_pct = random.gauss(0, volatility)
                new_price = price * (1 + change_pct)
                
                # Ensure positive prices
                if new_price <= 0:
                    new_price = price * 0.99
                
                open_p = price
                close_p = new_price
                
                # Generate high/low with realistic spreads
                spread = abs(open_p - close_p) * 0.5
                high_p = max(open_p, close_p) + random.uniform(0, spread)
                low_p = min(open_p, close_p) - random.uniform(0, spread)
                
                # Ensure OHLC consistency
                high_p = max(high_p, open_p, close_p)
                low_p = min(low_p, open_p, close_p)
                
                # Realistic volume with correlation to price movement
                base_volume = 1000
                volatility_factor = abs(change_pct) * 10  # Higher volume on big moves
                volume = base_volume * (0.5 + random.uniform(0.5, 1.5) + volatility_factor)
                
                data.append([int(timestamp), float(open_p), float(high_p), float(low_p), float(close_p), float(volume)])
                price = new_price
            
            logger.info(f"Generated {len(data)} simulation candles for {self.symbol}")
            return data
            
        except Exception as e:
            logger.error(f"Error generating simulation data: {e}")
            return []

    def get_balance(self) -> Dict:
        """Get account balance with error handling and validation"""
        if self.simulation_mode or not self.exchange:
            capital = self.config.get('initial_capital', 10000)
            return {
                "free": {"USDT": capital}, 
                "total": {"USDT": capital},
                "used": {"USDT": 0}
            }
        
        self._rate_limit_check()
        
        max_retries = 3
        for attempt in range(max_retries):
            try:
                with self.connection_lock:
                    if not self.connected:
                        self._reconnect()
                        if not self.connected:
                            break
                    
                    balance = self.exchange.fetch_balance()
                    self.last_heartbeat = time.time()
                    
                    # Validate balance structure
                    if not isinstance(balance, dict):
                        logger.error("Invalid balance format received")
                        continue
                    
                    logger.info("Successfully fetched account balance")
                    return balance
                    
            except ccxt.NetworkError as e:
                logger.warning(f"Network error fetching balance (attempt {attempt + 1}): {e}")
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
                    continue
                    
            except ccxt.ExchangeError as e:
                logger.error(f"Exchange error fetching balance: {e}")
                self.connected = False
                break
                
            except Exception as e:
                logger.error(f"Unexpected error fetching balance: {e}")
                break
        
        # Return default balance on error
        logger.warning("Using default balance due to API errors")
        return {"free": {"USDT": 0}, "total": {"USDT": 0}, "used": {"USDT": 0}}

    def execute_order(self, symbol: str, side: str, amount: float, order_type: str = 'market', price: Optional[float] = None) -> Optional[Dict]:
        """Execute order with comprehensive error handling and validation"""
        logger.info(f"Attempting to execute order: {side.upper()} {amount:.6f} {symbol}")
        
        # Input validation
        if amount <= 0:
            logger.error("Order amount must be positive")
            return None
        
        if side not in ['buy', 'sell']:
            logger.error(f"Invalid order side: {side}")
            return None
        
        if order_type == 'limit' and price is None:
            logger.error("Price required for limit orders")
            return None
        
        # Simulation mode
        if self.simulation_mode or not self.exchange:
            current_price = price if price else self._get_current_price(symbol)
            simulated_order = {
                "id": f"sim_{int(time.time())}_{random.randint(1000, 9999)}",
                "symbol": symbol,
                "side": side,
                "amount": amount,
                "type": order_type,
                "status": "closed",
                "filled": amount,
                "remaining": 0,
                "price": current_price,
                "cost": amount * current_price,
                "timestamp": int(time.time() * 1000),
                "datetime": time.strftime('%Y-%m-%dT%H:%M:%S.%fZ'),
                "fee": {"cost": amount * current_price * 0.001, "currency": "USDT"}
            }
            logger.info(f"--- [SIMULATION] Executed {side.upper()} {amount:.6f} {symbol} at {current_price:.4f} ---")
            return simulated_order
        
        self._rate_limit_check()
        
        # Real order execution with retry logic
        max_retries = 3
        for attempt in range(max_retries):
            try:
                with self.connection_lock:
                    if not self.connected:
                        self._reconnect()
                        if not self.connected:
                            logger.error("Cannot execute order - exchange not connected")
                            return None
                    
                    # Execute order
                    if order_type == 'market':
                        order = self.exchange.create_market_order(symbol, side, amount)
                    elif order_type == 'limit':
                        order = self.exchange.create_limit_order(symbol, side, amount, price)
                    else:
                        logger.error(f"Unsupported order type: {order_type}")
                        return None
                    
                    self.last_heartbeat = time.time()
                    logger.info(f"+++ [LIVE] Order executed successfully: {order.get('id', 'Unknown')} +++")
                    return order
                
            except ccxt.InsufficientFunds as e:
                logger.error(f"Insufficient funds for order: {e}")
                return None
                
            except ccxt.InvalidOrder as e:
                logger.error(f"Invalid order parameters: {e}")
                return None
                
            except ccxt.NetworkError as e:
                logger.warning(f"Network error on order attempt {attempt + 1}: {e}")
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
                    continue
                logger.error("Max retries exceeded for order execution")
                
            except ccxt.ExchangeError as e:
                logger.error(f"Exchange error during order execution: {e}")
                self.connected = False
                
            except Exception as e:
                logger.error(f"Unexpected error during order execution: {e}")
        
        return None

    def _get_current_price(self, symbol: str) -> float:
        """Get current price for simulation or validation"""
        try:
            if self.simulation_mode or not self.exchange:
                # Return a realistic price based on symbol
                if 'BTC' in symbol:
                    return 45000 + random.uniform(-1000, 1000)
                elif 'ETH' in symbol:
                    return 2500 + random.uniform(-100, 100)
                else:
                    return 100 + random.uniform(-10, 10)
            
            with self.connection_lock:
                if not self.connected:
                    self._reconnect()
                    if not self.connected:
                        return 50000  # Fallback
                
                ticker = self.exchange.fetch_ticker(symbol)
                self.last_heartbeat = time.time()
                return ticker['last'] or ticker['close']
            
        except Exception as e:
            logger.warning(f"Could not fetch current price for {symbol}: {e}")
            return 50000 if 'BTC' in symbol else 2500 if 'ETH' in symbol else 100

    def get_order_book(self, symbol: str, limit: int = 20) -> Optional[Dict]:
        """Get order book data with error handling"""
        if self.simulation_mode or not self.exchange:
            logger.info("Order book not available in simulation mode")
            return None
        
        try:
            self._rate_limit_check()
            with self.connection_lock:
                if not self.connected:
                    return None
                
                order_book = self.exchange.fetch_order_book(symbol, limit)
                self.last_heartbeat = time.time()
                return order_book
        except Exception as e:
            logger.error(f"Error fetching order book for {symbol}: {e}")
            return None

    def get_open_orders(self, symbol: Optional[str] = None) -> List:
        """Get open orders with error handling"""
        if self.simulation_mode or not self.exchange:
            return []
        
        try:
            self._rate_limit_check()
            with self.connection_lock:
                if not self.connected:
                    return []
                
                orders = self.exchange.fetch_open_orders(symbol)
                self.last_heartbeat = time.time()
                return orders
        except Exception as e:
            logger.error(f"Error fetching open orders: {e}")
            return []

    def cancel_order(self, order_id: str, symbol: str) -> Optional[Dict]:
        """Cancel order with error handling"""
        if self.simulation_mode or not self.exchange:
            logger.info(f"[SIMULATION] Order {order_id} cancelled")
            return {"status": "cancelled", "id": order_id}
        
        try:
            self._rate_limit_check()
            with self.connection_lock:
                if not self.connected:
                    return None
                
                result = self.exchange.cancel_order(order_id, symbol)
                self.last_heartbeat = time.time()
                logger.info(f"Order {order_id} cancelled successfully")
                return result
        except Exception as e:
            logger.error(f"Error cancelling order {order_id}: {e}")
            return None

    def get_position_info(self, symbol: Optional[str] = None) -> List:
        """Get position information (for futures trading)"""
        if self.simulation_mode or not self.exchange or not self.is_futures:
            return []
        
        try:
            self._rate_limit_check()
            with self.connection_lock:
                if not self.connected:
                    return []
                
                positions = self.exchange.fetch_positions(symbol)
                self.last_heartbeat = time.time()
                return positions
        except Exception as e:
            logger.error(f"Error fetching positions: {e}")
            return []

    def test_connection(self) -> Dict:
        """Test exchange connection and return detailed status"""
        if self.simulation_mode:
            return {
                "status": "simulation", 
                "connected": True, 
                "message": "Simulation mode",
                "exchange": self.exchange_id,
                "symbol": self.symbol
            }
        
        if not self.exchange:
            return {
                "status": "error", 
                "connected": False, 
                "message": "Exchange not initialized",
                "exchange": self.exchange_id
            }
        
        try:
            with self.connection_lock:
                # Test with multiple API calls
                status = self.exchange.fetch_status()
                ticker = self.exchange.fetch_ticker(self.symbol)
                balance = self.exchange.fetch_balance()
                
                self.connected = True
                self.last_heartbeat = time.time()
                
                return {
                    "status": "connected", 
                    "connected": True, 
                    "message": "All API calls successful",
                    "exchange": self.exchange_id,
                    "symbol": self.symbol,
                    "testnet": self.testnet,
                    "last_price": ticker.get('last', 'N/A'),
                    "balance_usdt": balance.get('free', {}).get('USDT', 'N/A')
                }
        except Exception as e:
            self.connected = False
            return {
                "status": "error", 
                "connected": False, 
                "message": str(e),
                "exchange": self.exchange_id
            }

    def cleanup(self):
        """Cleanup resources"""
        try:
            if self.exchange and hasattr(self.exchange, 'close'):
                self.exchange.close()
            logger.info("Broker cleanup completed")
        except Exception as e:
            logger.error(f"Error during broker cleanup: {e}")

    def __del__(self):
        """Destructor to ensure cleanup"""
        self.cleanup()