"""
Historical volatility and IV rank utilities.

HV is computed from daily log returns over a trailing window.
IV rank = (current IV - HV_min) / (HV_max - HV_min)  over the lookback period.
IV percentile = fraction of days where daily HV was below current IV.
IV/HV ratio = current IV divided by 30-day HV (quick premium signal).
"""
import math
from datetime import date, timedelta
from zoneinfo import ZoneInfo

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame


def historical_volatility(
    symbol: str,
    client: StockHistoricalDataClient,
    window: int = 30,
    lookback_days: int = 365,
) -> dict:
    """
    Returns:
        hv_current  — annualised HV over the most recent `window` trading days
        hv_min      — lowest rolling HV over `lookback_days`
        hv_max      — highest rolling HV over `lookback_days`
        iv_rank_base — (hv_current - hv_min) / (hv_max - hv_min), 0-100
        all_hvs     — list of all rolling HV values (for percentile calcs)
    Returns None on failure.
    """
    end = date.today()
    start = end - timedelta(days=lookback_days + window * 2)  # extra buffer for weekends

    try:
        bars = client.get_stock_bars(StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=TimeFrame.Day,
            start=start,
            end=end,
        ))[symbol]
    except Exception:
        return None

    closes = [b.close for b in bars]
    if len(closes) < window + 2:
        return None

    log_returns = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]

    all_hvs = []
    for i in range(window, len(log_returns) + 1):
        chunk = log_returns[i - window:i]
        mean = sum(chunk) / window
        variance = sum((r - mean) ** 2 for r in chunk) / (window - 1)
        hv = math.sqrt(variance * 252)
        all_hvs.append(hv)

    if not all_hvs:
        return None

    hv_current = all_hvs[-1]
    hv_min = min(all_hvs)
    hv_max = max(all_hvs)

    iv_rank_base = (
        (hv_current - hv_min) / (hv_max - hv_min) * 100
        if hv_max > hv_min else 50.0
    )

    return {
        "hv_current": round(hv_current * 100, 2),   # as percentage
        "hv_min": round(hv_min * 100, 2),
        "hv_max": round(hv_max * 100, 2),
        "hv_rank": round(iv_rank_base, 1),
        "all_hvs": all_hvs,
    }


def iv_rank(current_iv_pct: float, hv_data: dict) -> dict:
    """
    Given current IV (as a percentage, e.g. 28.5) and hv_data from historical_volatility(),
    returns IV rank and IV percentile relative to the historical HV distribution.

    IV rank    = (IV - HV_min) / (HV_max - HV_min) * 100
    IV pctile  = % of historical HV readings below current IV
    IV/HV      = current IV / current 30d HV
    """
    iv = current_iv_pct / 100.0
    hv_min = hv_data["hv_min"] / 100.0
    hv_max = hv_data["hv_max"] / 100.0
    hv_curr = hv_data["hv_current"] / 100.0
    all_hvs = hv_data["all_hvs"]

    rank = (
        (iv - hv_min) / (hv_max - hv_min) * 100
        if hv_max > hv_min else 50.0
    )
    rank = max(0.0, min(100.0, rank))

    percentile = sum(1 for h in all_hvs if h < iv) / len(all_hvs) * 100

    iv_hv_ratio = (iv / hv_curr) if hv_curr > 0 else None

    return {
        "iv_rank": round(rank, 1),
        "iv_pctile": round(percentile, 1),
        "iv_hv_ratio": round(iv_hv_ratio, 2) if iv_hv_ratio else None,
    }
