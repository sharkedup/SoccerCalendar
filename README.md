# Soccer Calendar Checker

Compares two published soccer calendar feeds (.ics) daily, flags mismatches, and sends email reports. Includes a web dashboard and settings UI.

## What it does

- Reads two .ics feeds directly over HTTPS (club and league) and matches upcoming games
- Ignores any event with "practice" in the title
- Pairs games by date first, then compares kickoff time and location (street name + town)
- Reports how many games each feed contains, so an empty feed can never read as "all clear"
- Sends a daily email summary (all clear or list of issues)
- Runs a web dashboard to view results and change settings without editing code
- Runs continuously as a background process, auto-starts on login

---

## Mac Mini Setup

### 1. Prerequisites

Make sure the Mac Mini has:
- **Python 3** — check with `python3 --version`. If missing: `brew install python3`
- **The two calendar feed URLs** — in Calendar.app, right-click each calendar › Get Info › copy the "Subscribed to" URL. The app reads these URLs directly; it does not use Apple Calendar, so no Calendar permission is needed.
- **Auto-login enabled** — System Settings → Users & Groups → Automatically log in as (required for LaunchAgents to fire at boot)

### 2. Clone the repo

SSH into the Mac Mini, then:

```bash
git clone https://github.com/YOUR_USERNAME/SoccerCalendar.git ~/SoccerCalendar
cd ~/SoccerCalendar
```

### 3. Run setup

```bash
bash setup.sh
```

This will:
- Install Python dependencies (`flask`, `schedule`)
- Create `config.json` from the example
- Install a launchd agent that auto-starts the app on login and keeps it running

### 4. Configure

Open the dashboard in a browser (from your MacBook, replace `mac-mini.local` with your Mac Mini's hostname or IP):

```
http://mac-mini.local:8080/config
```

Fill in:
- Calendar display names, and the .ics feed URL for each
- Gmail address and App Password
- Daily check time (default 7:00 AM)

### 5. Run a manual check

Click **Run Check Now** on the dashboard, or visit:

```
http://mac-mini.local:8080
```

---

## Finding your Mac Mini's address

On the Mac Mini, run:

```bash
hostname
```

Or check System Settings → General → Sharing — the local hostname is shown there (e.g. `kevins-mac-mini.local`).

---

## Logs

```bash
tail -f ~/SoccerCalendar/soccer_calendar.log
```

---

## Gmail App Password

Generate at: https://myaccount.google.com/apppasswords  
Create one named "Soccer Calendar Checker" and paste it into Settings.

---

## Pushing to GitHub (from MacBook)

```bash
cd /Users/kbookair/0-Projects/SoccerCalendar
git remote add origin https://github.com/YOUR_USERNAME/SoccerCalendar.git
git push -u origin main
```

Then on the Mac Mini, clone as shown in Step 2 above.
