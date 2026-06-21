import json
import unittest
from datetime import datetime, timezone

import win_provider
import status_engine

# Shape that the PowerShell query emits (one object per scheduled task).
SAMPLE = [
    {"name": "MyAgent-backup", "path": "\\", "state": "Ready",
     "last": "2026-06-21 06:00:00", "next": "2026-06-22 06:00:00", "result": 0},
    {"name": "MyAgent-sync", "path": "\\Tools\\", "state": "Running",
     "last": "2026-06-21 11:30:00", "next": "2026-06-21 12:00:00", "result": 267009},
    {"name": "MyAgent-broken", "path": "\\", "state": "Ready",
     "last": "2026-06-21 05:00:00", "next": "2026-06-22 05:00:00", "result": 1},
    {"name": "MyAgent-fresh", "path": "\\", "state": "Ready",
     "last": None, "next": "2026-06-22 05:00:00", "result": 267011},
    {"name": "Unrelated", "path": "\\", "state": "Ready",
     "last": "2026-06-21 05:00:00", "next": None, "result": 0},
]


def _runner(payload):
    return lambda: json.dumps(payload)


class TestQueryTasks(unittest.TestCase):
    def test_array_parsed(self):
        self.assertEqual(len(win_provider.query_tasks(_runner(SAMPLE))), 5)

    def test_single_object_normalized_to_list(self):
        # ConvertTo-Json emits a bare object for one task, not an array
        out = win_provider.query_tasks(_runner(SAMPLE[0]))
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["name"], "MyAgent-backup")

    def test_bad_json_is_empty(self):
        self.assertEqual(win_provider.query_tasks(lambda: "not json"), [])


class TestCollect(unittest.TestCase):
    def _by_label(self, includes, excludes=None):
        cfg = {"include_prefixes": includes, "exclude_patterns": excludes or []}
        return {label: (meta, probe)
                for label, meta, probe in win_provider.collect(cfg, runner=_runner(SAMPLE))}

    def test_prefix_filter_drops_unrelated(self):
        got = self._by_label(["MyAgent"])
        self.assertIn("\\MyAgent-backup", got)
        self.assertIn("\\Tools\\MyAgent-sync", got)   # label includes subfolder path
        self.assertNotIn("\\Unrelated", got)

    def test_exclude_pattern(self):
        got = self._by_label(["MyAgent"], ["broken"])
        self.assertNotIn("\\MyAgent-broken", got)
        self.assertIn("\\MyAgent-backup", got)

    def test_success_maps_exit_zero(self):
        meta, probe = self._by_label(["MyAgent"])["\\MyAgent-backup"]
        self.assertEqual(probe["last_exit_code"], 0)
        self.assertFalse(probe["is_running"])
        self.assertEqual(meta["schedule_human"], "יומי")   # 24h between last and next

    def test_running_task(self):
        meta, probe = self._by_label(["MyAgent"])["\\Tools\\MyAgent-sync"]
        self.assertTrue(probe["is_running"])
        self.assertIsNone(probe["last_exit_code"])         # 267009 sentinel -> not an exit code

    def test_failed_task(self):
        meta, probe = self._by_label(["MyAgent"])["\\MyAgent-broken"]
        self.assertEqual(probe["last_exit_code"], 1)

    def test_never_ran(self):
        meta, probe = self._by_label(["MyAgent"])["\\MyAgent-fresh"]
        self.assertIsNone(probe["last_exit_code"])         # 267011 sentinel
        self.assertIsNone(probe["os_last_run"])


class TestWindowsConsumption(unittest.TestCase):
    """build_records must turn a Windows-style (label, meta, probe) into a
    correct AgentRecord, including the OS-last-run fallback when no log exists."""

    def test_record_from_os_last_run_no_log(self):
        def collector(cfg, lad, runner):
            yield ("\\MyAgent-x",
                   {"standard_out_path": None, "interval_seconds": 86400,
                    "schedule_human": "יומי", "os_last_run": "2026-06-21 11:00:00"},
                   {"is_running": False, "last_exit_code": 0,
                    "os_last_run": "2026-06-21 11:00:00"})

        cfg = {"default_error_signatures": [],
               "agents": {"\\MyAgent-x": {"name": "גיבוי", "category": "health",
                                          "result_hint": "generic"}}}
        now = datetime(2026, 6, 21, 12, 0, 0, tzinfo=timezone.utc)
        recs = status_engine.build_records(cfg, now=now, collector=collector)
        self.assertEqual(len(recs), 1)
        r = recs[0]
        self.assertEqual(r.label, "\\MyAgent-x")
        self.assertEqual(r.name, "גיבוי")
        self.assertIsNotNone(r.last_run)        # fell back to OS-reported time
        self.assertEqual(r.status, "ok")

    def test_failed_exit_from_os(self):
        def collector(cfg, lad, runner):
            yield ("\\MyAgent-y",
                   {"standard_out_path": None, "interval_seconds": 86400,
                    "schedule_human": "יומי", "os_last_run": "2026-06-21 11:00:00"},
                   {"is_running": False, "last_exit_code": 1,
                    "os_last_run": "2026-06-21 11:00:00"})

        cfg = {"default_error_signatures": [],
               "agents": {"\\MyAgent-y": {"name": "x", "category": "health"}}}
        now = datetime(2026, 6, 21, 12, 0, 0, tzinfo=timezone.utc)
        r = status_engine.build_records(cfg, now=now, collector=collector)[0]
        self.assertEqual(r.status, "failed")


if __name__ == "__main__":
    unittest.main()
