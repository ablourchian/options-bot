"""
Trade ranker — scores each option contract on a 0-115 composite scale.

Score breakdown:
  IV Rank          25 pts  — how elevated IV is vs. its own history
  IV/HV Ratio      20 pts  — how rich premium is vs. realized vol
  Spread Quality   20 pts  — bid/ask spread as % of mid (tighter = better)
  Theta Efficiency 20 pts  — daily theta decay per dollar of premium
  Delta Profile    15 pts  — proximity to strategy sweet spot
  News Sentiment   10 pts  — bonus/penalty from news & earnings catalyst
  Intraday Signal   5 pts  — bonus if technicals agree with direction

Grades: A+ (>=85) / A (>=75) / B (>=65) / C (>=50) / D (<50)
"""

# ── Score helpers ─────────────────────────────────────────────────────────────

def _clamp(val, lo, hi):
    return max(lo, min(hi, val))


def _score_iv_rank(iv_rank: float, mode: str) -> float:
    """25 pts. Sell: reward high rank. Buy: reward low rank."""
    if mode == "sell":
        return _clamp(iv_rank / 100, 0, 1) * 25
    else:
        return _clamp(1 - iv_rank / 100, 0, 1) * 25


def _score_iv_hv(iv_hv_ratio: float | None, mode: str) -> float:
    """20 pts. Sell: reward ratio > 1. Buy: reward ratio < 1."""
    if iv_hv_ratio is None:
        return 10.0  # neutral if missing
    if mode == "sell":
        # ratio of 1.5+ = full score, 1.0 = half, <1.0 = 0
        score = _clamp((iv_hv_ratio - 1.0) / 0.5, 0, 1) * 20
    else:
        # ratio of 0.7 or below = full score, 1.0 = half, >1.0 = 0
        score = _clamp((1.0 - iv_hv_ratio) / 0.3, 0, 1) * 20
    return score


def _score_spread(bid: float, ask: float, mid: float) -> float:
    """20 pts. Spread % of mid — tighter is always better regardless of mode."""
    if mid <= 0:
        return 0.0
    spread_pct = (ask - bid) / mid
    # 0% spread = 20pts, 10% = 10pts, 20%+ = 0pts
    return _clamp(1 - spread_pct / 0.20, 0, 1) * 20


def _score_theta(theta: float | None, mid: float) -> float:
    """
    20 pts. Daily theta as % of option price (for sellers: higher = better).
    A theta of 1% of price per day is very good for selling.
    """
    if theta is None or mid <= 0:
        return 0.0
    # theta is negative; abs(theta)/mid gives daily decay rate
    decay_rate = abs(theta) / mid
    # 0% = 0pts, 1%/day = 10pts, 2%/day+ = 20pts
    return _clamp(decay_rate / 0.02, 0, 1) * 20


def _score_delta_sell(delta: float | None, option_type: str) -> float:
    """
    15 pts for sell mode.
    Sweet spot: 0.20–0.35 delta (calls) or -0.20 to -0.35 (puts).
    """
    if delta is None:
        return 0.0
    abs_delta = abs(delta)
    # Peak at 0.25, falls off linearly toward 0.15 and 0.40
    if 0.20 <= abs_delta <= 0.35:
        return 15.0
    elif abs_delta < 0.20:
        return _clamp(abs_delta / 0.20, 0, 1) * 15
    else:
        return _clamp(1 - (abs_delta - 0.35) / 0.20, 0, 1) * 15


def _score_delta_buy(delta: float | None, option_type: str) -> float:
    """
    15 pts for buy mode.
    Sweet spot: 0.40–0.60 delta (near ATM for directional plays).
    """
    if delta is None:
        return 0.0
    abs_delta = abs(delta)
    if 0.40 <= abs_delta <= 0.60:
        return 15.0
    elif abs_delta < 0.40:
        return _clamp(abs_delta / 0.40, 0, 1) * 15
    else:
        return _clamp(1 - (abs_delta - 0.60) / 0.30, 0, 1) * 15


