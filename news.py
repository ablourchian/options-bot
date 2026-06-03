"""
News engine — aggregates stock news from multiple sources:
  1. Alpaca News API       — stock-specific, already credentialed
  2. CNBC RSS              — top market/business stories
  3. Reuters Business RSS  — global business & markets
  4. MarketWatch RSS       — market news
  5. Yahoo Finance RSS     — per-ticker news
  6. Seeking Alpha RSS     — per-ticker analysis
  7. Google News RSS       — broad search by company name

Also provides keyword-based sentiment scoring (no ML needed).

Usage:
    python news.py SPY TSLA NVDA           # print news + sentiment for tickers
    python news.py --sources rss           # RSS only (no Alpaca)
    python news.py --hours 48 AAPL         # last 48 hours of news
"""
import os
import re
import xml.etree.ElementTree as ET
import urllib.request
import urllib.parse
import argparse
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

load_dotenv()

API_KEY    = os.getenv("ALPACA_API_KEY")
SECRET_KEY = os.getenv("ALPACA_SECRET_KEY")

# ── RSS Sources ───────────────────────────────────────────────────────────────

RSS_SOURCES = {
    "CNBC Markets":     "https://www.cnbc.com/id/20910258/device/rss/rss.html",
    "CNBC Finance":     "https://www.cnbc.com/id/10000664/device/rss/rss.html",
    "Reuters Business": "https://feeds.reuters.com/reuters/businessNews",
    "MarketWatch":      "https://feeds.marketwatch.com/marketwatch/topstories/",
    "Investopedia":     "https://www.investopedia.com/feeds/news.aspx",
}

def _ticker_rss_sources(symbol: str, company_name: str = None) -> dict:
    name_q = urllib.parse.quote(company_name or symbol)
    sym_q  = urllib.parse.quote(symbol)
    return {
        f"Yahoo Finance ({symbol})":  f"https://finance.yahoo.com/rss/headline?s={sym_q}",
        f"Seeking Alpha ({symbol})":  f"https://seekingalpha.com/symbol/{sym_q}/feed.xml",
        f"Google News ({symbol})":    f"https://news.google.com/rss/search?q={name_q}+stock&hl=en-US&gl=US&ceid=US:en",
    }


# ── Sentiment lexicon ─────────────────────────────────────────────────────────

_POSITIVE = [
    "beat", "beats", "exceeded", "surpassed", "record", "all-time high",
    "raised guidance", "raised forecast", "upgrade", "upgraded", "outperform",
    "buy rating", "strong buy", "price target raised", "pt raised",
    "partnership", "acquisition", "buyout", "deal", "contract", "won",
    "growth", "profit", "revenue growth", "sales growth", "expanding",
    "bullish", "momentum", "breakout", "rally", "surge", "soar", "jump",
    "positive", "optimistic", "recovery", "rebound", "turnaround",
]

_NEGATIVE = [
    "miss", "missed", "below estimates", "fell short", "disappointed",
    "cut guidance", "lowered guidance", "lowered forecast", "downgrade",
    "downgraded", "underperform", "sell rating", "price target cut", "pt cut",
    "recall", "lawsuit", "investigation", "probe", "subpoena", "fine",
    "bankruptcy", "default", "debt", "layoffs", "job cuts", "restructuring",
    "loss", "revenue decline", "sales decline", "shrinking", "contraction",
    "bearish", "selloff", "plunge", "crash", "drop", "fall", "decline",
    "negative", "concern", "warning", "risk", "headwind", "uncertainty",
]

_HIGH_IMPACT = [
    "earnings", "eps", "quarterly results", "q1", "q2", "q3", "q4",
    "fed", "federal reserve", "interest rate", "inflation", "cpi",
    "merger", "acquisition", "buyout", "takeover", "ipo",
    "sec", "doj", "investigation", "lawsuit", "recall",
    "guidance", "forecast", "outlook",
]


def sentiment_score(text: str) -> dict:
    """
    Returns {"score": int, "label": str, "pos": int, "neg": int, "high_impact": bool}.
    score: positive = bullish, negative = bearish.
    """
    t = text.lower()
    pos = sum(1 for w in _POSITIVE if w in t)
    neg = sum(1 for w in _NEGATIVE if w in t)
    high_impact = any(w in t for w in _HIGH_IMPACT)
    score = pos - neg
    if score > 1:    label = "bullish"
    elif score == 1: label = "leaning bullish"
    elif score == -1: label = "leaning bearish"
    elif score < -1: label = "bearish"
    else:             label = "neutral"
    return {"score": score, "label": label, "pos": pos, "neg": neg, "high_impact": high_impact}


