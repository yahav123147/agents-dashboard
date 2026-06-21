from __future__ import annotations

import os
import re
import glob
import json
from dataclasses import dataclass, asdict


@dataclass
class RunRecord:
    start: str | None
    end: str | None
    ok: bool
    result: str

    def to_dict(self) -> dict:
        return asdict(self)


# Log run markers look like:
#   === [2026-05-29 08:03:59] my_agent START ===
#   === [2026-05-29 08:07:33] my_agent END (exit=1) ===
_START_RE = re.compile(r"^=== \[([^\]]+)\].*\bSTART\b")
_END_RE = re.compile(r"^=== \[([^\]]+)\].*\bEND\b")
_EXIT_RE = re.compile(r"\bEND\b.*\(exit=(-?\d+)\)")
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_FAIL_TOKENS = ("EMPTY", "ERROR", "FAILED", "TRACEBACK", "FORBIDDEN")


def _strip_ts(line: str) -> str:
    return re.sub(r"^\[[^\]]+\]\s*", "", line.strip())


def read_log_lines(path: str, max_lines: int = 200) -> list[str]:
    if not path:
        return []
    expanded = os.path.expanduser(path)
    if "*" in expanded:
        matches = sorted(
            glob.glob(expanded),
            key=lambda p: os.path.getmtime(p) if os.path.exists(p) else 0,
        )
        if not matches:
            return []
        expanded = matches[-1]
    if not os.path.isfile(expanded):
        return []
    try:
        with open(expanded, "r", encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except OSError:
        return []
    return [ln for ln in lines[-max_lines:]]


def _body_failed(body_lines: list[str]) -> bool:
    up = " ".join(body_lines).upper()
    return any(t in up for t in _FAIL_TOKENS)


def _summarize_body(body_lines: list[str]) -> str:
    for ln in reversed(body_lines):
        if ln.strip():
            return _strip_ts(ln)
    return ""


def parse_startend_runs(lines: list[str]) -> list[RunRecord]:
    runs = []
    cur_start = None
    cur_body = []
    for ln in lines:
        ms = _START_RE.search(ln)
        me = _END_RE.search(ln)
        if ms:
            cur_start = ms.group(1)
            cur_body = []
        elif me and cur_start is not None:
            exit_m = _EXIT_RE.search(ln)
            if exit_m is not None:
                ok = exit_m.group(1) == "0"
            else:
                ok = not _body_failed(cur_body)
            runs.append(RunRecord(start=cur_start, end=me.group(1), ok=ok,
                                  result=_summarize_body(cur_body)))
            cur_start = None
        elif cur_start is not None:
            cur_body.append(ln)
    return runs


def last_run_lines(lines: list[str]) -> list[str]:
    """Lines belonging to the most recent run only.

    Scoping to the last run prevents weeks-old, already-recovered errors in the
    log history from raising false warnings. For START/END logs that is the
    slice from the last START marker; otherwise the last few lines as a proxy.
    """
    last_start = None
    for i, ln in enumerate(lines):
        if _START_RE.search(ln):
            last_start = i
    if last_start is not None:
        return lines[last_start:]
    # No run markers (append-style log): isolate the most recent day's lines so
    # errors from previous days don't leak into "last run". Falls back to a small
    # tail when the log has no dates.
    dates = [m.group(0) for ln in lines if (m := _DATE_RE.search(ln))]
    if dates:
        newest = max(dates)
        same_day = [ln for ln in lines if newest in ln]
        if same_day:
            return same_day[-15:]
    return lines[-15:]


_SUCCESS_TAIL_RE = re.compile(
    r"DONE\s+id=|Published\s+id=|Published:\s|פורסם|"
    r"כתב ופרסם|Banner generated: OK|END\s.*\(exit=0\)|"
    r"\bOK\b.*media_id=|כבר הושלם|✅",
    re.IGNORECASE,
)


def scan_warnings(lines: list[str], signatures: list[str]) -> bool:
    """True iff an error signature appears AND the run did not visibly succeed.

    Without the positive-tail override, a stale 'failed ' / 'error:' substring
    anywhere in the recent window keeps raising warnings even after the writer
    finished cleanly. We treat a clear success marker in the last few non-empty
    lines as authoritative — that's the agent's own 'I'm done and it worked'."""
    blob = "\n".join(lines).lower()
    hit = any(sig.lower() in blob for sig in signatures)
    if not hit:
        return False
    nonempty = [ln for ln in lines if ln.strip()]
    for ln in nonempty[-6:]:
        if _SUCCESS_TAIL_RE.search(ln):
            return False
    return True


def _last_nonempty(lines: list[str]) -> str:
    for ln in reversed(lines):
        s = ln.strip()
        if s and not _START_RE.search(ln) and not _END_RE.search(ln):
            return _strip_ts(ln)
    return ""


def _extract_generic(lines, cfg):
    # Prefer an alert line if one exists in the recent window: the literal
    # last line is often noise (e.g. "WhatsApp alert sent ✓") while the 🚨
    # line above it carries the actual diagnosis.
    for ln in reversed(lines):
        if "🚨" in ln:
            return _strip_ts(ln), False
    return _last_nonempty(lines), False


def _extract_article_writer(lines, cfg):
    up = "\n".join(lines).upper()
    if "TOPICS QUEUE EMPTY" in up or ("תור" in "\n".join(lines) and "ריק" in "\n".join(lines)):
        return "תור הנושאים ריק", False
    if "FAILED" in up or "API ERROR" in up or "TRACEBACK" in up:
        for ln in reversed(lines):
            s = ln.strip()
            if "Last line:" in s or "FAILED" in s.upper():
                return _strip_ts(ln), False
        return "נכשל בריצה האחרונה", False
    state_file = cfg.get("state_file")
    if state_file and os.path.isfile(os.path.expanduser(state_file)):
        try:
            with open(os.path.expanduser(state_file), encoding="utf-8") as f:
                st = json.load(f)
            url = st.get("last_url", "")
            if url:
                return f"פרסם ✓ {url}", True
        except (OSError, json.JSONDecodeError):
            pass
    return _last_nonempty(lines), False


def _extract_nl_report(lines, cfg):
    for ln in reversed(lines):
        if "Result:" in ln:
            return ln.split("Result:", 1)[1].strip(), True
    return _last_nonempty(lines), False


def _extract_python_freeform(lines, cfg):
    return _last_nonempty(lines), False


def _extract_line_prefixed(lines, cfg):
    last = _last_nonempty(lines)
    raw = " ".join(lines)
    if "Already succeeded today" in raw or "Skipping" in raw:
        return "כבר הושלם היום", True
    return last, False


def _extract_marker_file(lines, cfg, today=None):
    marker_glob = cfg.get("marker_glob")
    if marker_glob:
        for p in glob.glob(os.path.expanduser(marker_glob)):
            if today and today in os.path.basename(p):
                return "הצליח היום ✓", True
    return _extract_line_prefixed(lines, cfg)


_HINTS = {
    "generic": _extract_generic,
    "article_writer": _extract_article_writer,
    "nl_report": _extract_nl_report,
    "python_freeform": _extract_python_freeform,
    "line_prefixed": _extract_line_prefixed,
}


def read_log(agent_cfg: dict, default_sigs: list[str], today: str | None = None) -> dict:
    log_path = os.path.expanduser(agent_cfg.get("log_path", "") or "")
    lines = read_log_lines(log_path)
    recent = last_run_lines(lines)
    hint = agent_cfg.get("result_hint", "generic")

    if hint == "marker_file":
        result, is_win = _extract_marker_file(recent, agent_cfg, today=today)
    else:
        extractor = _HINTS.get(hint, _extract_generic)
        result, is_win = extractor(recent, agent_cfg)

    runs = parse_startend_runs(lines)
    last_run_ok = runs[-1].ok if runs else None
    signatures = agent_cfg.get("error_signatures", default_sigs)
    return {
        "last_result": result,
        "is_win": is_win,
        "has_warnings": scan_warnings(recent, signatures),
        "last_run_ok": last_run_ok,
        "recent_runs": [r.to_dict() for r in runs][-10:],
    }
