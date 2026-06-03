import os
from datetime import date, timedelta
from dotenv import load_dotenv
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import GetOptionContractsRequest
from alpaca.trading.enums import AssetStatus, ContractType
from alpaca.data.historical.option import OptionHistoricalDataClient
from alpaca.data.requests import OptionLatestQuoteRequest

load_dotenv()

API_KEY = os.getenv("ALPACA_API_KEY")
SECRET_KEY = os.getenv("ALPACA_SECRET_KEY")

trading_client = TradingClient(API_KEY, SECRET_KEY, paper=True)
option_data_client = OptionHistoricalDataClient(API_KEY, SECRET_KEY)

# Assume SPY is around $725 — pull contracts within $20 of that
spot = 725
today = date.today()
exp_start = today + timedelta(days=7)
exp_end = today + timedelta(days=14)

request = GetOptionContractsRequest(
    underlying_symbols=["SPY"],
    status=AssetStatus.ACTIVE,
    expiration_date_gte=exp_start,
    expiration_date_lte=exp_end,
    type=ContractType.CALL,
    strike_price_gte=str(spot - 20),
    strike_price_lte=str(spot + 20),
    limit=20,
)

contracts = trading_client.get_option_contracts(request).option_contracts
symbols = [c.symbol for c in contracts]

# Pull live quotes for all of them in one call
quote_req = OptionLatestQuoteRequest(symbol_or_symbols=symbols)
quotes = option_data_client.get_option_latest_quote(quote_req)

print(f"SPY call options expiring {exp_start} to {exp_end}, strikes near ${spot}")
print()
print(f"{'Symbol':<25} {'Strike':>7} {'Expires':<12} {'Bid':>7} {'Ask':>7} {'Mid':>7}")
print("-" * 72)

for c in contracts:
    q = quotes.get(c.symbol)
    if q:
        mid = round((q.bid_price + q.ask_price) / 2, 2)
        print(f"{c.symbol:<25} {c.strike_price:>7} {str(c.expiration_date):<12} "
              f"{q.bid_price:>7} {q.ask_price:>7} {mid:>7}")
    else:
        print(f"{c.symbol:<25} {c.strike_price:>7} {str(c.expiration_date):<12} (no quote)")