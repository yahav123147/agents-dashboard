from __future__ import annotations

import os
import re
import sys
import json
import time
import subprocess
import urllib.request
from datetime import datetime, timezone, timedelta

import status_engine
import render

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "agents.json")
STATE_PATH = os.path.join(HERE, "manager_state.json")

# Optional WhatsApp digest. Disabled by default: when these are unset the
# manager just prints the digest to stdout, so it works out of the box with no
# bot. To enable, point AGENTS_WA_URL at a bot endpoint that accepts a JSON
# body of {"jid": ..., "text": ...} and set AGENTS_WA_JID to the target chat.
WA_URL = os.environ.get("AGENTS_WA_URL", "")
WA_JID = os.environ.get("AGENTS_WA_JID", "")

MAX_FIXES_PER_RUN = 6
# Per-agent retry budget: max kicks/day with a cooldown between attempts.
# Verification (sleep + re-check) confirms the kick actually moved the agent
# out of failure. With cooldown=30min and max=3, the worst case is 3 wasted
# attempts before escalation, far better than 1-and-done.
MAX_KICKS_PER_DAY = 3
KICK_COOLDOWN_MIN = 30
VERIFY_DELAY_SEC = 60

# A failure we must NOT try to auto-fix by re-running.
LIMIT_HINTS = ("hit your limit", "resets", "rate limit", "usage limit", "מכסת")
QUEUE_HINTS = ("queue empty", "תור הנושאים ריק", "אין נושאים")

# Transient network errors, safe to retry once even for non-auto_fix agents,
# provided no successful run happened today (so we don't burn quota).
_NETWORK_ERROR_RE = re.compile(
    r"ENOTFOUND|ETIMEDOUT|ECONNRESET|ECONNREFUSED|"
    r"Unable to connect to API|getaddrinfo|"
    r"network is unreachable|temporary failure in name resolution|"
    r"Request timed out|API Error.*timed out|connection refused|"
    r"overloaded_error|API Error: 529|API Error: 503|"
    r"upstream connect error|service unavailable",
    re.IGNORECASE,
)


def _self_label() -> str:
    return "com.example.agents-manager"


def load_state() -> dict:
    """Returns the state file, migrating the legacy `kicked: [labels]` format
    to the new `kicks: {label: [{at, verified_ok}]}` shape so old entries don't
    silently disappear."""
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            state = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    if "kicked" in state and "kicks" not in state:
        legacy = state.pop("kicked")
        date = state.get("date", "")
        ts = f"{date}T00:00:00" if date else datetime.now().isoformat()
        state["kicks"] = {label: [{"at": ts, "verified_ok": False}] for label in legacy}
    state.setdefault("kicks", {})
    return state


def save_state(state: dict) -> None:
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _parse_iso(s: str):
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


def _kicks_for(state: dict, label: str) -> list:
    return state.setdefault("kicks", {}).setdefault(label, [])


def _cooldown_remaining_min(kicks: list) -> int:
    """Minutes left before another kick is allowed. 0 means kick-ready."""
    if not kicks:
        return 0
    last = _parse_iso(kicks[-1].get("at", ""))
    if not last:
        return 0
    elapsed = (datetime.now() - last).total_seconds() / 60
    remaining = KICK_COOLDOWN_MIN - elapsed
    return max(0, int(remaining))


def kickstart(label: str) -> bool:
    try:
        r = subprocess.run(
            ["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/{label}"],
            capture_output=True, text=True, timeout=15,
        )
        return r.returncode == 0
    except Exception:
        return False


def send_whatsapp(text: str) -> bool:
    """POST the digest to a WhatsApp bot endpoint if configured.

    Disabled by default (AGENTS_WA_URL / AGENTS_WA_JID unset): returns False
    without sending. main() still prints the digest, so nothing is lost."""
    if not WA_URL or not WA_JID:
        return False
    try:
        data = json.dumps({"jid": WA_JID, "text": text, "src": "manager"}).encode("utf-8")
        req = urllib.request.Request(WA_URL, data=data,
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=15)
        return True
    except Exception:
        return False


def _is_limit(rec) -> bool:
    blob = (rec.last_result or "").lower()
    return any(h in blob or h in (rec.last_result or "") for h in LIMIT_HINTS)


def _is_queue_empty(rec) -> bool:
    blob = rec.last_result or ""
    low = blob.lower()
    return any(h in blob or h in low for h in QUEUE_HINTS)


