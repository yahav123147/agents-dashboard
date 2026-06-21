import os
import html
import re
from datetime import datetime

TEMPLATE = os.path.join(os.path.dirname(__file__), "templates", "dashboard.html")
CATEGORY_LABELS = {"content": "תוכן ו-SEO", "ads": "מודעות", "health": "תקינות המערכת", "reports": "דוחות", "אחר": "אחר"}

# status -> (Hebrew label, css class)
STATUS_HE = {
    "ok": ("תקין", "ok"),
    "running": ("רץ עכשיו", "run"),
    "failed": ("נכשל", "fail"),
    "stale": ("לא רץ מזמן", "stale"),
    "never_ran": ("עוד לא רץ", "none"),
    "unknown": ("לא ידוע", "none"),
}


def _esc(s):
    # Strip em-dashes from any echoed log text (house style bans them).
    return html.escape(str(s or "").replace("—", ", "))


PRESET_BUTTONS = [
    ("עכשיו", "/", None),
    ("היום", "/?preset=today", "today"),
    ("אתמול", "/?preset=yesterday", "yesterday"),
    ("השבוע", "/?preset=week", "week"),
    ("7 ימים", "/?preset=7d", "7d"),
    ("14 ימים", "/?preset=14d", "14d"),
    ("30 ימים", "/?preset=30d", "30d"),
]


def _date_picker(preset):
    chips = []
    for label, href, p in PRESET_BUTTONS:
        active = "active" if (p == preset or (p is None and preset is None)) else ""
        chips.append(f'<a class="pchip {active}" href="{href}">{label}</a>')
    custom = (
        '<form class="pcustom" method="get" action="/">'
        '<input type="date" name="from" required>'
        ' עד '
        '<input type="date" name="to" required>'
        '<button type="submit">החל</button>'
        '</form>'
    )
    return f'<div class="picker">{"".join(chips)}<span class="psep">|</span>{custom}</div>'


def humanize(rec):
    """Turn the raw last-result / status into one plain Hebrew sentence."""
    raw = rec.last_result or ""
    low = raw.lower()

    # Self-healed: lead with the recovery story instead of whatever line tail
    # extraction picked up. The "fail_time" gives you the concrete moment to
    # cross-reference (e.g., DNS outage at 07:00).
    if getattr(rec, "self_healed", False) and rec.status == "ok":
        fail_at = getattr(rec, "self_healed_at", None) or ""
        when = fail_at.split(" ")[1][:5] if " " in fail_at else ""
        return f"נכשל ב-{when}, התאושש בריצה הבאה" if when else "נכשל קודם, התאושש בריצה הבאה"

    # When the agent is stale, an old success line is misleading ("כתב ופרסם
    # מאמר חדש" while it hasn't run in a week is just confusing). Surface the
    # real situation. Operational matches below (queue empty, network) still
    # apply because they explain WHY it's stale.
    if rec.status == "stale" and (rec.is_win or low.startswith("done") or "פרסם" in raw):
        return "לא רץ כמו שצריך כבר זמן רב"

    if ("תור" in raw and "ריק" in raw) or "topics queue empty" in low:
        return "אין נושאים בתור לכתיבה, צריך להוסיף נושאים"
    # Make health-check alerts surface from the daily log; the literal-tail
    # extractor returns "WhatsApp alert sent ✓" which is misleading. Promote
    # the actual issue counts when has_warnings is true.
    if rec.has_warnings and "make health check" in low:
        return "מצאתי בעיות ב-Make, בדוק את הוואטסאפ עם הפירוט"
    if "timed out" in low or "timeout" in low:
        return "נכשל: השרת לא הגיב בזמן (בעיית תקשורת)"
    if "hit your limit" in low or ("limit" in low and "reset" in low):
        return "נכשל: נגמרה מכסת השימוש (מתחדשת בקרוב)"
    if "state ready for id" in low:
        return "נתקע באמצע: התחיל מאמר ולא סיים"
    if "without pii" in low or "not_has_pii" in low or "cardcom api credentials" in low or "firing without" in low:
        return "הטראקינג משדר בלי פרטי לקוח, צריך טיפול"
    if "api error" in low or "traceback" in low or "exception" in low:
        return "נכשל: שגיאת מערכת בזמן הריצה"
    if "already approved" in low or "skipping" in low:
        return "הכל מאושר, לא נדרשה פעולה"
    if "no leads" in low or "0 new leads" in low or "leads to process" in low:
        return "אין לידים חדשים לטפל בהם כרגע"
    if rec.is_win and "פרסם" in raw:
        return "כתב ופרסם מאמר חדש"
    if "connected" in low:
        return "מחובר ותקין"
    if "כבר הושלם" in raw:
        return "כבר הושלם היום, אין צורך בריצה נוספת"
    if '"ok":true' in low or low == "ok" or "ok (exit=0)" in low or low.startswith("done"):
        return "רץ בהצלחה"

    if rec.status == "never_ran":
        return "עוד לא רץ מאז שהופעל"
    if rec.status == "stale":
        return "לא רץ כמו שצריך כבר כמה זמן"
    if rec.status == "failed":
        return "נכשל בריצה האחרונה"
    if rec.status == "ok":
        return "רץ בהצלחה"
    return ""


