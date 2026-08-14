from __future__ import annotations

import re
import ssl
import time
import smtplib
from datetime import datetime, date, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import EventKit
from Foundation import NSDate

_store = EventKit.EKEventStore.alloc().init()


def _ensure_calendar_access():
    status = EventKit.EKEventStore.authorizationStatusForEntityType_(EventKit.EKEntityTypeEvent)
    if status == EventKit.EKAuthorizationStatusFullAccess:
        return

    granted = [False]
    responded = [False]

    def completion_handler(success, error):
        granted[0] = success
        responded[0] = True

    # macOS 14+ splits calendar access into full vs. write-only; the older
    # requestAccessToEntityType:completion: only ever grants write-only here,
    # so full read access requires the newer full-access request explicitly.
    if hasattr(_store, "requestFullAccessToEventsWithCompletion_"):
        _store.requestFullAccessToEventsWithCompletion_(completion_handler)
    else:
        _store.requestAccessToEntityType_completion_(EventKit.EKEntityTypeEvent, completion_handler)

    for _ in range(300):  # wait up to 30s for the user to respond to the system prompt
        if responded[0]:
            break
        time.sleep(0.1)

    if not granted[0]:
        raise PermissionError(
            "Calendar access not granted. Please allow access in "
            "System Settings > Privacy & Security > Calendars"
        )


def _find_calendar(calendar_name: str):
    calendars = _store.calendarsForEntityType_(EventKit.EKEntityTypeEvent)
    for cal in calendars:
        if cal.title().lower() == calendar_name.lower():
            return cal

    available = ", ".join(cal.title() for cal in calendars)
    raise RuntimeError(f"Could not read '{calendar_name}': calendar not found. Available: {available}")


_STREET_SUFFIX = re.compile(
    r"\b(rd|road|ave|avenue|st|street|blvd|boulevard|dr|drive|ln|lane|ct|court|way|pl|place)\b",
    re.IGNORECASE,
)
_STATE = re.compile(r"\bNJ\b|\bNew Jersey\b", re.IGNORECASE)


def get_events(calendar_name: str, months_ahead: int = 6) -> list[dict]:
    _ensure_calendar_access()
    calendar = _find_calendar(calendar_name)

    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    end = today_start + timedelta(days=30 * months_ahead)

    ns_start = NSDate.dateWithTimeIntervalSince1970_(today_start.timestamp())
    ns_end   = NSDate.dateWithTimeIntervalSince1970_(end.timestamp())

    predicate  = _store.predicateForEventsWithStartDate_endDate_calendars_(ns_start, ns_end, [calendar])
    ek_events  = _store.eventsMatchingPredicate_(predicate)

    events = []
    for e in ek_events:
        name = (e.title() or "").strip()
        if "practice" in name.lower():
            continue

        start_dt = datetime.fromtimestamp(e.startDate().timeIntervalSince1970())
        location = (e.location() or "").strip()

        events.append({"name": name, "start": start_dt, "location": location})

    return events


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


def game_key(event: dict) -> tuple:
    s = event["start"]
    return (s.date(), s.hour, s.minute)


def run_check(config: dict) -> dict:
    """Run the full calendar comparison. Returns a result dict."""
    primary_cal   = config["calendars"]["primary"]
    secondary_cal = config["calendars"]["secondary"]

    try:
        primary   = get_events(primary_cal)
        secondary = get_events(secondary_cal)
    except (RuntimeError, PermissionError) as e:
        return {
            "timestamp": datetime.now().isoformat(),
            "status": "error",
            "error": str(e),
            "games": []
        }

    pri_map = {game_key(e): e for e in primary}
    sec_map = {game_key(e): e for e in secondary}
    all_keys = sorted(set(pri_map) | set(sec_map))

    games = []
    for k in all_keys:
        pri_event = pri_map.get(k)
        sec_event = sec_map.get(k)

        if pri_event and sec_event:
            if _locations_match(pri_event["location"], sec_event["location"]):
                match_status = "ok"
            else:
                match_status = "location_mismatch"
        elif pri_event:
            match_status = "missing_secondary"
        else:
            match_status = "missing_primary"

        games.append({
            "date":    k[0].isoformat(),
            "time":    f"{k[1]:02d}:{k[2]:02d}",
            "primary":   _event_dict(pri_event),
            "secondary": _event_dict(sec_event),
            "status":  match_status
        })

    issues    = [g for g in games if g["status"] != "ok"]
    status    = "issues" if issues else "ok"

    return {
        "timestamp": datetime.now().isoformat(),
        "status":    status,
        "games":     games
    }


def _event_dict(event: dict | None) -> dict | None:
    if event is None:
        return None
    return {
        "name":     event["name"],
        "location": event["location"],
    }


def send_email(result: dict, config: dict):
    cfg   = config["email"]
    addr  = cfg["address"]
    pw    = cfg["gmail_app_password"]
    games = result["games"]
    issues = [g for g in games if g["status"] != "ok"]

    if result["status"] == "error":
        subject = "[Soccer] ERROR — calendar check failed"
        body    = f"Error: {result.get('error', 'Unknown error')}"
    elif not issues:
        subject = "[Soccer] All games match ✓"
        body    = (
            f"All upcoming games on {config['calendars']['primary']} and "
            f"{config['calendars']['secondary']} are consistent.\n\nNo action needed."
        )
    else:
        subject = f"[Soccer] {len(issues)} mismatch(es) found — action needed"
        lines   = [f"{len(issues)} issue(s) found.\n"]
        for g in issues:
            dt = f"{g['date']} {g['time']}"
            if g["status"] == "location_mismatch":
                lines.append(f"LOCATION MISMATCH — {dt}")
                lines.append(f"  {config['calendars']['primary']}: {g['primary']['location']}")
                lines.append(f"  {config['calendars']['secondary']}: {g['secondary']['location']}")
            elif g["status"] == "missing_secondary":
                lines.append(f"MISSING FROM {config['calendars']['secondary']} — {dt}")
                lines.append(f"  {g['primary']['name']} | {g['primary']['location']}")
            elif g["status"] == "missing_primary":
                lines.append(f"MISSING FROM {config['calendars']['primary']} — {dt}")
                lines.append(f"  {g['secondary']['name']} | {g['secondary']['location']}")
            lines.append("")
        body = "\n".join(lines)

    msg = MIMEMultipart()
    msg["From"]    = addr
    msg["To"]      = addr
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    ctx = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx) as server:
        server.login(addr, pw)
        server.sendmail(addr, addr, msg.as_string())
