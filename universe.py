"""
Index universe loader — S&P 500, Dow 30, Nasdaq 100.
Fetches S&P 500 and Nasdaq 100 from Wikipedia; Dow is hardcoded (only 30 names).
Results are cached to disk for 24 hours to avoid hammering Wikipedia.
"""
import json
import os
import time
import urllib.request
import html.parser

CACHE_FILE = os.path.join(os.path.dirname(__file__), ".universe_cache.json")
CACHE_TTL = 60 * 60 * 24  # 24 hours

DOW_30 = [
    "AAPL", "AMGN", "AXP", "BA", "CAT", "CRM", "CSCO", "CVX", "DIS", "DOW",
    "GS", "HD", "HON", "IBM", "INTC", "JNJ", "JPM", "KO", "MCD", "MMM",
    "MRK", "MSFT", "NKE", "PG", "TRV", "UNH", "V", "VZ", "WBA", "WMT",
]


class _TableParser(html.parser.HTMLParser):
    """Minimal Wikipedia table scraper — extracts first column of first wikitable."""

    def __init__(self):
        super().__init__()
        self.in_table = False
        self.in_td = False
        self.depth = 0
        self.tickers = []
        self._buf = ""
        self._first_col = True
        self._col_idx = 0

    def handle_starttag(self, tag, attrs):
        attr_dict = dict(attrs)
        if tag == "table" and "wikitable" in attr_dict.get("class", ""):
            self.in_table = True
            self.depth = 0
        if self.in_table:
            if tag == "tr":
                self._first_col = True
                self._col_idx = 0
            if tag in ("td", "th"):
                self.in_td = self._col_idx == 0
                self._buf = ""
                self._col_idx += 1

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self.in_table and self.in_td:
            ticker = self._buf.strip().split("\n")[0].strip()
            if ticker and ticker.isalpha() and len(ticker) <= 5 and ticker.upper() == ticker:
                self.tickers.append(ticker)
            self.in_td = False
        if tag == "table":
            self.in_table = False

    def handle_data(self, data):
        if self.in_td:
            self._buf += data


def _fetch_wiki_tickers(url: str) -> list[str]:
    req = urllib.request.Request(url, headers={"User-Agent": "options-bot/1.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        html_bytes = resp.read().decode("utf-8", errors="replace")
    parser = _TableParser()
    parser.feed(html_bytes)
    return list(dict.fromkeys(parser.tickers))  # dedupe, preserve order


def _load_cache() -> dict:
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE) as f:
            data = json.load(f)
        if time.time() - data.get("ts", 0) < CACHE_TTL:
            return data
    return {}


def _save_cache(data: dict):
    data["ts"] = time.time()
    with open(CACHE_FILE, "w") as f:
        json.dump(data, f)


def get_sp500() -> list[str]:
    cache = _load_cache()
    if "sp500" in cache:
        return cache["sp500"]
    try:
        tickers = _fetch_wiki_tickers(
            "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
        )
        # Wikipedia lists BRK.B as BRK.B — Alpaca uses BRK/B, skip edge cases
        tickers = [t.replace(".", "/") for t in tickers if t.isalpha() or "." in t]
        tickers = [t for t in tickers if len(t) <= 5]
        cache["sp500"] = tickers
        _save_cache(cache)
        return tickers
    except Exception as e:
        print(f"  [!] Could not fetch S&P 500 list: {e}")
        return []


def get_nasdaq100() -> list[str]:
    cache = _load_cache()
    if "nasdaq100" in cache:
        return cache["nasdaq100"]
    try:
        tickers = _fetch_wiki_tickers(
            "https://en.wikipedia.org/wiki/Nasdaq-100"
        )
        tickers = [t for t in tickers if t.isalpha() and len(t) <= 5]
        cache["nasdaq100"] = tickers
        _save_cache(cache)
        return tickers
    except Exception as e:
        print(f"  [!] Could not fetch Nasdaq 100 list: {e}")
        return []


def get_dow30() -> list[str]:
    return DOW_30.copy()


def get_universe(include_sp500=True, include_nasdaq=True, include_dow=True) -> list[str]:
    """Returns deduplicated union of requested indices."""
    seen = set()
    result = []
    sources = []
    if include_sp500:
        sources.append(get_sp500())
    if include_nasdaq:
        sources.append(get_nasdaq100())
    if include_dow:
        sources.append(get_dow30())
    for lst in sources:
        for t in lst:
            if t not in seen:
                seen.add(t)
                result.append(t)
    return result
