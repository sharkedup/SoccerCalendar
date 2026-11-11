#!/usr/bin/env python3
"""
Soccer Calendar Checker — web app + scheduler.
Run this once; it handles its own daily schedule and serves the dashboard.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import urllib.parse
from datetime import date, datetime
from pathlib import Path

import schedule
import time
from flask import Flask, render_template, request, redirect, send_from_directory, url_for, flash

from config_manager import load_config, save_config
from checker import run_check, send_email

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ── History ───────────────────────────────────────────────────────────────────
HISTORY_PATH  = Path(__file__).parent / "history.json"
LATEST_PATH   = Path(__file__).parent / "latest.json"
MAX_HISTORY   = 30

# How stale the cached result may be before loading the dashboard re-checks.
FRESH_FOR_MINUTES = 15

_refresh_lock = threading.Lock()


def load_history() -> list:
    if HISTORY_PATH.exists():
        return json.loads(HISTORY_PATH.read_text())
    return []


def load_latest() -> dict | None:
    if LATEST_PATH.exists():
        try:
            return json.loads(LATEST_PATH.read_text())
        except json.JSONDecodeError:
            return None
    return None


def save_latest(result: dict):
    LATEST_PATH.write_text(json.dumps(result, indent=2))


def refresh_latest(max_age_minutes: int = 0) -> dict:
    """Re-run the comparison for the dashboard. No email, no history entry.

    History stays a record of the scheduled daily runs; on-demand page loads
    would otherwise churn through it.
    """
    with _refresh_lock:
        latest = load_latest()
        if latest and max_age_minutes:
            try:
                age = datetime.now() - datetime.fromisoformat(latest["timestamp"])
                if age.total_seconds() < max_age_minutes * 60:
                    return latest
            except (ValueError, KeyError):
                pass
        result = run_check(load_config())
        save_latest(result)
        return result


def save_history(entry: dict):
    history = load_history()
    history.insert(0, entry)
    HISTORY_PATH.write_text(json.dumps(history[:MAX_HISTORY], indent=2))


# ── Scheduled job ─────────────────────────────────────────────────────────────
def scheduled_check():
    config = load_config()
    log.info("Running scheduled calendar check…")
    result = run_check(config)
    save_history(result)
    save_latest(result)

    email_cfg = config["email"]
    if email_cfg.get("gmail_app_password") and email_cfg.get("address"):
        has_issues = result["status"] != "ok"
        if email_cfg.get("always_send") or has_issues:
            try:
                send_email(result, config)
                log.info("Email sent.")
            except Exception as e:
                log.error(f"Email failed: {e}")
    else:
        log.info("Email not configured — skipping.")

    log.info(f"Check complete — status: {result['status']}")


def startup_check():
    """Check once on boot, unless today's scheduled run already happened."""
    try:
        history = load_history()
        today = datetime.now().date().isoformat()
        if any(e.get("timestamp", "").startswith(today) for e in history):
            log.info("Startup check skipped — already ran today.")
            return
        log.info("Running startup calendar check…")
        scheduled_check()
    except Exception as e:
        log.error(f"Startup check failed: {e}")


def run_scheduler():
    config = load_config()
    check_time = config["schedule"].get("check_time", "07:00")
    schedule.every().day.at(check_time).do(scheduled_check)
    log.info(f"Scheduler started — daily check at {check_time}")
    while True:
        schedule.run_pending()
        time.sleep(30)


# ── Flask app ─────────────────────────────────────────────────────────────────
app = Flask(__name__)
app.secret_key = "soccer-calendar-checker-secret"


# ── Template filters ──────────────────────────────────────────────────────────
_MATCHUP = re.compile(r"\b(at|vs\.?|v)\b", re.IGNORECASE)


def _as_date(value):
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


@app.template_filter("pretty_date")
def pretty_date(value) -> str:
    d = _as_date(value)
    if not d:
        return value
    return d.strftime("%a, %b %-d")