def _pill(rec):
    label, cls = STATUS_HE.get(rec.status, ("לא ידוע", "none"))
    if rec.has_warnings and rec.status == "ok":
        label, cls = "שווה לבדוק", "stale"
    # Self-healed wins over the stale-warning view: the manager (or launchd
    # retry) already fixed the previous failure, so show a calm recovery badge
    # rather than a stale "שווה לבדוק". Only when status is truly ok and we
    # haven't already promoted to a worse state.
    if getattr(rec, "self_healed", False) and rec.status == "ok":
        label, cls = "התאושש ✓", "healed"
    return f'<span class="pill p-{cls}">{label}</span>'


def _when(rec):
    if rec.last_run and "T" in rec.last_run:
        return _esc(rec.last_run.split("T")[1][:5])
    return _esc(rec.schedule_human)


def _summary(records):
    c = {"ok": 0, "running": 0, "failed": 0, "attn": 0, "healed": 0}
    for r in records:
        if r.status == "running":
            c["running"] += 1
        elif r.status == "failed":
            c["failed"] += 1
        elif r.status in ("stale", "never_ran", "unknown"):
            c["attn"] += 1
        else:
            c["ok"] += 1
            if getattr(r, "self_healed", False):
                c["healed"] += 1
    healed_tile = (
        f'<div class="sum s-healed"><b>{c["healed"]}</b><span>התאוששו לבד</span></div>'
        if c["healed"] else ""
    )
    return (
        '<div class="sumbar">'
        f'<div class="sum s-ok"><b>{c["ok"]}</b><span>עובדים תקין</span></div>'
        f'<div class="sum s-run"><b>{c["running"]}</b><span>רצים כרגע</span></div>'
        f'<div class="sum s-fail"><b>{c["failed"]}</b><span>נכשלו</span></div>'
        f'<div class="sum s-attn"><b>{c["attn"]}</b><span>לא רצו כמו שצריך</span></div>'
        f'{healed_tile}'
        '</div>'
    )


def _needs_attention(r):
    if getattr(r, "dismissed", False):
        return False
    # Self-healed agents don't need attention even if old failure text still
    # lingers in the log tail. The next successful run is proof enough.
    if getattr(r, "self_healed", False) and r.status == "ok":
        return False
    return r.status in ("failed", "stale", "never_ran", "unknown") or r.has_warnings


def _action_buttons(label: str) -> str:
    log_link = f'<a class="btn btn-link" href="/log/{_esc(label)}" target="_blank">פתח לוג</a>'
    return (
        f'<div class="actions">'
        f'<button class="btn btn-run" data-action="kickstart" data-label="{_esc(label)}">הרץ שוב</button>'
        f'<button class="btn btn-dismiss" data-action="dismiss" data-label="{_esc(label)}">סגור התראה</button>'
        f'{log_link}'
        f'</div>'
    )


def _runs_detail(r):
    runs = r.recent_runs[-6:]
    if not runs and not r.log_path:
        return ""
    items = []
    for run in runs:
        mark = "✓" if run.get("ok") else "✗"
        when = _esc(run.get("end") or run.get("start") or "")
        items.append(f'<li>{mark} {when}</li>')
    log_link = f'<a href="/log/{_esc(r.label)}">פתח את הלוג המלא</a>' if r.log_path else ""
    runs_html = f'<ul class="runs">{"".join(items)}</ul>' if items else ""
    return f'<details><summary>פרטים</summary>{runs_html}{log_link}</details>'


def _attention(records):
    items = [r for r in records if _needs_attention(r)]
    if not items:
        return '<div class="allgood">הכל תקין, אין מה לטפל כרגע ✓</div>'
    order = {"failed": 0, "stale": 1, "never_ran": 2, "unknown": 2}
    items.sort(key=lambda r: (order.get(r.status, 1), not r.has_warnings))
    rows = []
    for r in items:
        rows.append(
            '<div class="arow">'
            f'{_pill(r)}'
            f'<div class="atxt"><div class="anm">{_esc(r.name)}</div>'
            f'<div class="adesc">{_esc(r.desc)}</div></div>'
            f'<div class="awhy">{_esc(humanize(r))}</div>'
            f'<div class="awhen">{_when(r)}</div>'
            f'{_action_buttons(r.label)}'
            '</div>'
        )
    return (f'<div class="block-title fail-title">צריך תשומת לב ({len(items)})</div>'
            f'<div class="attn">{"".join(rows)}</div>')


def _tables(records):
    out = []
    for cat in ("content", "ads", "health", "reports", "אחר"):
        rows = [r for r in records if r.category == cat and not _needs_attention(r)]
        if not rows:
            continue
        out.append(f'<div class="block-title">{_esc(CATEGORY_LABELS.get(cat, cat))}</div>')
        out.append('<div class="cardlist">')
        for r in rows:
            out.append(
                '<div class="crow">'
                f'{_pill(r)}'
                f'<div class="ctxt"><div class="cnm">{_esc(r.name)}</div>'
                f'<div class="cdesc">{_esc(r.desc)}</div></div>'
                f'<div class="cwhat">{_esc(humanize(r))}{_runs_detail(r)}</div>'
                f'<div class="cwhen">{_when(r)}</div>'
                '</div>'
            )
        out.append('</div>')
    return "".join(out)


