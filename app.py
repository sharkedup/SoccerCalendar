#!/usr/bin/env python3
"""
Soccer Calendar Checker — web app + scheduler.
Run this once; it handles its own daily schedule and serves the dashboard.
"""

import json
import logging
import threading
from datetime import datetime
from pathlib import Path

import schedule
import time
from flask import Flask, render_template, request, redirect, url_for, flash

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
MAX_HISTORY   = 30


def load_history() -> list:
    if HISTORY_PATH.exists():
        return json.loads(HISTORY_PATH.read_text())
    return []


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


@app.route("/")
def index():
    history = load_history()
    latest  = history[0] if history else None
    config  = load_config()
    return render_template("index.html", latest=latest, history=history, config=config)


@app.route("/run", methods=["POST"])
def manual_run():
    log.info("Manual check triggered from web UI.")
    scheduled_check()
    flash("Check complete.", "success")
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

    log.info(f"Starting web server on http://0.0.0.0:{port}")
    app.run(host="0.0.0.0", port=port, debug=False)