_POSITIVE = ("בהצלחה", "מחובר ותקין", "תקין", "מאושר", "הושלם", "אין לידים")


def _is_problem(rec) -> bool:
    # never_ran / unknown are ambiguous (often "not due yet" or event-driven),
    # so the manager never nags or acts on them. Only real problems count.
    return rec.status in ("failed", "stale") or (rec.has_warnings and rec.status == "ok")


def _warning_reason(rec) -> str:
    reason = render.humanize(rec)
    if not reason or any(p in reason for p in _POSITIVE):
        return "רץ אבל יש שגיאה בתוך הלוג, שווה לבדוק"
    return reason


def _had_success_today(rec) -> bool:
    today = datetime.now().strftime("%Y-%m-%d")
    for run in (rec.recent_runs or []):
        start = run.get("start", "") if isinstance(run, dict) else ""
        ok = run.get("ok", False) if isinstance(run, dict) else False
        if start.startswith(today) and ok:
            return True
    return False


def _last_failure_is_network(rec) -> bool:
    if not rec.log_path:
        return False
    path = os.path.expanduser(rec.log_path)
    if not os.path.isfile(path):
        return False
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            tail = f.readlines()[-80:]
    except OSError:
        return False
    return any(_NETWORK_ERROR_RE.search(ln) for ln in tail)


def classify(rec, limit_hit_globally: bool):
    """Return ('fix'|'escalate', plain-Hebrew reason)."""
    reason = render.humanize(rec)
    if _is_limit(rec):
        return "escalate", "מכסת MAX מלאה, חסום עד האיפוס"
    if _is_queue_empty(rec):
        return "escalate", "תור נושאים ריק, צריך להוסיף נושאים"
    if rec.has_warnings and rec.status == "ok":
        # exit 0 but an error inside; re-running won't fix it -> always escalate
        return "escalate", _warning_reason(rec)
    if rec.status in ("failed", "stale"):
        if not rec.auto_fix:
            # Network blip + no successful run today + content category:
            # safe to retry once. The kick budget caps it to one attempt
            # so we never burn quota in a loop.
            if (rec.category == "content"
                    and not limit_hit_globally
                    and not _had_success_today(rec)
                    and _last_failure_is_network(rec)):
                return "fix", reason + " (כשל רשת זמני, מנסה שוב)"
            # not on the safe allowlist (publishes / sends / external / costs quota)
            return "escalate", reason
        if limit_hit_globally and rec.category == "content":
            return "escalate", "חסום בגלל מכסת MAX, לא מנסה שוב כדי לא לשרוף מכסה"
        return "fix", reason
    return "escalate", reason


