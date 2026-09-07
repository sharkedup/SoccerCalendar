from __future__ import annotations

import re
import ssl
import smtplib
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from zoneinfo import ZoneInfo

# ── iCalendar feed reading ────────────────────────────────────────────────────
#
# The team calendars are published as .ics feeds (PlayMetrics for the club side,
# SportsEngine for the league side). We read those URLs directly rather than
# going through Apple Calendar: the Mac's copy is a subscription synced via
# iCloud, and a stale sync is invisible — it looks exactly like "no games".

FETCH_TIMEOUT = 30
USER_AGENT = "SoccerCalendarChecker/2.0"

_LOCAL_TZ = ZoneInfo("America/New_York")


def fetch_feed(url: str) -> str:
    if url.startswith("http://"):
        url = "https://" + url[len("http://"):]
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _unfold(text: str) -> list[str]:
    """RFC 5545 line unfolding: a leading space/tab continues the previous line."""
    lines: list[str] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    return lines


def _unescape(value: str) -> str:
    out = value.replace("\\n", "\n").replace("\\N", "\n")
    out = re.sub(r"\\([,;\\])", r"\1", out)
    return out.strip()


def _parse_dt(value: str, params: dict) -> tuple[datetime, bool]:
    """Return (local naive datetime, is_all_day)."""
    value = value.strip()
    if params.get("VALUE") == "DATE" or re.fullmatch(r"\d{8}", value):
        return datetime.strptime(value[:8], "%Y%m%d"), True

    stamp = datetime.strptime(value[:15], "%Y%m%dT%H%M%S")
    if value.endswith("Z"):
        stamp = stamp.replace(tzinfo=ZoneInfo("UTC")).astimezone(_LOCAL_TZ).replace(tzinfo=None)
    elif params.get("TZID"):
        try:
            src = ZoneInfo(params["TZID"])
        except Exception:
            return stamp, False
        stamp = stamp.replace(tzinfo=src).astimezone(_LOCAL_TZ).replace(tzinfo=None)
    return stamp, False


def parse_ics(text: str) -> list[dict]:
    """Parse VEVENTs out of an iCalendar feed.

    Deliberately narrow: these feeds publish one VEVENT per session with no
    recurrence. Anything carrying an RRULE is returned flagged rather than
    silently expanded (or silently dropped), so it shows up in the report.
    """
    events: list[dict] = []
    current: dict | None = None
    in_timezone = False

    for line in _unfold(text):
        if line == "BEGIN:VTIMEZONE":
            in_timezone = True
            continue
        if line == "END:VTIMEZONE":
            in_timezone = False
            continue
        if in_timezone:
            continue

        if line == "BEGIN:VEVENT":
            current = {"name": "", "location": "", "start": None, "all_day": False,
                       "recurring": False, "uid": ""}
            continue
        if line == "END:VEVENT":
            if current and current["start"] is not None:
                events.append(current)
            current = None
            continue
        if current is None or ":" not in line:
            continue

        head, _, value = line.partition(":")
        parts = head.split(";")
        prop = parts[0].upper()
        params = {}
        for p in parts[1:]:
            k, _, v = p.partition("=")
            params[k.upper()] = v.strip('"')

        if prop == "SUMMARY":
            current["name"] = _unescape(value)
        elif prop == "LOCATION":
            current["location"] = _unescape(value)
        elif prop == "UID":
            current["uid"] = value.strip()
        elif prop == "RRULE":
            current["recurring"] = True
        elif prop == "DTSTART":
            try:
                current["start"], current["all_day"] = _parse_dt(value, params)
            except ValueError:
                current["start"] = None

    return events


def get_events(url: str, months_ahead: int = 6) -> dict:
    """Fetch one feed and split it into games, practices and skipped events."""
    raw = fetch_feed(url)
    if "BEGIN:VCALENDAR" not in raw:
        raise ValueError("response was not an iCalendar feed (check the URL)")

    window_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    window_end = window_start + timedelta(days=30 * months_ahead)

    games, practices, recurring = [], [], []
    for event in parse_ics(raw):
        if not (window_start <= event["start"] <= window_end):
            continue
        if event["recurring"]:
            recurring.append(event)
            continue
        if "practice" in event["name"].lower():
            practices.append(event)
            continue
        games.append(event)

    games.sort(key=lambda e: e["start"])
    practices.sort(key=lambda e: e["start"])
    return {
        "games": games,
        "practices": practices,
        "practice_count": len(practices),
        "recurring_skipped": len(recurring),
    }


