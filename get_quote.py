import os
from dotenv import load_dotenv
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockLatestQuoteRequest

load_dotenv()

API_KEY = os.getenv("ALPACA_API_KEY")
SECRET_KEY = os.getenv("ALPACA_SECRET_KEY")

# Data client (separate from the trading client)
data_client = StockHistoricalDataClient(API_KEY, SECRET_KEY)

# Ask for the latest quote on SPY (the S&P 500 ETF)
symbol = "SPY"
request = StockLatestQuoteRequest(symbol_or_symbols=symbol)
quote = data_client.get_stock_latest_quote(request)[symbol]

print(f"Symbol: {symbol}")
print(f"Bid: ${quote.bid_price} (size: {quote.bid_size})")
print(f"Ask: ${quote.ask_price} (size: {quote.ask_size})")
print(f"Spread: ${round(quote.ask_price - quote.bid_price, 4)}")
print(f"Timestamp: {quote.timestamp}")
