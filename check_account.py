import os
from dotenv import load_dotenv
from alpaca.trading.client import TradingClient

load_dotenv()

API_KEY = os.getenv("ALPACA_API_KEY")
SECRET_KEY = os.getenv("ALPACA_SECRET_KEY")

client = TradingClient(API_KEY, SECRET_KEY, paper=True)

account = client.get_account()

print("Connected to Alpaca Paper Trading!")
print(f"Account status: {account.status}")
print(f"Buying power: ${account.buying_power}")
print(f"Cash: ${account.cash}")
print(f"Portfolio value: ${account.portfolio_value}")