# ── Location comparison ───────────────────────────────────────────────────────

_STREET_SUFFIX = re.compile(
    r"\b(rd|road|ave|avenue|st|street|blvd|boulevard|dr|drive|ln|lane|ct|court|way|pl|place)\b",
    re.IGNORECASE,
)
_STATE = re.compile(r"\bNJ\b|\bNew Jersey\b", re.IGNORECASE)


def _parse_location(loc: str) -> tuple[str, str]:
    parts = [p.strip() for p in loc.split(",")]
    street_core = ""
    town = ""
    for i, part in enumerate(parts):
        if _STREET_SUFFIX.search(part):
            cleaned = re.sub(r"^\d+\s+", "", part)
            cleaned = _STREET_SUFFIX.sub("", cleaned)
            street_core = " ".join(cleaned.split()).lower()
        if i + 1 < len(parts) and _STATE.search(parts[i + 1]):
            town = part.lower().strip()
    return street_core, town


def _locations_match(loc1: str, loc2: str) -> bool:
    if loc1.lower() == loc2.lower():
        return True
    s1, t1 = _parse_location(loc1)
    s2, t2 = _parse_location(loc2)
    if not (s1 and t1 and s2 and t2):
        return loc1.lower() == loc2.lower()
    return s1 == s2 and t1 == t2


# ── Game comparison ───────────────────────────────────────────────────────────

def _is_cancelled(event: dict) -> bool:
    return event["name"].strip().upper().startswith("CANCELED") or \
           event["name"].strip().upper().startswith("CANCELLED")


def _is_tbd(event: dict) -> bool:
    """A placeholder game: time not set yet, or location literally 'TBD'."""
    if event["location"].strip().upper() in ("", "TBD"):
        return True
    return event["start"].hour == 0 and event["start"].minute == 0


def _time_of(event: dict | None) -> tuple:
    return (event["start"].hour, event["start"].minute) if event else ()


def _row(day, pri: dict | None, sec: dict | None) -> dict:
    if pri and sec:
        if _is_cancelled(pri) or _is_cancelled(sec):
            status = "cancelled"
        elif _is_tbd(pri) or _is_tbd(sec):
            status = "tbd"
        elif _time_of(pri) != _time_of(sec):
            status = "time_mismatch"
        elif not _locations_match(pri["location"], sec["location"]):
            status = "location_mismatch"
        else:
            status = "ok"
    elif pri:
        status = "cancelled" if _is_cancelled(pri) else "missing_secondary"
    else:
        status = "cancelled" if _is_cancelled(sec) else "missing_primary"

    shown = pri or sec
    return {
        "date": day.isoformat(),
        "time": shown["start"].strftime("%H:%M"),
        "primary": _event_dict(pri),
        "secondary": _event_dict(sec),
        "status": status,
    }


def _event_dict(event: dict | None) -> dict | None:
    if event is None:
        return None
    return {
        "name": event["name"],
        "location": event["location"],
        "time": event["start"].strftime("%H:%M"),
    }


def compare_games(primary: list[dict], secondary: list[dict]) -> list[dict]:
    """Pair games by date, then by time within the date.

    Pairing on the date first is what lets a kickoff-time disagreement surface
    as one 'time mismatch' row instead of two unrelated 'missing game' rows.
    """
    by_date: dict = defaultdict(lambda: {"p": [], "s": []})
    for event in primary:
        by_date[event["start"].date()]["p"].append(event)
    for event in secondary:
        by_date[event["start"].date()]["s"].append(event)

    rows = []
    for day in sorted(by_date):
        pri = sorted(by_date[day]["p"], key=lambda e: e["start"])
        sec = sorted(by_date[day]["s"], key=lambda e: e["start"])

        matched_sec: set = set()
        unmatched_pri = []
        for pg in pri:
            hit = None
            for i, sg in enumerate(sec):
                if i not in matched_sec and _time_of(sg) == _time_of(pg):
                    hit = i
                    break
            if hit is None:
                unmatched_pri.append(pg)
            else:
                matched_sec.add(hit)
                rows.append(_row(day, pg, sec[hit]))

        unmatched_sec = [sg for i, sg in enumerate(sec) if i not in matched_sec]

        # Same day, no exact time match: treat as the same game at a different
        # time rather than two separate missing games.
        while unmatched_pri and unmatched_sec:
            rows.append(_row(day, unmatched_pri.pop(0), unmatched_sec.pop(0)))
        for pg in unmatched_pri:
            rows.append(_row(day, pg, None))
        for sg in unmatched_sec:
            rows.append(_row(day, None, sg))

    rows.sort(key=lambda r: (r["date"], r["time"]))
    return rows


