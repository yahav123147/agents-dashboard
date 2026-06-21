from __future__ import annotations

import os
import re
import sys
import glob
import json
import plistlib
import subprocess
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone

import extractors


@dataclass
class AgentRecord:
    label: str
    name: str
    category: str
    desc: str = ""
    status: str = "unknown"
    is_running: bool = False
    last_exit_code: int | None = None
    schedule_human: str = ""
    last_run: str | None = None
    last_duration_sec: int | None = None
    last_result: str = ""
    is_win: bool = False
    next_expected: str | None = None
    is_stale: bool = False
    has_warnings: bool = False
    recent_runs: list = field(default_factory=list)
    log_path: str | None = None
    auto_fix: bool = False
    dismissed: bool = False
    # True when the latest run succeeded but one of the previous few runs failed.
    # Signals "the manager (or launchd retry) fixed it" so the UI can show a
    # calm "התאושש" badge instead of a scary failure warning that's already gone.
    self_healed: bool = False
    self_healed_at: str | None = None
    # Populated only in window mode (date-range view). When set, the dashboard
    # shows what happened in the window instead of the live "right now" status.
    window_runs: list = field(default_factory=list)
    window_summary: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def load_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    cfg.setdefault("include_prefixes", [])
    cfg.setdefault("exclude_patterns", [])
    cfg.setdefault("default_error_signatures", ["traceback", "forbidden", "exception", "failed ", "error:", "http 4", "http 5"])
    cfg.setdefault("agents", {})
    return cfg


def discover_labels(launchagents_dir: str, cfg: dict) -> list[str]:
    labels = []
    for path in sorted(glob.glob(os.path.join(launchagents_dir, "*.plist"))):
        label = os.path.basename(path)[: -len(".plist")]
        if not any(label.startswith(p) for p in cfg.get("include_prefixes", [])):
            continue
        if any(pat in label for pat in cfg.get("exclude_patterns", [])):
            continue
        labels.append(label)
    return labels


