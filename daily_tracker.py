#!/usr/bin/env python3
"""
Daily Tracker — generates Apple Calendar (.ics) time blocks for day planning.

Usage:
    python daily_tracker.py                    # today + 4 more weekdays
    python daily_tracker.py --date 2026-06-16  # start from a specific date
    python daily_tracker.py --days 10          # generate 2 weeks
    python daily_tracker.py --output ~/Desktop/my_week.ics

Open the generated .ics file to import directly into Apple Calendar.
"""

import argparse
import uuid
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

# ── Customize your daily schedule ───────────────────────────────────────────
# (start, end, title, category)
# Categories: WORK | ADMIN | PERSONAL | HEALTH
TIME_BLOCKS = [
    ("07:00", "07:30", "Morning Routine",        "PERSONAL"),
    ("07:30", "08:00", "Review Day Plan",         "ADMIN"),
    ("08:00", "10:00", "Deep Work — Block 1",    "WORK"),
    ("10:00", "10:15", "Break",                  "PERSONAL"),
    ("10:15", "12:00", "Deep Work — Block 2",    "WORK"),
    ("12:00", "13:00", "Lunch",                  "PERSONAL"),
    ("13:00", "13:30", "Email & Messages",        "ADMIN"),
    ("13:30", "15:30", "Deep Work — Block 3",    "WORK"),
    ("15:30", "15:45", "Break",                  "PERSONAL"),
    ("15:45", "17:00", "Meetings & Follow-ups",  "WORK"),
    ("17:00", "17:30", "Daily Review & EOD",     "ADMIN"),
]

CALENDAR_NAME = "Daily Tracker"
TIMEZONE = "America/New_York"  # Change to your local timezone
# ────────────────────────────────────────────────────────────────────────────

CATEGORY_COLORS = {
    "WORK": "3",      # red
    "ADMIN": "7",     # blue
    "PERSONAL": "5",  # green
    "HEALTH": "9",    # purple
}


def fmt_dt(dt: datetime) -> str:
    return dt.astimezone(ZoneInfo("UTC")).strftime("%Y%m%dT%H%M%SZ")


def make_event(day: date, start: str, end: str, title: str, category: str, tz) -> str:
    start_dt = datetime.fromisoformat(f"{day}T{start}:00").replace(tzinfo=tz)
    end_dt = datetime.fromisoformat(f"{day}T{end}:00").replace(tzinfo=tz)
    now = datetime.now(ZoneInfo("UTC")).strftime("%Y%m%dT%H%M%SZ")
    color = CATEGORY_COLORS.get(category, "0")
    return "\n".join([
        "BEGIN:VEVENT",
        f"UID:{uuid.uuid4()}@daily-tracker",
        f"DTSTAMP:{now}",
        f"DTSTART:{fmt_dt(start_dt)}",
        f"DTEND:{fmt_dt(end_dt)}",
        f"SUMMARY:{title}",
        f"DESCRIPTION:[{category}]",
        f"COLOR:{color}",
        "STATUS:CONFIRMED",
        "TRANSP:OPAQUE",
        "END:VEVENT",
    ])


def generate_ics(start_date: date, num_days: int, output_path: str) -> None:
    tz = ZoneInfo(TIMEZONE)
    events = []
    days_generated = 0

    i = 0
    while days_generated < num_days:
        day = start_date + timedelta(days=i)
        i += 1
        if day.weekday() >= 5:  # skip weekends
            continue
        for start, end, title, category in TIME_BLOCKS:
            events.append(make_event(day, start, end, title, category, tz))
        days_generated += 1

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Daily Tracker//EN",
        f"X-WR-CALNAME:{CALENDAR_NAME}",
        f"X-WR-TIMEZONE:{TIMEZONE}",
        "",
    ]
    lines.extend("\n".join(e.splitlines()) for e in events)
    lines.append("END:VCALENDAR")

    content = "\n".join(lines)
    with open(output_path, "w") as f:
        f.write(content)

    print(f"Generated {len(events)} events across {days_generated} weekdays")
    print(f"Saved to: {output_path}")
    print("Double-click the file (or File > Import in Apple Calendar) to add to your calendar.")


def main():
    parser = argparse.ArgumentParser(description="Generate a daily planning .ics for Apple Calendar")
    parser.add_argument("--date", default=str(date.today()),
                        help="Start date YYYY-MM-DD (default: today)")
    parser.add_argument("--days", type=int, default=5,
                        help="Number of weekdays to generate (default: 5)")
    parser.add_argument("--output", default="daily_tracker.ics",
                        help="Output .ics file path (default: daily_tracker.ics)")
    args = parser.parse_args()

    start = date.fromisoformat(args.date)
    generate_ics(start, args.days, args.output)


if __name__ == "__main__":
    main()