# ── RSS parser ────────────────────────────────────────────────────────────────

def _fetch_rss(url: str, timeout: int = 8) -> list[dict]:
    try:
        import feedparser
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (compatible; options-bot/1.0)"
        })
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            content = resp.read()
        feed = feedparser.parse(content)
        items = []
        for e in feed.entries[:15]:
            title   = getattr(e, "title", "")
            summary = getattr(e, "summary", "")
            link    = getattr(e, "link", "")
            pub     = getattr(e, "published", "") or getattr(e, "updated", "")
            items.append({
                "title":   title,
                "summary": summary[:300],
                "url":     link,
                "source":  feed.feed.get("title", url),
                "published": pub,
            })
        return items
    except Exception:
        return []


# ── Alpaca News API ───────────────────────────────────────────────────────────

def _fetch_alpaca_news(symbols: list[str], hours: int = 24) -> list[dict]:
    if not API_KEY:
        return []
    start = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
    params = urllib.parse.urlencode({
        "symbols": ",".join(symbols),
        "start":   start,
        "limit":   50,
        "sort":    "desc",
    })
    url = f"https://data.alpaca.markets/v1beta1/news?{params}"
    try:
        req = urllib.request.Request(url, headers={
            "APCA-API-KEY-ID":     API_KEY,
            "APCA-API-SECRET-KEY": SECRET_KEY,
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            import json
            data = json.loads(resp.read())
        items = []
        for a in data.get("news", []):
            items.append({
                "title":     a.get("headline", ""),
                "summary":   a.get("summary", "")[:300],
                "url":       a.get("url", ""),
                "source":    a.get("source", "Alpaca"),
                "published": a.get("created_at", ""),
                "symbols":   a.get("symbols", []),
            })
        return items
    except Exception:
        return []


# ── Earnings (yfinance) ───────────────────────────────────────────────────────

def get_earnings_info(symbol: str) -> dict:
    """
    Returns earnings calendar info for a symbol via yfinance.
    {
        next_earnings_date: str | None,
        days_to_earnings: int | None,
        last_eps_actual: float | None,
        last_eps_estimate: float | None,
        eps_surprise_pct: float | None,
        earnings_trend: str  "beat" | "miss" | "unknown"
    }
    """
    try:
        import yfinance as yf
        ticker = yf.Ticker(symbol)
        info   = ticker.info or {}

        # Next earnings date
        next_date = info.get("earningsDate") or info.get("earningsTimestamp")
        days_to   = None
        date_str  = None
        if next_date:
            if isinstance(next_date, (int, float)):
                dt = datetime.fromtimestamp(next_date, tz=timezone.utc)
            elif isinstance(next_date, list) and next_date:
                dt = datetime.fromtimestamp(next_date[0], tz=timezone.utc)
            else:
                dt = None
            if dt:
                days_to  = (dt.date() - datetime.now(timezone.utc).date()).days
                date_str = dt.strftime("%Y-%m-%d")

        # Last earnings surprise
        cal = ticker.calendar
        eps_actual = eps_est = surprise_pct = None
        trend = "unknown"
        try:
            hist = ticker.earnings_history
            if hist is not None and not hist.empty:
                latest = hist.iloc[-1]
                eps_actual = latest.get("epsActual")
                eps_est    = latest.get("epsEstimate")
                surprise   = latest.get("epsDifference")
                if eps_est and eps_est != 0 and surprise is not None:
                    surprise_pct = round(surprise / abs(eps_est) * 100, 1)
                    trend = "beat" if surprise > 0 else "miss"
        except Exception:
            pass

        return {
            "next_earnings_date": date_str,
            "days_to_earnings":   days_to,
            "last_eps_actual":    eps_actual,
            "last_eps_estimate":  eps_est,
            "eps_surprise_pct":   surprise_pct,
            "earnings_trend":     trend,
        }
    except Exception:
        return {
            "next_earnings_date": None, "days_to_earnings": None,
            "last_eps_actual": None, "last_eps_estimate": None,
            "eps_surprise_pct": None, "earnings_trend": "unknown",
        }


# ── Main aggregator ───────────────────────────────────────────────────────────

def get_news_for_symbols(symbols: list[str], hours: int = 24,
                         include_rss: bool = True) -> dict[str, dict]:
    """
    Returns per-symbol news summary:
    {
        symbol: {
            articles: [...],
            sentiment: {score, label, pos, neg, high_impact},
            earnings:  {...},
            catalyst:  str    # short summary of key events
        }
    }
    """
    results = {}

    # Fetch Alpaca news for all symbols at once
    alpaca_articles = _fetch_alpaca_news(symbols, hours=hours)

    for sym in symbols:
        articles = [a for a in alpaca_articles if sym in a.get("symbols", [])]

        if include_rss:
            for source_name, url in _ticker_rss_sources(sym).items():
                rss_items = _fetch_rss(url)
                for item in rss_items:
                    item["source"] = source_name
                articles.extend(rss_items)

        # Deduplicate by title similarity
        seen_titles = set()
        deduped = []
        for a in articles:
            key = re.sub(r'\W+', '', a["title"].lower())[:40]
            if key not in seen_titles:
                seen_titles.add(key)
                deduped.append(a)

        # Sentiment across all headlines
        combined_text = " ".join(
            (a.get("title","") + " " + a.get("summary",""))
            for a in deduped
        )
        sent = sentiment_score(combined_text)

        # Earnings
        earnings = get_earnings_info(sym)

        # Catalyst summary
        catalysts = []
        dte = earnings.get("days_to_earnings")
        if dte is not None and 0 <= dte <= 7:
            catalysts.append(f"EARNINGS in {dte}d ({earnings['next_earnings_date']})")
        elif dte is not None and 0 <= dte <= 30:
            catalysts.append(f"Earnings in {dte}d")

        if earnings.get("eps_surprise_pct") is not None:
            t = earnings["earnings_trend"].upper()
            catalysts.append(f"Last EPS {t} ({earnings['eps_surprise_pct']:+.1f}%)")

        # Scan top headlines for high-impact events
        for a in deduped[:5]:
            s = sentiment_score(a["title"])
            if s["high_impact"] and abs(s["score"]) >= 1:
                snippet = a["title"][:80]
                catalysts.append(snippet)
                break

        results[sym] = {
            "articles":  deduped[:20],
            "sentiment": sent,
            "earnings":  earnings,
            "catalyst":  " | ".join(catalysts) if catalysts else "No major catalyst",
            "article_count": len(deduped),
        }

    return results


# ── CLI ───────────────────────────────────────────────────────────────────────

def print_news_summary(results: dict):
    for sym, data in results.items():
        sent = data["sentiment"]
        earn = data["earnings"]
        label_color = {"bullish":"[+]","leaning bullish":"[+]",
                       "neutral":"[ ]","leaning bearish":"[-]","bearish":"[-]"}
        lc = label_color.get(sent["label"], "[ ]")

        print(f"\n  {'='*60}")
        print(f"  {sym}  {lc} {sent['label'].upper()}  (score {sent['score']:+d})")
        print(f"  {'='*60}")
        print(f"  Catalyst:  {data['catalyst']}")
        print(f"  Articles:  {data['article_count']}  |  "
              f"Pos signals: {sent['pos']}  Neg signals: {sent['neg']}")
        if earn["next_earnings_date"]:
            dte = earn["days_to_earnings"]
            flag = "  *** IMMINENT ***" if dte is not None and dte <= 7 else ""
            print(f"  Earnings:  {earn['next_earnings_date']} ({dte}d away){flag}")
        if earn["eps_surprise_pct"] is not None:
            print(f"  Last EPS:  {earn['earnings_trend'].upper()} "
                  f"({earn['eps_surprise_pct']:+.1f}% surprise)  "
                  f"actual={earn['last_eps_actual']}  est={earn['last_eps_estimate']}")

        print(f"\n  Top headlines:")
        for i, a in enumerate(data["articles"][:6], 1):
            s = sentiment_score(a["title"])
            flag = "[!]" if s["high_impact"] else "   "
            print(f"  {flag} {i}. {a['title'][:90]}")
            print(f"       {a['source']}  {a.get('published','')[:16]}")


def main():
    parser = argparse.ArgumentParser(description="News + earnings aggregator")
    parser.add_argument("symbols", nargs="*", default=["SPY","TSLA","NVDA","AAPL"])
    parser.add_argument("--hours", type=int, default=24)
    parser.add_argument("--no-rss", action="store_true")
    args = parser.parse_args()

    symbols = [s.upper() for s in args.symbols]
    print(f"\n  Fetching news for {symbols}  (last {args.hours}h)...")

    results = get_news_for_symbols(symbols, hours=args.hours, include_rss=not args.no_rss)
    print_news_summary(results)


if __name__ == "__main__":
    main()
