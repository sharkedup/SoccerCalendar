from __future__ import annotations

import re
import ssl
import smtplib
import subprocess
from datetime import datetime, date
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

APPLESCRIPT = """\
tell application "Calendar"
    set output to ""
    set theCalendar to first calendar whose name is "{CAL}"
    set todayDate to current date
    set time of todayDate to 0
    set upcomingEvents to every event of theCalendar whose start date >= todayDate
    repeat with e in upcomingEvents
        set eName to summary of e
        set eStart to start date of e
        set eLoc to ""
        try
            set eLoc to location of e
        end try
        set yr  to year of eStart as string
        set mo  to month of eStart as integer
        set dy  to day of eStart
        set hr  to hours of eStart
        set mi  to minutes of eStart
        set moStr to mo as string
        set dyStr to dy as string
        set hrStr to hr as string
        set miStr to mi as string
        if mo < 10 then set moStr to "0" & moStr
        if dy < 10 then set dyStr to "0" & dyStr
        if hr < 10 then set hrStr to "0" & hrStr
        if mi < 10 then set miStr to "0" & miStr
        set datePart to yr & "-" & moStr & "-" & dyStr & " " & hrStr & ":" & miStr
        set output to output & eName & (ASCII character 9) & datePart & (ASCII character 9) & eLoc & (ASCII character 10)
    end repeat
    return output
end tell
"""

_STREET_SUFFIX = re.compile(
    r"\b(rd|road|ave|avenue|st|street|blvd|boulevard|dr|drive|ln|lane|ct|court|way|pl|place)\b",
    re.IGNORECASE,
)
_STATE = re.compile(r"\bNJ\b|\bNew Jersey\b", re.IGNORECASE)


def get_events(calendar_name: str) -> list[dict]:
    script = APPLESCRIPT.replace("{CAL}", calendar_name)
    result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"Could not read '{calendar_name}': {result.stderr.strip()}")

    events = []
    for line in result.stdout.strip().splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        name     = parts[0].strip()
        start_s  = parts[1].strip()
        location = parts[2].strip() if len(parts) > 2 else ""

        if "practice" in name.lower():
            continue

        try:
            start_dt = datetime.strptime(start_s, "%Y-%m-%d %H:%M")
        except ValueError:
            continue

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
    except RuntimeError as e:
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
