"""Windows backend: Task Scheduler via PowerShell.

Mirrors the launchd backend in status_engine.py. It enumerates scheduled tasks
and maps each one onto the same shape the engine consumes on every platform:

    (label, meta, probe)
      label  -> "\\Path\\Name"  (the full task path, used as the agents.json key)
      meta   -> {standard_out_path, interval_seconds, schedule_human, os_last_run}
      probe  -> {is_running, last_exit_code, os_last_run}

Everything here is pure stdlib (subprocess/json/datetime) so the parsing is
unit-testable on any OS by injecting a fake `runner`. Only the live query
actually shells out to powershell.exe, which exists on Windows 10/11.
"""
from __future__ import annotations

import json
import datetime
import subprocess

# Task Scheduler "Last Result" sentinel HRESULTs.
_RUNNING = 267009    # 0x41301: task is currently running
_NEVER_RAN = 267011  # 0x41303: task has not run yet

# Emits one JSON object per task. ConvertTo-Json yields a bare object (not an
# array) when there is exactly one task; query_tasks() normalizes that.
_PS_SCRIPT = (
    "$ErrorActionPreference='Stop';"
    "Get-ScheduledTask | ForEach-Object {"
    "  $i = $_ | Get-ScheduledTaskInfo;"
    "  [PSCustomObject]@{"
    "    name=$_.TaskName; path=$_.TaskPath; state=[string]$_.State;"
    "    last=if($i.LastRunTime){$i.LastRunTime.ToString('yyyy-MM-dd HH:mm:ss')}else{$null};"
    "    next=if($i.NextRunTime){$i.NextRunTime.ToString('yyyy-MM-dd HH:mm:ss')}else{$null};"
    "    result=$i.LastTaskResult"
    "  }"
    "} | ConvertTo-Json -Depth 3"
)


def _run_powershell(script: str) -> str:
    out = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, timeout=30,
    )
    return out.stdout


def query_tasks(runner=None) -> list:
    """Return the raw task dicts. `runner` is a no-arg callable returning the
    PowerShell JSON string (injected in tests); defaults to a real query."""
    runner = runner or (lambda: _run_powershell(_PS_SCRIPT))
    raw = runner() or ""
    try:
        data = json.loads(raw)
    except (ValueError, json.JSONDecodeError):
        return []
    if isinstance(data, dict):  # single task -> one object, not an array
        data = [data]
    return data if isinstance(data, list) else []


def _label_of(task: dict) -> str:
    path = task.get("path") or "\\"
    name = task.get("name") or ""
    if not path.endswith("\\"):
        path += "\\"
    return path + name


def _interval_and_human(last, nxt):
    def _p(s):
        try:
            return datetime.datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
        except (ValueError, TypeError):
            return None
    a, b = _p(last), _p(nxt)
    secs = int((b - a).total_seconds()) if (a and b and b > a) else 86400
    mins = secs // 60
    if mins < 1:
        human = "מתוזמן"
    elif mins < 60:
        human = f"כל {mins} דק'"
    elif mins < 60 * 24:
        human = f"כל {mins // 60} שעות"
    elif secs <= int(86400 * 1.5):
        human = "יומי"
    else:
        human = f"כל {secs // 86400} ימים"
    return secs, human


def _matches(label: str, name: str, includes, excludes) -> bool:
    if includes and not any(label.startswith(p) or name.startswith(p) for p in includes):
        return False
    if any(x in label for x in excludes):
        return False
    return True


def collect(cfg, runner=None) -> list:
    """Return [(label, meta, probe), ...] filtered by include_prefixes /
    exclude_patterns, matching the launchd backend's output shape."""
    includes = cfg.get("include_prefixes", [])
    excludes = cfg.get("exclude_patterns", [])
    out = []
    for t in query_tasks(runner):
        name = t.get("name") or ""
        label = _label_of(t)
        if not _matches(label, name, includes, excludes):
            continue
        result = t.get("result")
        state = (t.get("state") or "").lower()
        is_running = state == "running" or result == _RUNNING
        if result in (None, _NEVER_RAN, _RUNNING):
            last_exit = None
        else:
            try:
                last_exit = int(result)
            except (TypeError, ValueError):
                last_exit = None
        secs, human = _interval_and_human(t.get("last"), t.get("next"))
        meta = {
            "standard_out_path": None,
            "interval_seconds": secs,
            "schedule_human": human,
            "os_last_run": t.get("last"),
        }
        probe = {
            "is_running": is_running,
            "last_exit_code": last_exit,
            "os_last_run": t.get("last"),
        }
        out.append((label, meta, probe))
    return out


def kickstart(label: str):
    """Run a task now via schtasks. `label` is the full "\\Path\\Name"."""
    try:
        r = subprocess.run(
            ["schtasks", "/run", "/tn", label],
            capture_output=True, text=True, timeout=15,
        )
        return r.returncode == 0, (r.stdout + r.stderr).strip()
    except Exception as e:  # noqa: BLE001 - report any failure to the UI
        return False, str(e)