@app.template_filter("pretty_time")
def pretty_time(value) -> str:
    """'15:30' → '3:30 PM'. Midnight is the feeds' stand-in for 'not set yet'."""
    if not value or value == "00:00":
        return "Time TBD"
    try:
        return datetime.strptime(value, "%H:%M").strftime("%-I:%M %p")
    except ValueError:
        return value


@app.template_filter("relative_day")
def relative_day(value) -> str:
    d = _as_date(value)
    if not d:
        return ""
    days = (d - datetime.now().date()).days
    if days < 0:
        return "past"
    if days == 0:
        return "today"
    if days == 1:
        return "tomorrow"
    if days < 7:
        return f"in {days} days"
    weeks = round(days / 7)
    return "in 1 week" if weeks == 1 else f"in {weeks} weeks"


@app.template_filter("maps_url")
def maps_url(location: str) -> str:
    return "https://maps.apple.com/?q=" + urllib.parse.quote(location or "")


@app.template_filter("game_title")
def game_title(row: dict) -> str:
    """The most descriptive name available for a game.

    One feed labels every game 'SPF U13G Phoenix - Game' and puts the actual
    matchup in the description; the other names the matchup directly. Prefer
    whichever candidate reads as 'A at B'.
    """
    candidates = []
    for side in ("primary", "secondary"):
        event = row.get(side)
        if not event:
            continue
        notes = event.get("notes") or []
        if notes:
            candidates.append(notes[0])
        candidates.append(event.get("name", ""))
    for candidate in candidates:
        if _MATCHUP.search(candidate):
            return candidate
    return next((c for c in candidates if c), "Game")


@app.route("/")
def index():
    latest  = refresh_latest(max_age_minutes=FRESH_FOR_MINUTES) or (load_history() or [None])[0]
    history = load_history()
    config  = load_config()
    return render_template("index.html", latest=latest, history=history, config=config)


@app.route("/favicon.ico")
def favicon():
    """Browsers ask for this by name regardless of the <link> tags."""
    return send_from_directory(app.static_folder, "apple-touch-icon.png",
                               mimetype="image/png")


@app.route("/refresh", methods=["POST"])
def manual_refresh():
    refresh_latest()
    flash("Refreshed from the calendar feeds.", "success")
    return redirect(url_for("index"))


@app.route("/run", methods=["POST"])
def manual_run():
    log.info("Manual check triggered from web UI.")
    scheduled_check()
    flash("Check complete — email sent.", "success")
    return redirect(url_for("index"))


@app.route("/config", methods=["GET", "POST"])
def config_page():
    config = load_config()

    if request.method == "POST":
        config["calendars"]["primary"] = {
            "label": request.form["primary_label"].strip(),
            "url":   request.form["primary_url"].strip(),
        }
        config["calendars"]["secondary"] = {
            "label": request.form["secondary_label"].strip(),
            "url":   request.form["secondary_url"].strip(),
        }
        config["email"]["address"]              = request.form["email_address"].strip()
        config["email"]["always_send"]          = "always_send" in request.form
        config["schedule"]["check_time"]        = request.form["check_time"].strip()

        new_pw = request.form["gmail_app_password"].strip()
        if new_pw:
            config["email"]["gmail_app_password"] = new_pw

        save_config(config)

        # Reschedule with new time
        schedule.clear()
        schedule.every().day.at(config["schedule"]["check_time"]).do(scheduled_check)

        flash("Settings saved.", "success")
        return redirect(url_for("config_page"))

    return render_template("config.html", config=config)


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    config = load_config()
    port   = config["web"].get("port", 8080)

    # Start scheduler in background thread
    t = threading.Thread(target=run_scheduler, daemon=True)
    t.start()

    # Run one check at startup so a restart past the scheduled time does not
    # silently skip the day.
    threading.Thread(target=startup_check, daemon=True).start()

    log.info(f"Starting web server on http://127.0.0.1:{port}")
    app.run(host="127.0.0.1", port=port, debug=False)
