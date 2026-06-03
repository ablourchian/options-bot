"""
Technical indicators computed from a list of OHLCV bars.
Each function takes a list of bar dicts with keys: open, high, low, close, volume.
All return plain floats or dicts — no external dependencies beyond stdlib.
"""


def _closes(bars):  return [b["close"] for b in bars]
def _highs(bars):   return [b["high"]  for b in bars]
def _lows(bars):    return [b["low"]   for b in bars]
def _volumes(bars): return [b["volume"] for b in bars]


# ── RSI ───────────────────────────────────────────────────────────────────────

def rsi(bars, period=14):
    """
    Relative Strength Index.
    Returns float 0–100.  >70 = overbought, <30 = oversold.
    """
    closes = _closes(bars)
    if len(closes) < period + 1:
        return None

    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)


# ── Stochastic ────────────────────────────────────────────────────────────────

def stochastic(bars, k_period=14, d_period=3):
    """
    Stochastic Oscillator %K and %D.
    Returns {"k": float, "d": float}.  >80 = overbought, <20 = oversold.
    """
    closes = _closes(bars)
    highs  = _highs(bars)
    lows   = _lows(bars)

    if len(bars) < k_period + d_period:
        return {"k": None, "d": None}

    k_values = []
    for i in range(k_period - 1, len(bars)):
        window_high = max(highs[i - k_period + 1: i + 1])
        window_low  = min(lows[i  - k_period + 1: i + 1])
        denom = window_high - window_low
        k = ((closes[i] - window_low) / denom * 100) if denom != 0 else 50.0
        k_values.append(k)

    if len(k_values) < d_period:
        return {"k": None, "d": None}

    k = k_values[-1]
    d = sum(k_values[-d_period:]) / d_period
    return {"k": round(k, 2), "d": round(d, 2)}


# ── VWAP ──────────────────────────────────────────────────────────────────────

def vwap(bars):
    """
    Volume Weighted Average Price (intraday from session open).
    Returns {"vwap": float, "pct_from_vwap": float}.
    Above VWAP = bullish momentum, below = bearish.
    """
    cum_pv = 0.0
    cum_v  = 0.0
    for b in bars:
        typical = (b["high"] + b["low"] + b["close"]) / 3
        cum_pv += typical * b["volume"]
        cum_v  += b["volume"]

    if cum_v == 0:
        return {"vwap": None, "pct_from_vwap": None}

    vwap_val = cum_pv / cum_v
    last_close = bars[-1]["close"]
    pct = (last_close - vwap_val) / vwap_val * 100
    return {"vwap": round(vwap_val, 4), "pct_from_vwap": round(pct, 3)}


# ── Bollinger Bands ───────────────────────────────────────────────────────────

def bollinger_bands(bars, period=20, std_dev=2.0):
    """
    Bollinger Bands — middle (SMA), upper, lower bands.
    Returns {"middle": float, "upper": float, "lower": float, "pct_b": float, "width": float}.
    %B > 1 = above upper band (overbought), %B < 0 = below lower (oversold).
    """
    closes = _closes(bars)
    if len(closes) < period:
        return {"middle": None, "upper": None, "lower": None, "pct_b": None, "width": None}

    window = closes[-period:]
    mean   = sum(window) / period
    variance = sum((c - mean) ** 2 for c in window) / period
    std    = variance ** 0.5

    upper  = mean + std_dev * std
    lower  = mean - std_dev * std
    last   = closes[-1]
    pct_b  = (last - lower) / (upper - lower) if (upper - lower) != 0 else 0.5
    width  = (upper - lower) / mean * 100  # band width as % of price

    return {
        "middle": round(mean, 4),
        "upper":  round(upper, 4),
        "lower":  round(lower, 4),
        "pct_b":  round(pct_b, 4),
        "width":  round(width, 3),
    }


# ── MACD ──────────────────────────────────────────────────────────────────────

def macd(bars, fast=12, slow=26, signal=9):
    """
    MACD line, signal line, and histogram.
    Returns {"macd": float, "signal": float, "histogram": float}.
    Histogram > 0 = bullish momentum, < 0 = bearish.
    """
    closes = _closes(bars)
    if len(closes) < slow + signal:
        return {"macd": None, "signal": None, "histogram": None}

    def ema(data, period):
        k = 2 / (period + 1)
        e = data[0]
        for v in data[1:]:
            e = v * k + e * (1 - k)
        return e

    def ema_series(data, period):
        k = 2 / (period + 1)
        result = [sum(data[:period]) / period]
        for v in data[period:]:
            result.append(v * k + result[-1] * (1 - k))
        return result

    ema_fast   = ema_series(closes, fast)
    ema_slow   = ema_series(closes, slow)
    min_len    = min(len(ema_fast), len(ema_slow))
    macd_line  = [ema_fast[-(min_len - i)] - ema_slow[-(min_len - i)]
                  for i in range(min_len)]

    if len(macd_line) < signal:
        return {"macd": None, "signal": None, "histogram": None}

    sig_line  = ema(macd_line[-signal * 2:], signal)
    macd_val  = macd_line[-1]
    hist      = macd_val - sig_line

    return {
        "macd":      round(macd_val, 6),
        "signal":    round(sig_line, 6),
        "histogram": round(hist, 6),
    }


