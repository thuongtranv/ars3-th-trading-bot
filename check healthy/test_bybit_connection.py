# test_bybit_connection.py
import ccxt
import os
from dotenv import load_dotenv

load_dotenv()

api_key = os.getenv("BYBIT_API_KEY")
api_secret = os.getenv("BYBIT_API_SECRET")

if not api_key or not api_secret:
    raise ValueError("❌ Missing BYBIT_API_KEY or BYBIT_API_SECRET in .env file!")

exchange = ccxt.bybit({
    'apiKey': api_key,
    'secret': api_secret,
    'options': {
        'recvWindow': 30000,  # 30 giây
    }
})

# Bật testnet (sandbox mode)
exchange.set_sandbox_mode(True)

# Test connection
try:
    # server_time = exchange.public_get_time()  # Bybit server time
    # print("Bybit server time:", server_time)

    balance = exchange.fetch_balance()
    print("✅ Connected! Balance:", balance.get('USDT', {}))
    
    ticker = exchange.fetch_ticker('BTC/USDT')
    print(f"BTC Price: {ticker['last']}")
    
except Exception as e:
    print(f"❌ Error: {e}")
