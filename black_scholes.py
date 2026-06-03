"""
Black-Scholes option pricing formula.
Returns the theoretical price of a European call or put.
"""
import math
from scipy.stats import norm


def black_scholes_price(S, K, T, r, sigma, option_type="call"):
    """
    S: spot price of the underlying
    K: strike price
    T: time to expiration in years (e.g., 30 days = 30/365)
    r: risk-free rate (e.g., 0.045 for 4.5%)
    sigma: volatility (e.g., 0.25 for 25%)
    option_type: "call" or "put"
    """
    if T <= 0 or sigma <= 0:
        # At expiration or zero-vol edge case: just intrinsic value
        if option_type == "call":
            return max(S - K, 0)
        else:
            return max(K - S, 0)

    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)

    if option_type == "call":
        price = S * norm.cdf(d1) - K * math.exp(-r * T) * norm.cdf(d2)
    elif option_type == "put":
        price = K * math.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)
    else:
        raise ValueError("option_type must be 'call' or 'put'")

    return price


# Quick test — runs only if you execute this file directly
if __name__ == "__main__":
    # Example: SPY at $725, $730 call, 30 days out, 4.5% rate, 20% vol
    price = black_scholes_price(S=725, K=730, T=30/365, r=0.045, sigma=0.20, option_type="call")
    print(f"Test call price: ${price:.2f}")
    # Should print something around $7-9
