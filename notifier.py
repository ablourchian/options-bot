"""
Discord notifier — sends the daily scan results as a rich embed
with the top trades and attaches the HTML dashboard file.
"""
import os
import json
import urllib.request
import urllib.parse
from datetime import date

from dotenv import load_dotenv

load_dotenv()
WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK")

GRADE_EMOJI = {"A+": "🟢", "A": "🟩", "B": "🟡", "C": "🟠", "D": "🔴"}
TYPE_EMOJI  = {"call": "📈", "put": "📉"}


def _grade_color(grade: str) -> int:
    return {
        "A+": 0x00c853,
        "A":  0x69f0ae,
        "B":  0xffd740,
        "C":  0xff6d00,
        "D":  0xd50000,
    }.get(grade, 0x888888)


def _fmt(val, spec=".2f", fallback="—"):
    if val is None:
        return fallback
    try:
        return format(float(val), spec)
    except Exception:
        return fallback


def send_daily_report(ranked: list[dict], scan_date: str, dashboard_path: str = None, pages_url: str = None):
    if not WEBHOOK_URL:
        print("  [notifier] DISCORD_WEBHOOK not set in .env — skipping.")
        return

    top = ranked[:5]
    top_grade = top[0].get("grade", "B") if top else "B"

    a_plus  = sum(1 for r in ranked if r.get("grade") == "A+")
    a_grade = sum(1 for r in ranked if r.get("grade") == "A")
    total   = len(ranked)
    tickers = len(set(r["underlying"] for r in ranked))
    avg_score = sum(r.get("score") or 0 for r in ranked) / total if total else 0

    # Build fields for top 5 trades
    fields = []
    for i, r in enumerate(top, 1):
        ge  = GRADE_EMOJI.get(r.get("grade","D"), "⚪")
        te  = TYPE_EMOJI.get(r.get("type","call"), "")
        sym = r.get("underlying","")
        typ = r.get("type","").upper()
        fields.append({
            "name": f"{ge} #{i}  {sym}  {te} {typ}",
            "value": (
                f"```"
                f"Score   {_fmt(r.get('score'),'.1f'):>6}    Grade  {r.get('grade','—')}\n"
                f"Strike  ${_fmt(r.get('strike')):>6}    DTE    {_fmt(r.get('dte'),'.0f')}d\n"
                f"Mid     ${_fmt(r.get('mid')):>6}    Spot   ${_fmt(r.get('spot'))}\n"
                f"IV%     {_fmt(r.get('iv'),'.1f'):>5}%    IVRnk  {_fmt(r.get('iv_rank'),'.1f'):>5}%\n"
                f"Delta   {_fmt(r.get('delta'),'.3f'):>6}    Theta  {_fmt(r.get('theta'),'.3f'):>6}\n"
                f"IV/HV   {_fmt(r.get('iv_hv_ratio'),'.2f'):>6}    Spread {_fmt((r['ask']-r['bid'])/r['mid']*100 if r.get('mid') else None,'.1f'):>5}%"
                f"```"
            ),
            "inline": False,
        })

    desc = (
        f"**{a_plus}** A+ setups · **{a_grade}** A setups · "
        f"**{total}** contracts · **{tickers}** tickers · avg score **{avg_score:.1f}**"
    )
    if pages_url:
        desc += f"\n\n[**View Full Dashboard →**]({pages_url})"

    embed = {
        "title": f"📊 Options Bot — {scan_date}",
        "description": desc,
        "color": _grade_color(top_grade),
        "fields": fields,
        "footer": {"text": "Options Bot · paper trading · Alpaca"},
        "timestamp": f"{scan_date}T08:30:00.000Z",
    }

    payload = {"embeds": [embed]}

    # If dashboard HTML exists, send as multipart with file attachment
    if dashboard_path and os.path.exists(dashboard_path):
        _send_with_file(payload, dashboard_path)
    else:
        _send_json(payload)


def _send_json(payload: dict):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        WEBHOOK_URL,
        data=data,
        headers={"Content-Type": "application/json", "User-Agent": "DiscordBot (options-bot, 1.0)"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            if resp.status in (200, 204):
                print("  [notifier] Discord message sent.")
            else:
                print(f"  [notifier] Discord returned HTTP {resp.status}")
    except Exception as e:
        print(f"  [notifier] Failed to send Discord message: {e}")


def _send_with_file(payload: dict, file_path: str):
    """Send embed + file attachment via multipart/form-data."""
    boundary = "----OptionsBotBoundary7x3k9"
    filename  = os.path.basename(file_path)

    with open(file_path, "rb") as f:
        file_bytes = f.read()

    payload_json = json.dumps(payload).encode("utf-8")

    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="payload_json"\r\n'
        f"Content-Type: application/json\r\n\r\n"
    ).encode() + payload_json + (
        f"\r\n--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: text/html\r\n\r\n"
    ).encode() + file_bytes + f"\r\n--{boundary}--\r\n".encode()

    req = urllib.request.Request(
        WEBHOOK_URL,
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            if resp.status in (200, 204):
                print("  [notifier] Discord message + dashboard file sent.")
            else:
                print(f"  [notifier] Discord returned HTTP {resp.status}")
    except Exception as e:
        print(f"  [notifier] Failed to send Discord message: {e}")
        # fallback: send without file
        _send_json(payload)
