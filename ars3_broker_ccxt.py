import ccxt
import os
from dotenv import load_dotenv
import yaml
import time
import random
import logging

logger = logging.getLogger(__name__)

class Broker:
    def __init__(self, config_path="ars3_config.yaml"):
        load_dotenv()
        
        try:
            with open(config_path, "r") as f:
                self.config = yaml.safe_load(f)
        except Exception as e:
            logger.warning(f"Error loading config: {e}. Using default config.")
            self.config = {}

        # Exchange setup
        exchange_id = self.config.get("exchange", "binance")
        self.symbol = self.config.get("coins", ["BTC/USDT"])[0]
        self.timeframe = self.config.get("timeframe", "1h")
        self.simulation_mode = self.config.get("simulation_mode", True)
        self.testnet = self.config.get("testnet", True)
        self.is_futures = self.config.get("futures", True)
        
        # FIXED: Better exchange initialization with proper error handling
        self.exchange = None
        try:
            # Get API credentials
            api_key = os.getenv("BINANCE_API_KEY")
            api_secret = os.getenv("BINANCE_API_SECRET")
            
            if not api_key or not api_secret:
                if not self.simulation_mode:
                    logger.warning("No API credentials found. Forcing simulation mode.")
                    self.simulation_mode = True
            
            if not self.simulation_mode:
                # Initialize exchange
                self.exchange = getattr(ccxt, exchange_id)({
                    "apiKey": api_key,
                    "secret": api_secret,
                    "enableRateLimit": True,
                    "timeout": 30000,  # 30 second timeout
                })
                
                # FIXED: Set market type before sandbox mode
                if self.is_futures:
                    self.exchange.options['defaultType'] = 'future'
                    
                # FIXED: Proper testnet setup
                if self.testnet:
                    if hasattr(self.exchange, 'set_sandbox_mode'):
                        self.exchange.set_sandbox_mode(True)
                        logger.info("Testnet mode enabled via sandbox")
                    elif 'test' in self.exchange.urls:
                        # Fallback for exchanges without sandbox method
                        self.exchange.urls['api'] = self.exchange.urls['test']
                        logger.info("Testnet mode enabled via URL override")
                    else:
                        logger.warning("Testnet requested but not supported. Using live mode!")
                
                # FIXED: Test connection with retry logic
                max_retries = 3
                for attempt in range(max_retries):
                    try:
                        self.exchange.load_markets()
                        # Test a simple API call
                        self.exchange.fetch_ticker(self.symbol)
                        logger.info(f"✅ Exchange connected: {exchange_id} ({'TESTNET' if self.testnet else 'LIVE'})")
                        break
                    except Exception as e:
                        logger.warning(f"Connection attempt {attempt + 1} failed: {e}")
                        if attempt == max_retries - 1:
                            raise e
                        time.sleep(2 ** attempt)  # Exponential backoff
                        
        except Exception as e:
            logger.error(f"❌ Exchange connection failed: {e}")
            logger.info("🔄 Falling back to SIMULATION MODE")
            self.simulation_mode = True
            self.exchange = None

    def fetch_ohlcv(self, limit=200):
        """Fetch OHLCV data with comprehensive error handling."""
        if self.simulation_mode or not self.exchange:
            return self._generate_simulation_data(limit)
        
        max_retries = 3
        for attempt in range(max_retries):
            try:
                # FIXED: Add data validation
                data = self.exchange.fetch_ohlcv(self.symbol, timeframe=self.timeframe, limit=limit)
                
                if not data:
                    logger.warning("Empty OHLCV data received")
                    return []
                
                # Validate data structure
                for i, candle in enumerate(data):
                    if len(candle) != 6:
                        logger.warning(f"Invalid candle format at index {i}: {candle}")
                        continue
                    
                    timestamp, open_p, high, low, close, volume = candle
                    
                    # Basic validation
                    if any(x is None or (isinstance(x, (int, float)) and x <= 0) for x in [open_p, high, low, close]):
                        logger.warning(f"Invalid price data at index {i}: {candle}")
                        continue
                    
                    if high < low or high < max(open_p, close) or low > min(open_p, close):
                        logger.warning(f"Inconsistent OHLC data at index {i}: {candle}")
                        continue
                
                logger.info(f"Fetched {len(data)} candles for {self.symbol}")
                return data
                
            except ccxt.NetworkError as e:
                logger.warning(f"Network error on attempt {attempt + 1}: {e}")
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
                    continue
                logger.error("Max retries exceeded for network errors")
                return []
                
            except ccxt.ExchangeError as e:
                logger.error(f"Exchange error: {e}")
                return []
                
            except Exception as e:
                logger.error(f"Unexpected error fetching OHLCV: {e}")
                return []
        
        return []

    def _generate_simulation_data(self, limit):
        """Generate realistic simulation data."""
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
            }.get(self.timeframe, 60 * 60 * 1000)  # Default to 1h
            
            # Starting price based on symbol
            if 'BTC' in self.symbol:
                price = 45000 + random.uniform(-5000, 5000)
            elif 'ETH' in self.symbol:
                price = 2500 + random.uniform(-500, 500)
            else:
                price = 100 + random.uniform(-20, 20)
            
            for i in range(limit):
                timestamp = now - (limit - i) * timeframe_ms
                
                # Generate realistic price movement
                change_pct = random.gauss(0, 0.01)  # Normal distribution, 1% std dev
                new_price = price * (1 + change_pct)
                
                open_p = price
                close_p = new_price
                
                # Generate high/low with realistic spreads
                spread = abs(open_p - close_p)
                high_p = max(open_p, close_p) + random.uniform(0, spread * 0.5)
                low_p = min(open_p, close_p) - random.uniform(0, spread * 0.5)
                
                # Ensure high >= low
                if high_p < low_p:
                    high_p, low_p = low_p, high_p
                
                # Realistic volume
                base_volume = 1000
                volume = base_volume * random.uniform(0.5, 2.0)
                
                data.append([timestamp, open_p, high_p, low_p, close_p, volume])
                price = new_price
            
            logger.info(f"Generated {len(data)} simulation candles for {self.symbol}")
            return data
            
        except Exception as e:
            logger.error(f"Error generating simulation data: {e}")
            return []

    def get_balance(self):
        """Get account balance with error handling."""
        if self.simulation_mode or not self.exchange:
            capital = self.config.get('initial_capital', 10000)
            return {
                "free": {"USDT": capital}, 
                "total": {"USDT": capital},
                "used": {"USDT": 0}
            }
        
        max_retries = 3
        for attempt in range(max_retries):
            try:
                balance = self.exchange.fetch_balance()
                logger.info("Successfully fetched account balance")
                return balance
                
            except ccxt.NetworkError as e:
                logger.warning(f"Network error fetching balance (attempt {attempt + 1}): {e}")
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
                    continue
                    
            except ccxt.ExchangeError as e:
                logger.error(f"Exchange error fetching balance: {e}")
                break
                
            except Exception as e:
                logger.error(f"Unexpected error fetching balance: {e}")
                break
        
        # Return default balance on error
        logger.warning("Using default balance due to API errors")
        return {"free": {"USDT": 0}, "total": {"USDT": 0}, "used": {"USDT": 0}}

    def execute_order(self, symbol, side, amount, order_type='market', price=None):
        """Execute order with comprehensive error handling and validation."""
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
        
        if self.simulation_mode or not self.exchange:
            # FIXED: More realistic simulation response
            simulated_order = {
                "id": f"sim_{int(time.time())}_{random.randint(1000, 9999)}",
                "symbol": symbol,
                "side": side,
                "amount": amount,
                "type": order_type,
                "status": "closed",
                "filled": amount,
                "remaining": 0,
                "price": price if price else self._get_current_price(symbol),
                "cost": amount * (price if price else self._get_current_price(symbol)),
                "timestamp": int(time.time() * 1000),
                "datetime": time.strftime('%Y-%m-%dT%H:%M:%S.%fZ'),
                "fee": {"cost": 0, "currency": "USDT"}
            }
            logger.info(f"--- [SIMULATION] Executed {side.upper()} {amount:.6f} {symbol} at {simulated_order['price']:.4f} ---")
            return simulated_order
        
        # Real order execution with retry logic
        max_retries = 3
        for attempt in range(max_retries):
            try:
                if order_type == 'market':
                    order = self.exchange.create_market_order(symbol, side, amount)
                elif order_type == 'limit':
                    order = self.exchange.create_limit_order(symbol, side, amount, price)
                else:
                    logger.error(f"Unsupported order type: {order_type}")
                    return None
                
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
                return None
                
            except ccxt.ExchangeError as e:
                logger.error(f"Exchange error during order execution: {e}")
                return None
                
            except Exception as e:
                logger.error(f"Unexpected error during order execution: {e}")
                return None
        
        return None

    def _get_current_price(self, symbol):
        """Get current price for simulation or validation."""
        try:
            if self.simulation_mode or not self.exchange:
                # Return a realistic price based on symbol
                if 'BTC' in symbol:
                    return 45000 + random.uniform(-1000, 1000)
                elif 'ETH' in symbol:
                    return 2500 + random.uniform(-100, 100)
                else:
                    return 100 + random.uniform(-10, 10)
            
            ticker = self.exchange.fetch_ticker(symbol)
            return ticker['last'] or ticker['close']
            
        except Exception as e:
            logger.warning(f"Could not fetch current price for {symbol}: {e}")
            return 50000 if 'BTC' in symbol else 2500 if 'ETH' in symbol else 100

    def get_order_book(self, symbol, limit=20):
        """Get order book data with error handling."""
        if self.simulation_mode or not self.exchange:
            logger.info("Order book not available in simulation mode")
            return None
        
        try:
            order_book = self.exchange.fetch_order_book(symbol, limit)
            return order_book
        except Exception as e:
            logger.error(f"Error fetching order book for {symbol}: {e}")
            return None

    def get_open_orders(self, symbol=None):
        """Get open orders with error handling."""
        if self.simulation_mode or not self.exchange:
            return []
        
        try:
            orders = self.exchange.fetch_open_orders(symbol)
            return orders
        except Exception as e:
            logger.error(f"Error fetching open orders: {e}")
            return []

    def cancel_order(self, order_id, symbol):
        """Cancel order with error handling."""
        if self.simulation_mode or not self.exchange:
            logger.info(f"[SIMULATION] Order {order_id} cancelled")
            return True
        
        try:
            result = self.exchange.cancel_order(order_id, symbol)
            logger.info(f"Order {order_id} cancelled successfully")
            return result
        except Exception as e:
            logger.error(f"Error cancelling order {order_id}: {e}")
            return None

    def test_connection(self):
        """Test exchange connection and return status."""
        if self.simulation_mode:
            return {"status": "simulation", "connected": True, "message": "Simulation mode"}
        
        if not self.exchange:
            return {"status": "error", "connected": False, "message": "Exchange not initialized"}
        
        try:
            # Test with a simple API call
            self.exchange.fetch_status()
            return {"status": "connected", "connected": True, "message": "Exchange connection OK"}
        except Exception as e:
            return {"status": "error", "connected": False, "message": str(e)}