from scipy.optimize import brentq
from black_scholes import black_scholes_price


def implied_volatility(market_price, S, K, T, r, option_type="call"):
    if T <= 0:
        return None

    if option_type == "call":
        intrinsic = max(S - K, 0)
    else:
        intrinsic = max(K - S, 0)
    if market_price < intrinsic:
        return None

    def objective(sigma):
        return black_scholes_price(S, K, T, r, sigma, option_type) - market_price

    try:
        iv = brentq(objective, 0.001, 5.0, maxiter=100)
        return iv
    except ValueError:
        return None