def _score_news(news_data: dict | None, contract_type: str) -> float:
    """
    10 pts max. News sentiment bonus/penalty based on direction alignment.
    sell mode: bearish news = good (elevated fear = high IV)
    buy  mode: bullish news = good (momentum behind direction)
    Earnings within 7 days = +5 bonus (IV spike likely).
    Earnings same day or tomorrow = -5 penalty (binary event risk).
    """
    if not news_data:
        return 5.0  # neutral if no data

    sent  = news_data.get("sentiment", {})
    earn  = news_data.get("earnings", {})
    score = sent.get("score", 0)     # positive = bullish, negative = bearish
    dte   = earn.get("days_to_earnings")

    pts = 5.0  # start neutral

    # Sentiment alignment
    if contract_type == "call":
        pts += _clamp(score * 1.5, -4, 4)   # bullish news boosts calls
    else:
        pts += _clamp(-score * 1.5, -4, 4)  # bearish news boosts puts

    # Earnings proximity
    if dte is not None:
        if 0 <= dte <= 1:
            pts -= 5   # binary event — penalise both directions
        elif 2 <= dte <= 7:
            pts += 5   # imminent earnings = IV spike opportunity
        elif 8 <= dte <= 14:
            pts += 2   # upcoming earnings = mild boost

    return _clamp(pts, 0, 10)


def _score_intraday(signal: dict | None, contract_type: str) -> float:
    """
    5 pts max. Bonus if intraday technical signal agrees with contract direction.
    """
    if not signal:
        return 2.5  # neutral

    direction  = signal.get("direction", "neutral")
    sig_score  = signal.get("signal", 0)

    if contract_type == "call" and direction == "oversold":
        return _clamp(2.5 + abs(sig_score) * 0.5, 0, 5)
    elif contract_type == "put" and direction == "overbought":
        return _clamp(2.5 + abs(sig_score) * 0.5, 0, 5)
    elif direction == "neutral":
        return 2.5
    else:
        return _clamp(2.5 - abs(sig_score) * 0.5, 0, 2.5)


def grade(score: float) -> str:
    if score >= 85:
        return "A+"
    elif score >= 75:
        return "A"
    elif score >= 65:
        return "B"
    elif score >= 50:
        return "C"
    else:
        return "D"


# ── Public API ────────────────────────────────────────────────────────────────

def score_row(row: dict, mode: str = "sell",
              news_data: dict = None, intraday_signal: dict = None) -> dict:
    """
    Adds `score`, `grade`, and per-component scores to a contract row dict.
    Returns the mutated row.
    """
    iv_rank_val = row.get("iv_rank") or 50.0
    iv_hv = row.get("iv_hv_ratio")
    bid = row.get("bid", 0)
    ask = row.get("ask", 0)
    mid = row.get("mid", 0)
    theta = row.get("theta")
    delta = row.get("delta")
    otype = row.get("type", "call")

    s_iv_rank = _score_iv_rank(iv_rank_val, mode)
    s_iv_hv   = _score_iv_hv(iv_hv, mode)
    s_spread  = _score_spread(bid, ask, mid)
    s_theta   = _score_theta(theta, mid)
    s_delta   = _score_delta_sell(delta, otype) if mode == "sell" else _score_delta_buy(delta, otype)
    s_news    = _score_news(news_data, otype)
    s_signal  = _score_intraday(intraday_signal, otype)

    total = s_iv_rank + s_iv_hv + s_spread + s_theta + s_delta + s_news + s_signal

    row["score"]     = round(total, 1)
    row["grade"]     = grade(total)
    row["_s_iv_rank"] = round(s_iv_rank, 1)
    row["_s_iv_hv"]   = round(s_iv_hv, 1)
    row["_s_spread"]  = round(s_spread, 1)
    row["_s_theta"]   = round(s_theta, 1)
    row["_s_delta"]   = round(s_delta, 1)
    row["_s_news"]    = round(s_news, 1)
    row["_s_signal"]  = round(s_signal, 1)
    return row


def rank_rows(rows: list[dict], mode: str = "sell",
              news_map: dict = None, signal_map: dict = None) -> list[dict]:
    """
    Score and sort all rows, best first.
    news_map:   {underlying: news_data}  from news.get_news_for_symbols()
    signal_map: {underlying: signal}     from intraday.get_signals_bulk()
    """
    for r in rows:
        nd = (news_map or {}).get(r.get("underlying"))
        sd = (signal_map or {}).get(r.get("underlying"))
        score_row(r, mode, news_data=nd, intraday_signal=sd)
    return sorted(rows, key=lambda r: r["score"], reverse=True)