def run(dry_run: bool = False, silent: bool = False) -> dict:
    """Run one pass.

    - dry_run: don't kick, don't save, don't send WhatsApp.
    - silent: kick + save state, but DO NOT send WhatsApp. Used by the
      hourly watchdog so it heals quietly between the named digest times.
    """
    cfg = status_engine.load_config(CONFIG_PATH)
    records = [r for r in status_engine.build_records(cfg) if r.label != _self_label()]
    problems = [r for r in records if _is_problem(r)]

    limit_hit_globally = any(_is_limit(r) for r in records)

    state = load_state()
    today = datetime.now().strftime("%Y-%m-%d")
    if state.get("date") != today:
        state = {"date": today, "kicks": {}}

    fixed, escalated = [], []
    to_verify = []  # [(rec, reason), ...]

    for rec in problems:
        action, reason = classify(rec, limit_hit_globally)
        if action != "fix":
            escalated.append((rec, reason))
            continue

        kicks = _kicks_for(state, rec.label)
        if len(kicks) >= MAX_KICKS_PER_DAY:
            escalated.append((rec, f"ניסיתי {MAX_KICKS_PER_DAY} פעמים היום ולא הצליח, צריך התערבות ידנית"))
            continue

        cooldown = _cooldown_remaining_min(kicks)
        if cooldown > 0:
            escalated.append((rec, f"ניסיון קודם לפני זמן קצר, מחכה עוד {cooldown} דק' לפני ניסיון חוזר"))
            continue

        if len(fixed) + len(to_verify) >= MAX_FIXES_PER_RUN:
            escalated.append((rec, reason + " (מעבר למכסת התיקונים לריצה הזו)"))
            continue

        ok = True if dry_run else kickstart(rec.label)
        if not ok:
            escalated.append((rec, "ניסיתי להפעיל מחדש ולא הצלחתי, צריך בדיקה ידנית"))
            continue

        kicks.append({"at": _now_iso(), "verified_ok": False})
        to_verify.append((rec, reason))

    # Verify all kicks at once after a single sleep, so the wall clock is 60s
    # regardless of how many agents we kicked.
    if to_verify and not dry_run:
        time.sleep(VERIFY_DELAY_SEC)
        fresh = {r.label: r for r in status_engine.build_records(cfg)}
        for rec, _reason in to_verify:
            new_rec = fresh.get(rec.label)
            attempt_no = len(state["kicks"][rec.label])
            verified = bool(new_rec and (new_rec.is_running or new_rec.status == "ok"))
            state["kicks"][rec.label][-1]["verified_ok"] = verified
            if verified:
                fixed.append((rec, f"הופעל מחדש ✓ אומת (ניסיון {attempt_no}/{MAX_KICKS_PER_DAY})"))
            else:
                # Kicked but verification failed. Don't escalate yet, the next
                # watchdog pass after cooldown will retry up to MAX_KICKS_PER_DAY.
                fixed.append((rec, f"הופעל מחדש, עדיין כושל (ניסיון {attempt_no}/{MAX_KICKS_PER_DAY}, אנסה שוב בעוד {KICK_COOLDOWN_MIN} דק')"))
    elif to_verify and dry_run:
        for rec, _reason in to_verify:
            fixed.append((rec, "הופעל מחדש (dry-run)"))

    if not dry_run:
        save_state(state)

    ok_agents = [r for r in records if r.status == "ok" and not r.has_warnings]
    msg = _build_message(today, ok_agents, fixed, escalated, limit_hit_globally)

    # Single daily digest. Skip the send on Shabbat (Saturday); Saturday's run
    # still logs, and Sunday's digest covers it. Silent (watchdog) mode also
    # skips the send, only the named manager times broadcast.
    is_shabbat = datetime.now().weekday() == 5
    if not dry_run and not silent and not is_shabbat:
        send_whatsapp(msg)

    return {"fixed": fixed, "escalated": escalated, "ok": ok_agents,
            "message": msg, "limit_hit": limit_hit_globally,
            "shabbat": is_shabbat, "silent": silent}


def _accomplishments(ok_agents):
    """Notable things agents actually did (skip the boring 'just ran ok')."""
    out = []
    for r in ok_agents:
        line = render.humanize(r)
        if r.is_win or (line and line != "רץ בהצלחה"):
            out.append((r.name, line))
    return out


def _build_message(today, ok_agents, fixed, escalated, limit_hit) -> str:
    lines = [f"🤖 דוח המנהל היומי · {today}", ""]
    lines.append("הסוכנים שרצו היום עשו עבודה מצוינת 💪")
    lines.append("")

    lines.append(f"✅ עבדו תקין היום: {len(ok_agents)} סוכנים")
    for name, what in _accomplishments(ok_agents)[:8]:
        lines.append(f"• {name}: {what}")
    lines.append("")

    if fixed:
        lines.append(f"🔧 נתקע ותיקנתי לבד ({len(fixed)}):")
        for rec, what in fixed:
            lines.append(f"• {rec.name}: {what}")
        lines.append("")

    if escalated:
        lines.append(f"⚠️ דורש את תשומת ליבך ({len(escalated)}):")
        for rec, why in escalated:
            lines.append(f"• {rec.name}: {why}")
        if limit_hit:
            lines.append("")
            lines.append("שים לב: מכסת MAX מלאה, חלק מהסוכנים יחזרו לעבוד אחרי האיפוס.")
    elif not fixed:
        lines.append("אין שום דבר שדורש אותך, הכל תחת שליטה ✓")

    return "\n".join(lines).strip()


def main():
    dry = "--dry-run" in sys.argv
    silent = "--silent" in sys.argv
    started = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    label = "agents_watchdog" if silent else "agents_manager"
    print(f"=== [{started}] {label} START ===")
    result = run(dry_run=dry, silent=silent)
    print(result["message"])
    ended = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    summary = f"סיכום: תיקנתי {len(result['fixed'])}, הסלמתי {len(result['escalated'])}"
    if dry:
        summary = "[DRY RUN] " + summary
    if silent:
        summary += " (שקט, ללא וואטסאפ)"
    print(summary)
    print(f"=== [{ended}] {label} END (exit=0) ===")


if __name__ == "__main__":
    main()