def _next_practice(feeds: dict, labels: dict) -> dict | None:
    now = datetime.now()
    upcoming = []
    for which, feed in feeds.items():
        for event in feed["practices"]:
            if event["start"] >= now:
                upcoming.append((event, labels[which]))
    if not upcoming:
        return None
    event, label = min(upcoming, key=lambda pair: pair[0]["start"])
    return {
        "date": event["start"].date().isoformat(),
        "time": event["start"].strftime("%H:%M"),
        "name": event["name"],
        "location": event["location"],
        "feed": label,
    }


def _next_game(rows: list[dict]) -> dict | None:
    """First compared game still ahead of us, with its match status."""
    now = datetime.now()
    for row in rows:
        when = datetime.strptime(f"{row['date']} {row['time']}", "%Y-%m-%d %H:%M")
        if when < now or row["status"] == "cancelled":
            continue
        shown = row["primary"] or row["secondary"]
        return {
            "date": row["date"],
            "time": row["time"],
            "name": shown["name"],
            "location": shown["location"],
            "status": row["status"],
        }
    return None


def _feed(config: dict, which: str) -> tuple[str, str]:
    """Return (label, url) for a configured feed."""
    entry = config["calendars"][which]
    if isinstance(entry, str):  # pre-2.0 config: calendar name, no URL
        return entry, ""
    return entry.get("label", which), entry.get("url", "")


def run_check(config: dict) -> dict:
    """Run the full comparison. Returns a result dict."""
    pri_label, pri_url = _feed(config, "primary")
    sec_label, sec_url = _feed(config, "secondary")

    result = {
        "timestamp": datetime.now().isoformat(),
        "primary_label": pri_label,
        "secondary_label": sec_label,
        "games": [],
    }

    feeds = {}
    for which, label, url in (("primary", pri_label, pri_url), ("secondary", sec_label, sec_url)):
        if not url:
            result.update(status="error",
                          error=f"No feed URL configured for '{label}'. Add it in Settings.")
            return result
        try:
            feeds[which] = get_events(url)
        except (urllib.error.URLError, urllib.error.HTTPError, ValueError, OSError) as e:
            result.update(status="error", error=f"Could not read '{label}': {e}")
            return result

    pri_games = feeds["primary"]["games"]
    sec_games = feeds["secondary"]["games"]

    labels = {"primary": pri_label, "secondary": sec_label}
    result["next_practice"] = _next_practice(feeds, labels)

    result["summary"] = {
        "primary_games": len(pri_games),
        "secondary_games": len(sec_games),
        "primary_practices": feeds["primary"]["practice_count"],
        "secondary_practices": feeds["secondary"]["practice_count"],
        "recurring_skipped": feeds["primary"]["recurring_skipped"] + feeds["secondary"]["recurring_skipped"],
        "last_game": max([g["start"] for g in pri_games + sec_games]).date().isoformat()
                     if (pri_games or sec_games) else None,
    }

    # An empty feed is reported on its own terms. Comparing against nothing
    # produced three weeks of "all games match" over an empty dataset, and one
    # side having published no games yet is not the same as N missing games.
    if not pri_games and not sec_games:
        result.update(status="no_games", games=[], next_game=None)
        return result
    if not pri_games or not sec_games:
        empty = pri_label if not pri_games else sec_label
        other = sec_label if not pri_games else pri_label
        count = len(sec_games) if not pri_games else len(pri_games)
        rows = compare_games(pri_games, sec_games)
        result.update(
            status="one_sided",
            error=f"No games published on '{empty}' yet — '{other}' has {count}.",
            games=rows,
            next_game=_next_game(rows),
        )
        return result

    games = compare_games(pri_games, sec_games)
    issues = [g for g in games if g["status"] not in ("ok", "cancelled")]
    result.update(status="issues" if issues else "ok", games=games, next_game=_next_game(games))
    return result


