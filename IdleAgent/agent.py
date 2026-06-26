"""
Network Idle Monitor - Python Agent
Checks if screen is locked and reports idle/active to dashboard.
Uses actual elapsed wall-clock time for accuracy.
"""

import os
import time
import socket
import json
import subprocess
from datetime import datetime, date

# ── Config ──────────────────────────────────────────────────
DASHBOARD_URL = "http://10.10.10.107:12001/report"  # <-- your server IP
# ────────────────────────────────────────────────────────────

BASE_DIR   = r"C:\IdleAgent"
LOG_FILE   = os.path.join(BASE_DIR, "agent.log")
STATE_FILE = os.path.join(BASE_DIR, "state.json")

os.makedirs(BASE_DIR, exist_ok=True)


def log(msg):
    line = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "unknown"


def fmt_duration(seconds):
    seconds = max(0, int(seconds))
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    if h:   return f"{h}h {m}m {s}s"
    elif m: return f"{m}m {s}s"
    else:   return f"{s}s"


def is_screen_locked():
    # Primary: check if LogonUI.exe is running (lock screen / login screen)
    number_of_lock_screens = 0
    try:
        result = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq LogonUI.exe"],
            capture_output=True, text=True, timeout=5
        )
        for line in result.stdout.splitlines():
            if "LogonUI.exe" in line:
                number_of_lock_screens += 1
    except Exception:
        pass
    # Backup: check if no active console session exists
    try:
        result = subprocess.run(
            ["query", "user"],
            capture_output=True, text=True, timeout=5
        )
        active = [l for l in result.stdout.splitlines() if "Active" in l]
        number_of_users = -1
        for line in result.stdout.splitlines():
            number_of_users += 1
        if len(active) == 0:
            return True
        if number_of_users == number_of_lock_screens:
            return True
    except Exception:
        pass

    return False


def load_state():
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, "r") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def save_state(state):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        log(f"[ERROR] Could not save state: {e}")


def main():
    now        = datetime.now()
    today      = now.strftime("%Y-%m-%d")
    now_ts     = time.time()

    state      = load_state()

    # ── Reset daily counter at midnight ──────────────────────
    if state.get("date") != today:
        log(f"[INFO] New day {today} - resetting daily counter")
        # If screen was already locked across midnight, preserve the idle
        # session start as NOW (start of new day) so current idle timer
        # counts from midnight, not from yesterday. Daily counter resets to 0.
        was_locked_across_midnight = state.get("was_idle", False)
        state = {
            "date":           today,
            "daily_idle_sec": 0,
            "idle_since_ts":  now_ts if was_locked_across_midnight else None,
            "last_check_ts":  now_ts,
            "was_idle":       was_locked_across_midnight
        }
        save_state(state)

    locked          = is_screen_locked()
    last_check_ts   = state.get("last_check_ts", now_ts)
    elapsed_sec     = now_ts - last_check_ts   # actual time since last run
    daily_idle_sec  = state.get("daily_idle_sec", 0)
    idle_since_ts   = state.get("idle_since_ts", None)
    was_idle        = state.get("was_idle", False)

    # Sanity check: elapsed time should not be more than 10 minutes
    # (covers first run, reboots, or task skipping)
    # Cap at 600s to avoid huge jumps on first run after reboot
    elapsed_sec = max(0, min(elapsed_sec, 600))

    # Sanity check: daily_idle_sec can never exceed 24 hours (86400s)
    # This prevents corrupt state files from inflating totals
    daily_idle_sec = max(0, min(daily_idle_sec, 86400))

    # ── Calculate idle/active ────────────────────────────────
    if locked:
        status = "idle"
        if not was_idle:
            # Just became idle now — record when it started
            idle_since_ts = now_ts
            log(f"[IDLE] Screen locked at {now.strftime('%H:%M:%S')}")

        # Add actual elapsed time (not fixed 60s) to daily total
        daily_idle_sec += elapsed_sec

        # Current idle = how long screen has been locked this session
        current_idle_sec = int(now_ts - idle_since_ts) if idle_since_ts else int(elapsed_sec)
        current_idle_dur = fmt_duration(current_idle_sec)
        idle_since_str   = datetime.fromtimestamp(idle_since_ts).strftime("%H:%M:%S") if idle_since_ts else now.strftime("%H:%M:%S")

    else:
        status = "active"
        if was_idle:
            log(f"[ACTIVE] Screen unlocked at {now.strftime('%H:%M:%S')} | daily idle: {fmt_duration(daily_idle_sec)}")
        idle_since_ts    = None
        current_idle_sec = 0
        current_idle_dur = "0s"
        idle_since_str   = None

    # ── Save updated state ───────────────────────────────────
    state = {
        "date":           today,
        "daily_idle_sec": daily_idle_sec,
        "idle_since_ts":  idle_since_ts,
        "last_check_ts":  now_ts,
        "was_idle":       locked
    }
    save_state(state)

    # ── Build and send payload ───────────────────────────────
    payload = {
        "hostname":         socket.gethostname(),
        "ip":               get_local_ip(),
        "status":           status,
        "current_idle_sec": current_idle_sec,
        "current_idle_dur": current_idle_dur,
        "idle_since":       idle_since_str,
        "daily_idle_sec":   int(daily_idle_sec),
        "daily_idle_dur":   fmt_duration(daily_idle_sec),
        "report_date":      today,
        "reported_at":      now.isoformat(),
    }

    try:
        import requests
        r = requests.post(DASHBOARD_URL, json=payload, timeout=10)
        if r.status_code == 200:
            log(f"[SENT] status={status} | current={current_idle_dur} | today={fmt_duration(daily_idle_sec)}")
        else:
            log(f"[WARN] Server returned HTTP {r.status_code}")
    except Exception as e:
        log(f"[ERROR] Cannot reach server: {e}")


if __name__ == "__main__":
    main()