def parse_plist(plist_path: str) -> dict:
    with open(plist_path, "rb") as f:
        data = plistlib.load(f)

    standard_out = data.get("StandardOutPath")
    interval_seconds = 86400
    schedule_human = "לא ידוע"

    if "StartInterval" in data:
        interval_seconds = int(data["StartInterval"])
        mins = interval_seconds // 60
        schedule_human = f"כל {mins} דק'" if mins < 60 else f"כל {mins // 60} שעות"
    elif "StartCalendarInterval" in data:
        sci = data["StartCalendarInterval"]
        entries = sci if isinstance(sci, list) else [sci]
        times = []
        for e in entries:
            h = e.get("Hour", 0)
            m = e.get("Minute", 0)
            times.append(f"{h:02d}:{m:02d}")
        has_weekday = any("Weekday" in e for e in entries)
        has_day = any("Day" in e for e in entries)
        if has_day:
            interval_seconds = 30 * 86400
            schedule_human = f"חודשי {times[0]}" if times else "חודשי"
        elif has_weekday:
            interval_seconds = 7 * 86400
            wd_map = {0: "ראשון", 1: "שני", 2: "שלישי", 3: "רביעי",
                      4: "חמישי", 5: "שישי", 6: "שבת"}
            wds = [wd_map.get(e.get("Weekday", 0), "?") for e in entries]
            schedule_human = f"שבועי ({wds[0]} {times[0]})" if wds and times else "שבועי"
        elif len(entries) <= 1:
            interval_seconds = 86400
            schedule_human = f"יומי {times[0]}" if times else "יומי"
        else:
            interval_seconds = max(3600, 86400 // len(entries))
            schedule_human = f"{len(entries)}x ביום ({', '.join(times[:3])}...)"

    return {
        "standard_out_path": standard_out,
        "interval_seconds": interval_seconds,
        "schedule_human": schedule_human,
    }


def _default_launchctl_runner(label: str) -> str:
    try:
        out = subprocess.run(
            ["launchctl", "list", label],
            capture_output=True, text=True, timeout=10,
        )
        return out.stdout
    except Exception:
        return ""


def probe_launchctl(label: str, runner=None) -> dict:
    runner = runner or _default_launchctl_runner
    out = runner(label) or ""
    pid_match = re.search(r'"PID"\s*=\s*(\d+)', out)
    exit_match = re.search(r'"LastExitStatus"\s*=\s*(-?\d+)', out)
    return {
        "is_running": pid_match is not None,
        "last_exit_code": int(exit_match.group(1)) if exit_match else None,
    }


def derive_status(is_running, last_exit_code, last_run, interval_seconds, now):
    is_stale = False
    if last_run is not None:
        grace = interval_seconds * 2
        age = (now - last_run).total_seconds()
        is_stale = age > grace

    if is_running:
        status = "running"
    elif last_run is None and last_exit_code is None:
        status = "never_ran"
    elif last_exit_code is not None and last_exit_code != 0:
        status = "failed"
    elif is_stale:
        status = "stale"
    elif last_exit_code == 0 or last_run is not None:
        status = "ok"
    else:
        status = "unknown"

    return {"status": status, "is_stale": is_stale}


DEFAULT_LAUNCHAGENTS = os.path.expanduser("~/Library/LaunchAgents")


def _parse_ts(ts):
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.strptime(ts, fmt).replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            continue
    return None


def _collect_os(cfg, launchagents_dir, runner):
    """Yield (label, meta, probe) for the active platform.

    meta:  {standard_out_path, interval_seconds, schedule_human, os_last_run?}
    probe: {is_running, last_exit_code, os_last_run?}

    macOS reads launchd plists + `launchctl`; Windows reads Task Scheduler via
    win_provider. The rest of build_records is identical for both."""
    if sys.platform == "win32":
        try:
            import win_provider
        except Exception:
            return
        for item in win_provider.collect(cfg):
            yield item
        return
    for label in discover_labels(launchagents_dir, cfg):
        plist_path = os.path.join(launchagents_dir, label + ".plist")
        if os.path.isfile(plist_path):
            meta = parse_plist(plist_path)
        else:
            meta = {"standard_out_path": None, "interval_seconds": 86400, "schedule_human": "לא ידוע"}
        probe = probe_launchctl(label, runner=runner)
        yield label, meta, probe


def build_records(cfg, launchagents_dir=DEFAULT_LAUNCHAGENTS, now=None, runner=None, window=None, collector=None):
    now = now or datetime.now(timezone.utc)
    default_sigs = cfg.get("default_error_signatures", [])
    today = now.strftime("%Y-%m-%d")
    records = []

    source = collector(cfg, launchagents_dir, runner) if collector else _collect_os(cfg, launchagents_dir, runner)
    for label, meta, lc in source:
        try:
            agent_cfg = cfg.get("agents", {}).get(label, {})
            name = agent_cfg.get("name", label.split(".")[-1])
            desc = agent_cfg.get("desc", "")
            category = agent_cfg.get("category", "אחר")

            log_cfg = dict(agent_cfg)
            if not log_cfg.get("log_path"):
                log_cfg["log_path"] = meta.get("standard_out_path")
            logdata = extractors.read_log(log_cfg, default_sigs, today=today)

            last_run_dt = None
            duration = None
            if logdata["recent_runs"]:
                last = logdata["recent_runs"][-1]
                last_run_dt = _parse_ts(last.get("end") or last.get("start"))
                s = _parse_ts(last.get("start"))
                e = _parse_ts(last.get("end"))
                if s and e:
                    duration = int((e - s).total_seconds())
            # Fall back to the OS-reported last run (Windows Task Scheduler) when
            # the log has no parseable START/END markers.
            if last_run_dt is None:
                last_run_dt = _parse_ts(meta.get("os_last_run") or lc.get("os_last_run"))

            st = derive_status(lc["is_running"], lc["last_exit_code"],
                               last_run_dt, meta["interval_seconds"], now)

            status = st["status"]
            # Wrapper scripts often exit 0 even when the real work failed inside.
            # If the last parsed run reports failure, trust that over the exit code.
            if status == "ok" and logdata.get("last_run_ok") is False:
                status = "failed"

            # Self-healing: latest run OK but one of the previous 4 runs failed.
            # We track the timestamp of the most recent failure so the UI can
            # show "התאושש אחרי כשל ב-08:03". Window-mode skips this — it has
            # its own success/fail counters.
            self_healed = False
            self_healed_at = None
            recent = logdata["recent_runs"]
            if recent and recent[-1].get("ok") is True:
                prior = recent[-5:-1]
                for r in reversed(prior):
                    if r.get("ok") is False:
                        self_healed = True
                        self_healed_at = r.get("end") or r.get("start")
                        break

            window_runs, window_summary = [], {}
            if window is not None:
                # Read deeper for window mode (need older runs than the 200-line live tail)
                deep_lines = extractors.read_log_lines(
                    os.path.expanduser(log_cfg.get("log_path", "") or ""),
                    max_lines=5000,
                )
                all_runs = [r.to_dict() for r in extractors.parse_startend_runs(deep_lines)]
                sd, ed = window["start_date"], window["end_date"]
                for r in all_runs:
                    rd = (r.get("end") or r.get("start") or "")[:10]
                    if sd <= rd <= ed:
                        window_runs.append(r)
                ok_count = sum(1 for r in window_runs if r.get("ok"))
                window_summary = {
                    "runs": len(window_runs),
                    "successes": ok_count,
                    "fails": len(window_runs) - ok_count,
                }

            records.append(AgentRecord(
                label=label, name=name, category=category, desc=desc,
                status=status, is_running=lc["is_running"],
                last_exit_code=lc["last_exit_code"],
                schedule_human=meta["schedule_human"],
                last_run=last_run_dt.isoformat() if last_run_dt else None,
                last_duration_sec=duration,
                last_result=logdata["last_result"], is_win=logdata["is_win"],
                is_stale=st["is_stale"], has_warnings=logdata["has_warnings"],
                recent_runs=logdata["recent_runs"], log_path=log_cfg.get("log_path"),
                auto_fix=bool(agent_cfg.get("auto_fix", False)),
                self_healed=self_healed, self_healed_at=self_healed_at,
                window_runs=window_runs, window_summary=window_summary,
            ))
        except Exception as exc:
            records.append(AgentRecord(
                label=label, name=label, category="אחר",
                status="unknown", last_result=f"שגיאת פרסור: {exc}"))
    return records


def kickstart(label: str) -> tuple[bool, str]:
    """Run an agent now, cross-platform.

    Windows: `schtasks /run` on the task. macOS: `launchctl kickstart`, with a
    load + retry fallback for agents that were unloaded. Returns (ok, message).
    """
    if sys.platform == "win32":
        try:
            import win_provider
            return win_provider.kickstart(label)
        except Exception as e:  # noqa: BLE001
            return False, str(e)

    def _run(args):
        return subprocess.run(args, capture_output=True, text=True, timeout=10)

    try:
        r = _run(["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/{label}"])
        if r.returncode == 0:
            return True, (r.stdout + r.stderr).strip()
        plist = os.path.expanduser(f"~/Library/LaunchAgents/{label}.plist")
        if not os.path.isfile(plist):
            return False, f"plist לא קיים: {plist}"
        load = _run(["launchctl", "load", plist])
        retry = _run(["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/{label}"])
        if retry.returncode == 0:
            return True, f"נטען וברץ ({(load.stdout + load.stderr).strip()})".strip()
        return False, (retry.stdout + retry.stderr + " | load: " + load.stdout + load.stderr).strip()
    except (subprocess.TimeoutExpired, OSError) as e:
        return False, str(e)
