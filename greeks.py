"""
Black-Scholes Greeks: delta, gamma, theta, vega.
"""
import math
from scipy.stats import norm


def greeks(S, K, T, r, sigma, option_type="call"):
    """
    Returns dict of delta, gamma, theta, vega for a European option.
    Theta is expressed as daily decay (per calendar day).
    Returns None values if T <= 0 or sigma <= 0.
    """
    if T <= 0 or sigma <= 0:
        return {"delta": None, "gamma": None, "theta": None, "vega": None}

    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)

    pdf_d1 = norm.pdf(d1)
    sqrt_T = math.sqrt(T)

    gamma = pdf_d1 / (S * sigma * sqrt_T)
    vega = S * pdf_d1 * sqrt_T / 100  # per 1% move in vol

    if option_type == "call":
        delta = norm.cdf(d1)
        theta = (
            -(S * pdf_d1 * sigma) / (2 * sqrt_T)
            - r * K * math.exp(-r * T) * norm.cdf(d2)
        ) / 365
    else:
        delta = norm.cdf(d1) - 1
        theta = (
            -(S * pdf_d1 * sigma) / (2 * sqrt_T)
            + r * K * math.exp(-r * T) * norm.cdf(-d2)
        ) / 365

    return {
        "delta": round(delta, 4),
        "gamma": round(gamma, 6),
        "theta": round(theta, 4),
        "vega": round(vega, 4),
    }