# ── Williams %R ───────────────────────────────────────────────────────────────

def williams_r(bars, period=14):
    """
    Williams %R.  Range -100 to 0.
    Above -20 = overbought, below -80 = oversold.
    """
    if len(bars) < period:
        return None
    closes = _closes(bars)
    highs  = _highs(bars)
    lows   = _lows(bars)
    hh = max(highs[-period:])
    ll = min(lows[-period:])
    if hh == ll:
        return -50.0
    return round((hh - closes[-1]) / (hh - ll) * -100, 2)


# ── Composite signal ──────────────────────────────────────────────────────────

def composite_signal(bars, rsi_period=14, bb_period=20, stoch_k=14):
    """
    Combines all indicators into a single directional signal.

    Returns:
        signal      : int  -5 to +5  (positive = oversold/bullish, negative = overbought/bearish)
        direction   : str  "oversold" | "overbought" | "neutral"
        confidence  : str  "strong" | "moderate" | "weak"
        indicators  : dict of all computed values
    """
    r   = rsi(bars, rsi_period)
    st  = stochastic(bars, stoch_k)
    vw  = vwap(bars)
    bb  = bollinger_bands(bars, bb_period)
    mc  = macd(bars)
    wr  = williams_r(bars)

    votes = 0
    reasons = []

    # RSI vote
    if r is not None:
        if r < 30:
            votes += 2; reasons.append(f"RSI {r:.0f} (oversold)")
        elif r < 40:
            votes += 1; reasons.append(f"RSI {r:.0f} (leaning oversold)")
        elif r > 70:
            votes -= 2; reasons.append(f"RSI {r:.0f} (overbought)")
        elif r > 60:
            votes -= 1; reasons.append(f"RSI {r:.0f} (leaning overbought)")

    # Stochastic vote
    k = st.get("k")
    if k is not None:
        if k < 20:
            votes += 1; reasons.append(f"Stoch %K {k:.0f} (oversold)")
        elif k > 80:
            votes -= 1; reasons.append(f"Stoch %K {k:.0f} (overbought)")

    # VWAP vote
    pct_vwap = vw.get("pct_from_vwap")
    if pct_vwap is not None:
        if pct_vwap < -1.5:
            votes += 1; reasons.append(f"Price {pct_vwap:.1f}% below VWAP")
        elif pct_vwap > 1.5:
            votes -= 1; reasons.append(f"Price {pct_vwap:.1f}% above VWAP")

    # Bollinger %B vote
    pct_b = bb.get("pct_b")
    if pct_b is not None:
        if pct_b < 0.05:
            votes += 1; reasons.append(f"BB %B {pct_b:.2f} (near lower band)")
        elif pct_b > 0.95:
            votes -= 1; reasons.append(f"BB %B {pct_b:.2f} (near upper band)")

    # MACD vote
    hist = mc.get("histogram")
    if hist is not None:
        if hist > 0:
            votes += 1; reasons.append(f"MACD histogram +{hist:.4f} (bullish)")
        else:
            votes -= 1; reasons.append(f"MACD histogram {hist:.4f} (bearish)")

    # Williams %R vote
    if wr is not None:
        if wr < -80:
            votes += 1; reasons.append(f"Williams %R {wr:.0f} (oversold)")
        elif wr > -20:
            votes -= 1; reasons.append(f"Williams %R {wr:.0f} (overbought)")

    # Clamp to -5..+5
    votes = max(-5, min(5, votes))

    if votes >= 3:
        direction, confidence = "oversold", "strong"
    elif votes >= 1:
        direction, confidence = "oversold", "moderate" if votes >= 2 else "weak"
    elif votes <= -3:
        direction, confidence = "overbought", "strong"
    elif votes <= -1:
        direction, confidence = "overbought", "moderate" if votes <= -2 else "weak"
    else:
        direction, confidence = "neutral", "weak"

    return {
        "signal":     votes,
        "direction":  direction,
        "confidence": confidence,
        "reasons":    reasons,
        "indicators": {
            "rsi":         r,
            "stoch_k":     k,
            "stoch_d":     st.get("d"),
            "vwap":        vw.get("vwap"),
            "pct_from_vwap": pct_vwap,
            "bb_pct_b":    pct_b,
            "bb_upper":    bb.get("upper"),
            "bb_lower":    bb.get("lower"),
            "bb_width":    bb.get("width"),
            "macd":        mc.get("macd"),
            "macd_signal": mc.get("signal"),
            "macd_hist":   hist,
            "williams_r":  wr,
        },
    }
