"""
Tracker viewer — query and display historical scan results.

Usage:
    python view_tracker.py                       # show all-time top 20 by score
    python view_tracker.py --date 2025-06-03     # show a specific day
    python view_tracker.py --symbol SPY          # filter by underlying
    python view_tracker.py --grade A+            # filter by grade
    python view_tracker.py --type put            # filter by contract type
    python view_tracker.py --top 50              # show more rows
    python view_tracker.py --summary             # daily summary stats
"""
import os
import csv
import argparse
from collections import defaultdict

RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results")
TRACKER = os.path.join(RESULTS_DIR, "tracker.csv")


def load_tracker(date_filter=None, symbol_filter=None, grade_filter=None, type_filter=None):
    if not os.path.exists(TRACKER):
        print("No tracker file found. Run daily_scan.py first.")
        return []
    rows = []
    with open(TRACKER, newline="") as f:
        reader = csv.DictReader(f)
        for r in reader:
            if date_filter and r["date"] != date_filter:
                continue
            if symbol_filter and r["underlying"].upper() != symbol_filter.upper():
                continue
            if grade_filter and r["grade"] != grade_filter:
                continue
            if type_filter and r["type"] != type_filter:
                continue
            # cast numeric fields
            for field in ["score", "iv", "iv_rank", "iv_hv_ratio", "delta", "theta",
                          "mid", "bid", "ask", "strike", "dte", "spot"]:
                try:
                    r[field] = float(r[field]) if r[field] not in ("", "None") else None
                except (ValueError, KeyError):
                    r[field] = None
            rows.append(r)
    return rows


def print_rows(rows, top_n=20):
    rows = sorted(rows, key=lambda r: r["score"] or 0, reverse=True)[:top_n]
    if not rows:
        print("  No results matching filters.")
        return
    print(f"\n  {'Date':<12} {'#':<4} {'Grd':<4} {'Score':>5} "
          f"{'Symbol':<24} {'T':<4} {'Strike':>7} {'DTE':>4} "
          f"{'Mid':>6} {'IV%':>5} {'IVRnk':>6} {'IV/HV':>6} {'Delta':>7} {'Theta':>7}")
    print(f"  {'-'*110}")
    for r in rows:
        ivr = f"{r['iv_rank']:.1f}%" if r["iv_rank"] is not None else "  N/A"
        ivhv = f"{r['iv_hv_ratio']:.2f}" if r["iv_hv_ratio"] is not None else "  N/A"
        print(
            f"  {r['date']:<12} {r['rank']:<4} {r['grade']:<4} {r['score']:>5.1f} "
            f"{r['symbol']:<24} {r['type'][0].upper():<4} {r['strike']:>7.2f} "
            f"{r['dte']:>4.0f} {r['mid']:>6.2f} "
            f"{r['iv']:>4.1f}% {ivr:>6} {ivhv:>6} "
            f"{r['delta']:>7.4f} {r['theta']:>7.4f}"
        )
    print(f"  {'-'*110}")
    print(f"  {len(rows)} rows shown\n")


def print_summary(rows):
    by_date = defaultdict(list)
    for r in rows:
        by_date[r["date"]].append(r)

    print(f"\n  {'Date':<12} {'Scans':>6} {'AvgScore':>9} {'A+':>4} {'A':>4} "
          f"{'B':>4} {'Top Symbol':<12} {'Top Score':>10}")
    print(f"  {'-'*70}")
    for d in sorted(by_date.keys()):
        day_rows = sorted(by_date[d], key=lambda r: r["score"] or 0, reverse=True)
        scores = [r["score"] for r in day_rows if r["score"]]
        avg = sum(scores) / len(scores) if scores else 0
        a_plus = sum(1 for r in day_rows if r["grade"] == "A+")
        a = sum(1 for r in day_rows if r["grade"] == "A")
        b = sum(1 for r in day_rows if r["grade"] == "B")
        top = day_rows[0] if day_rows else None
        top_sym = top["underlying"] if top else "-"
        top_score = top["score"] if top else 0
        print(f"  {d:<12} {len(day_rows):>6} {avg:>9.1f} {a_plus:>4} {a:>4} "
              f"{b:>4} {top_sym:<12} {top_score:>10.1f}")
    print()


def main():
    parser = argparse.ArgumentParser(description="View historical scan tracker")
    parser.add_argument("--date", default=None, help="Filter by date (YYYY-MM-DD)")
    parser.add_argument("--symbol", default=None, help="Filter by underlying symbol")
    parser.add_argument("--grade", default=None, choices=["A+", "A", "B", "C", "D"])
    parser.add_argument("--type", dest="contract_type", default=None, choices=["call", "put"])
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument("--summary", action="store_true", help="Show daily summary stats")
    args = parser.parse_args()

    rows = load_tracker(
        date_filter=args.date,
        symbol_filter=args.symbol,
        grade_filter=args.grade,
        type_filter=args.contract_type,
    )

    if args.summary:
        print_summary(rows)
    else:
        print_rows(rows, top_n=args.top)


if __name__ == "__main__":
    main()
