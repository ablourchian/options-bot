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
from black_scholes import delta
import matplotlib.pyplot as plt

load_dotenv()
API_KEY = os.getenv("ALPACA_API_KEY")
SECRET_KEY = os.getenv("ALPACA_SECRET_KEY")

trading_client = TradingClient(API_KEY, SECRET_KEY, paper=True)
stock_data = StockHistoricalDataClient(API_KEY, SECRET_KEY)
option_data_client = OptionHistoricalDataClient(API_KEY, SECRET_KEY)

# 1. Get current SPY spot price
spy_quote = stock_data.get_stock_latest_quote(
    StockLatestQuoteRequest(symbol_or_symbols="SPY")
)["SPY"]
spot = (spy_quote.bid_price + spy_quote.ask_price) / 2
print(f"SPY spot (mid): ${spot:.2f}")

# 2. Fetch the option chain ~30 days out
today = date.today()
target_exp_start = today + timedelta(days=25)
target_exp_end = today + timedelta(days=40)

call_contracts = trading_client.get_option_contracts(GetOptionContractsRequest(
    underlying_symbols=["SPY"],
    status=AssetStatus.ACTIVE,
    expiration_date_gte=target_exp_start,
    expiration_date_lte=target_exp_end,
    type=ContractType.CALL,
)).option_contracts

put_contracts = trading_client.get_option_contracts(GetOptionContractsRequest(
    underlying_symbols=["SPY"],
    status=AssetStatus.ACTIVE,
    expiration_date_gte=target_exp_start,
    expiration_date_lte=target_exp_end,
    type=ContractType.PUT,
)).option_contracts

contracts = call_contracts + put_contracts

if not contracts:
    print("No contracts found in target range.")
    exit()

exp_date = contracts[0].expiration_date
print(f"Expiration: {exp_date}")

# 3. Fetch quotes for all contracts
symbols = [contract.symbol for contract in contracts]
quotes = option_data_client.get_option_latest_quote(
    OptionLatestQuoteRequest(symbol_or_symbols=symbols)
)

# 4. Calculate IV and delta for each contract
results = []
for contract in contracts:
    quote = quotes.get(contract.symbol)
    if quote is None or quote.bid_price is None or quote.ask_price is None:
        continue
    if quote.bid_price == 0 or quote.ask_price == 0:
        continue

    mid_price = (quote.bid_price + quote.ask_price) / 2
    strike = float(contract.strike_price)
    T = (contract.expiration_date - today).days / 365
    opt_type = contract.type.value

    iv = implied_volatility(
        market_price=mid_price,
        S=spot, K=strike, T=T, r=0.045,
        option_type=opt_type,
    )
    if iv is not None:
        d = delta(spot, strike, T, 0.045, iv, option_type=opt_type)
        results.append({
            'type': opt_type,
            'strike': strike,
            'mid_price': mid_price,
            'iv': iv,
            'delta': d,
        })

# 5. Separate calls and puts
calls = sorted([o for o in results if o['type'] == 'call'], key=lambda x: x['strike'])
puts = sorted([o for o in results if o['type'] == 'put'], key=lambda x: x['strike'])

print(f"Processed {len(calls)} calls and {len(puts)} puts")

# 6. Plot the volatility smile
fig, ax = plt.subplots(figsize=(12, 8))
ax.plot([o['strike'] for o in calls], [o['iv']*100 for o in calls], 'b.-', label='Calls')
ax.plot([o['strike'] for o in puts], [o['iv']*100 for o in puts], 'r.-', label='Puts')
ax.axvline(spot, color='k', linestyle='--', label='Spot Price')
ax.set_xlabel('Strike Price')
ax.set_ylabel('Implied Volatility (%)')
ax.set_title(f'SPY Volatility Smile: {exp_date} Expiration')
ax.legend()
ax.grid(True, alpha=0.3)
plt.show()

# 7. Find the put option closest to target delta
if puts:
    target_delta = -0.3
    closest_put = min(puts, key=lambda x: abs(x['delta'] - target_delta))
    print(f"\nPut option closest to delta {target_delta}:")
    print(f"  Strike: ${closest_put['strike']}")
    print(f"  Mid Price: ${closest_put['mid_price']:.2f}")
    print(f"  Implied Volatility: {closest_put['iv']:.2%}")
    print(f"  Delta: {closest_put['delta']:.2f}")
else:
    print("\nNo puts available to evaluate.")