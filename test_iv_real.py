"""
Pull a real SPY option from Alpaca and compute its implied volatility.
"""
import os
from datetime import date, timedelta
from dotenv import load_dotenv
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import GetOptionContractsRequest
from alpaca.trading.enums import AssetStatus, ContractType
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.historical.option import OptionHistoricalDataClient
from alpaca.data.requests import StockLatestQuoteRequest, OptionLatestQuoteRequest

from implied_vol import implied_volatility

load_dotenv()
API_KEY = os.getenv("ALPACA_API_KEY")
SECRET_KEY = os.getenv("ALPACA_SECRET_KEY")

trading_client = TradingClient(API_KEY, SECRET_KEY, paper=True)
stock_data = StockHistoricalDataClient(API_KEY, SECRET_KEY)
option_data = OptionHistoricalDataClient(API_KEY, SECRET_KEY)

# 1. Get current SPY spot price
spy_quote = stock_data.get_stock_latest_quote(
    StockLatestQuoteRequest(symbol_or_symbols="SPY")
)["SPY"]
spot = (spy_quote.bid_price + spy_quote.ask_price) / 2
print(f"SPY spot (mid): ${spot:.2f}")

# 2. Find an at-the-money call ~30 days out
today = date.today()
target_exp_start = today + timedelta(days=25)
target_exp_end = today + timedelta(days=40)

contracts = trading_client.get_option_contracts(GetOptionContractsRequest(
    underlying_symbols=["SPY"],
    status=AssetStatus.ACTIVE,
    expiration_date_gte=target_exp_start,
    expiration_date_lte=target_exp_end,
    type=ContractType.CALL,
    strike_price_gte=str(int(spot)),
    strike_price_lte=str(int(spot) + 5),
    limit=5,
)).option_contracts

if not contracts:
    print("No contracts found in target range. Markets may be closed or strikes unavailable.")
    exit()

# 3. Pick the first one and pull its quote
contract = contracts[0]
quote = option_data.get_option_latest_quote(
    OptionLatestQuoteRequest(symbol_or_symbols=[contract.symbol])
)[contract.symbol]

market_mid = (quote.bid_price + quote.ask_price) / 2
strike = float(contract.strike_price)
T_days = (contract.expiration_date - today).days
T = T_days / 365.0

print()
print(f"Contract: {contract.symbol}")
print(f"  Strike: ${strike}")
print(f"  Expires: {contract.expiration_date} ({T_days} days)")
print(f"  Bid: ${quote.bid_price}  Ask: ${quote.ask_price}  Mid: ${market_mid:.2f}")

# 4. Compute implied vol
iv = implied_volatility(
    market_price=market_mid,
    S=spot, K=strike, T=T, r=0.045,
    option_type="call"
)

if iv is not None:
    print(f"  Implied Volatility: {iv*100:.2f}%")
else:
    print("  IV could not be computed (likely stale weekend data).")