def _window_summary(records, window):
    totals = {"runs": 0, "successes": 0, "fails": 0}
    active_agents = 0
    for r in records:
        ws = r.window_summary or {}
        if ws.get("runs", 0) > 0:
            active_agents += 1
        totals["runs"] += ws.get("runs", 0)
        totals["successes"] += ws.get("successes", 0)
        totals["fails"] += ws.get("fails", 0)
    return (
        f'<div class="winlabel">בטווח: <b>{_esc(window.get("label",""))}</b></div>'
        '<div class="sumbar">'
        f'<div class="sum s-ok"><b>{totals["successes"]}</b><span>ריצות מוצלחות</span></div>'
        f'<div class="sum s-fail"><b>{totals["fails"]}</b><span>ריצות שנכשלו</span></div>'
        f'<div class="sum s-run"><b>{totals["runs"]}</b><span>סה"כ ריצות</span></div>'
        f'<div class="sum s-attn"><b>{active_agents}</b><span>סוכנים פעילים</span></div>'
        '</div>'
    )


def _window_attention(records):
    items = [r for r in records if (r.window_summary or {}).get("fails", 0) > 0]
    if not items:
        return '<div class="allgood">אף סוכן לא נכשל בטווח הזה ✓</div>'
    items.sort(key=lambda r: -(r.window_summary or {}).get("fails", 0))
    rows = []
    for r in items:
        ws = r.window_summary
        fails = ws.get("fails", 0)
        runs = ws.get("runs", 0)
        last_fail = next((x for x in reversed(r.window_runs) if not x.get("ok")), None)
        why = _esc((last_fail or {}).get("result", "")) or "נכשל בריצה אחת או יותר"
        rows.append(
            '<div class="arow">'
            f'<span class="pill p-fail">{fails} כשלים</span>'
            f'<div class="atxt"><div class="anm">{_esc(r.name)}</div>'
            f'<div class="adesc">{_esc(r.desc)}</div></div>'
            f'<div class="awhy">{why}</div>'
            f'<div class="awhen">{runs} ריצות בטווח</div>'
            '</div>'
        )
    return (f'<div class="block-title fail-title">נכשלו בטווח ({len(items)})</div>'
            f'<div class="attn">{"".join(rows)}</div>')


def _window_tables(records):
    out = []
    for cat in ("content", "ads", "health", "reports", "אחר"):
        rows = [r for r in records if r.category == cat and (r.window_summary or {}).get("runs", 0) > 0 and (r.window_summary or {}).get("fails", 0) == 0]
        if not rows:
            continue
        rows.sort(key=lambda r: -(r.window_summary or {}).get("successes", 0))
        out.append(f'<div class="block-title">{_esc(CATEGORY_LABELS.get(cat, cat))}</div>')
        out.append('<div class="cardlist">')
        for r in rows:
            ws = r.window_summary
            successes = ws.get("successes", 0)
            wins = [x for x in r.window_runs if x.get("ok") and x.get("result")]
            sample = wins[-1] if wins else None
            sample_text = _esc((sample or {}).get("result", "")) if sample else "רץ בהצלחה"
            details = ""
            if r.window_runs:
                items = []
                for run in r.window_runs[-15:]:
                    mark = "✓" if run.get("ok") else "✗"
                    when = _esc(run.get("end") or run.get("start") or "")
                    items.append(f'<li>{mark} {when} {_esc(run.get("result", ""))}</li>')
                details = f'<details><summary>פרטים ({len(r.window_runs)} ריצות)</summary><ul class="runs">{"".join(items)}</ul></details>'
            out.append(
                '<div class="crow">'
                f'<span class="pill p-ok">{successes} הצליחו</span>'
                f'<div class="ctxt"><div class="cnm">{_esc(r.name)}</div>'
                f'<div class="cdesc">{_esc(r.desc)}</div></div>'
                f'<div class="cwhat">{sample_text}{details}</div>'
                f'<div class="cwhen">{ws.get("runs", 0)} ריצות</div>'
                '</div>'
            )
        out.append('</div>')
    return "".join(out)


def render_dashboard(records, window=None, preset=None) -> str:
    with open(TEMPLATE, encoding="utf-8") as f:
        tmpl = f.read()
    picker = _date_picker(preset)
    if window is None:
        summary_html = _summary(records)
        attention_html = _attention(records)
        tables_html = _tables(records)
    else:
        summary_html = _window_summary(records, window)
        attention_html = _window_attention(records)
        tables_html = _window_tables(records)
    return (tmpl
            .replace("{{UPDATED}}", datetime.now().strftime("%H:%M"))
            .replace("{{PICKER}}", picker)
            .replace("{{SUMMARY}}", summary_html)
            .replace("{{ATTENTION}}", attention_html)
            .replace("{{TABLES}}", tables_html))
