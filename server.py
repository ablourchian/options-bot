"""
Web server for the Options Bot dashboard.

Usage:
    python server.py              # serves on http://localhost:5000
    python server.py --port 8080
"""
import argparse
import os
from datetime import date
from flask import Flask, Response

from dashboard import load_results, load_tracker_history, generate_html

app = Flask(__name__)
RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results")


def get_latest_date():
    if not os.path.exists(RESULTS_DIR):
        return None
    files = sorted(
        [f for f in os.listdir(RESULTS_DIR) if f.endswith(".csv") and f != "tracker.csv"],
        reverse=True,
    )
    if files:
        return files[0].replace(".csv", "")
    return str(date.today())


@app.route("/")
def index():
    scan_date = get_latest_date()
    if not scan_date:
        return Response("No scan results found. Run daily_scan.py first.", mimetype="text/plain", status=404)
    rows = load_results(scan_date)
    if not rows:
        return Response("No scan results found. Run daily_scan.py first.", mimetype="text/plain", status=404)
    history = load_tracker_history()
    html = generate_html(rows, history, scan_date)
    return Response(html, mimetype="text/html")


@app.route("/<scan_date>")
def by_date(scan_date):
    rows = load_results(scan_date)
    if not rows:
        return Response(f"No results found for {scan_date}.", mimetype="text/plain", status=404)
    history = load_tracker_history()
    html = generate_html(rows, history, scan_date)
    return Response(html, mimetype="text/html")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()
    print(f"Dashboard running at http://{args.host}:{args.port}")
    app.run(host=args.host, port=args.port, debug=True)