# ── Email ─────────────────────────────────────────────────────────────────────

_STATUS_LABEL = {
    "time_mismatch": "TIME MISMATCH",
    "location_mismatch": "LOCATION MISMATCH",
    "missing_primary": "MISSING FROM",
    "missing_secondary": "MISSING FROM",
    "tbd": "TIME/LOCATION TBD",
    "cancelled": "CANCELLED",
}


def _summary_lines(result: dict) -> list[str]:
    s = result.get("summary")
    if not s:
        return []
    lines = [
        "Feed contents:",
        f"  {result['primary_label']}: {s['primary_games']} game(s), {s['primary_practices']} practice(s)",
        f"  {result['secondary_label']}: {s['secondary_games']} game(s), {s['secondary_practices']} practice(s)",
    ]
    if s.get("last_game"):
        lines.append(f"  Last scheduled game: {s['last_game']}")
    if s.get("recurring_skipped"):
        lines.append(f"  NOTE: {s['recurring_skipped']} recurring event(s) skipped — not supported.")
    return lines + [""]


def _issue_lines(result: dict, issues: list) -> list[str]:
    pri = result.get("primary_label", "primary")
    sec = result.get("secondary_label", "secondary")
    lines: list[str] = []
    for g in issues:
        when = f"{g['date']} {g['time']}"
        label = _STATUS_LABEL.get(g["status"], g["status"].upper())
        if g["status"] == "missing_primary":
            lines.append(f"{label} {pri} — {when}")
            lines.append(f"  {sec}: {g['secondary']['name']} | {g['secondary']['location']}")
        elif g["status"] == "missing_secondary":
            lines.append(f"{label} {sec} — {when}")
            lines.append(f"  {pri}: {g['primary']['name']} | {g['primary']['location']}")
        elif g["status"] == "time_mismatch":
            lines.append(f"{label} — {g['date']}")
            lines.append(f"  {pri}: {g['primary']['time']} | {g['primary']['name']}")
            lines.append(f"  {sec}: {g['secondary']['time']} | {g['secondary']['name']}")
        else:
            lines.append(f"{label} — {when}")
            lines.append(f"  {pri}: {g['primary']['location'] if g['primary'] else '—'}")
            lines.append(f"  {sec}: {g['secondary']['location'] if g['secondary'] else '—'}")
        lines.append("")
    return lines


def build_email(result: dict, config: dict) -> tuple[str, str]:
    pri = result.get("primary_label", "primary")
    sec = result.get("secondary_label", "secondary")
    games = result.get("games", [])
    issues = [g for g in games if g["status"] not in ("ok", "cancelled")]

    if result["status"] == "error":
        subject = "[Soccer] ERROR — calendar check failed"
        body = f"Error: {result.get('error', 'Unknown error')}"
        return subject, body

    if result["status"] == "no_games":
        subject = "[Soccer] No games on either feed — nothing compared"
        body = "\n".join(
            [f"Neither '{pri}' nor '{sec}' has any games in the next 6 months.",
             "Nothing was compared, so this is NOT an all-clear.", ""] + _summary_lines(result))
        return subject, body

    if result["status"] == "one_sided":
        subject = f"[Soccer] {len(issues)} game(s) on one feed only — action needed"
        body = "\n".join(
            [result.get("error", ""),
             "",
             "Every game below is listed individually — nothing could be",
             "cross-checked until both feeds have games.", ""]
            + _summary_lines(result) + _issue_lines(result, issues))
        return subject, body

    if not issues:
        subject = f"[Soccer] All {len(games)} games match ✓"
        body = "\n".join(
            [f"All upcoming games on '{pri}' and '{sec}' are consistent.", "", "No action needed.", ""]
            + _summary_lines(result))
        return subject, body

    subject = f"[Soccer] {len(issues)} mismatch(es) found — action needed"
    lines = [f"{len(issues)} issue(s) found.", ""] + _summary_lines(result)
    return subject, "\n".join(lines + _issue_lines(result, issues))


def send_email(result: dict, config: dict):
    cfg = config["email"]
    addr = cfg["address"]
    pw = cfg["gmail_app_password"]
    subject, body = build_email(result, config)

    msg = MIMEMultipart()
    msg["From"] = addr
    msg["To"] = addr
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    ctx = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx) as server:
        server.login(addr, pw)
        server.sendmail(addr, addr, msg.as_string())
