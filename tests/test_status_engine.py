import unittest
import os
import json
import tempfile
from datetime import datetime, timezone, timedelta

from status_engine import (
    AgentRecord, load_config, discover_labels, parse_plist,
    probe_launchctl, derive_status, build_records,
)

FIX_LA = os.path.join(os.path.dirname(__file__), "fixtures", "launchagents")
FIX_LOGS = os.path.join(os.path.dirname(__file__), "fixtures", "logs")


class TestAgentRecord(unittest.TestCase):
    def test_defaults(self):
        r = AgentRecord(label="com.x", name="X", category="content")
        self.assertEqual(r.status, "unknown")
        self.assertFalse(r.is_running)
        self.assertEqual(r.recent_runs, [])
        self.assertIsNone(r.last_run)


class TestLoadConfig(unittest.TestCase):
    def test_expands_user_and_returns_dict(self):
        data = {"include_prefixes": ["com.example"], "exclude_patterns": ["other"],
                "default_error_signatures": ["error"], "agents": {}}
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(data, f)
            path = f.name
        cfg = load_config(path)
        os.unlink(path)
        self.assertEqual(cfg["include_prefixes"], ["com.example"])
        self.assertIn("agents", cfg)


class TestDiscoverLabels(unittest.TestCase):
    def test_includes_prefix_excludes_patterns(self):
        cfg = {"include_prefixes": ["com.example", "com.claude"],
               "exclude_patterns": ["other", "com.apple"]}
        labels = discover_labels(FIX_LA, cfg)
        self.assertIn("com.example.alpha", labels)
        self.assertNotIn("com.other.tunnel", labels)
        self.assertNotIn("com.apple.something", labels)


class TestParsePlist(unittest.TestCase):
    def test_interval(self):
        meta = parse_plist(os.path.join(FIX_LA, "com.example.interval.plist"))
        self.assertEqual(meta["interval_seconds"], 900)
        self.assertEqual(meta["standard_out_path"], "/tmp/interval.log")
        self.assertIn("15", meta["schedule_human"])

    def test_calendar_daily(self):
        meta = parse_plist(os.path.join(FIX_LA, "com.example.alpha.plist"))
        self.assertEqual(meta["interval_seconds"], 86400)
        self.assertIn("11:00", meta["schedule_human"])


RUNNING_OUT = '{\n\t"PID" = 4321;\n\t"LastExitStatus" = 0;\n\t"Label" = "com.x";\n};\n'
EXITED_FAIL_OUT = '{\n\t"LastExitStatus" = 78;\n\t"Label" = "com.x";\n};\n'


class TestProbeLaunchctl(unittest.TestCase):
    def test_running(self):
        r = probe_launchctl("com.x", runner=lambda label: RUNNING_OUT)
        self.assertTrue(r["is_running"])
        self.assertEqual(r["last_exit_code"], 0)

    def test_exited_fail(self):
        r = probe_launchctl("com.x", runner=lambda label: EXITED_FAIL_OUT)
        self.assertFalse(r["is_running"])
        self.assertEqual(r["last_exit_code"], 78)

    def test_unknown_label(self):
        r = probe_launchctl("com.x", runner=lambda label: "")
        self.assertFalse(r["is_running"])
        self.assertIsNone(r["last_exit_code"])


class TestDeriveStatus(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 5, 29, 12, 0, tzinfo=timezone.utc)

    def test_running(self):
        s = derive_status(True, 0, self.now - timedelta(minutes=1), 900, self.now)
        self.assertEqual(s["status"], "running")

    def test_never_ran(self):
        s = derive_status(False, None, None, 86400, self.now)
        self.assertEqual(s["status"], "never_ran")

    def test_failed(self):
        s = derive_status(False, 78, self.now - timedelta(hours=1), 86400, self.now)
        self.assertEqual(s["status"], "failed")

    def test_ok(self):
        s = derive_status(False, 0, self.now - timedelta(hours=1), 86400, self.now)
        self.assertEqual(s["status"], "ok")
        self.assertFalse(s["is_stale"])

    def test_stale(self):
        s = derive_status(False, 0, self.now - timedelta(days=3), 86400, self.now)
        self.assertEqual(s["status"], "stale")
        self.assertTrue(s["is_stale"])


class TestBuildRecords(unittest.TestCase):
    def test_builds_and_isolates_errors(self):
        cfg = {
            "include_prefixes": ["com.example"],
            "exclude_patterns": ["other"],
            "default_error_signatures": ["error", "WARN"],
            "agents": {
                "com.example.alpha": {
                    "name": "אלפא", "category": "content",
                    "result_hint": "article_writer",
                    "log_path": os.path.join(FIX_LOGS, "startend.log"),
                },
            },
        }
        recs = build_records(cfg, launchagents_dir=FIX_LA,
                             now=datetime(2026, 5, 29, 12, 0, tzinfo=timezone.utc),
                             runner=lambda label: '{\n\t"LastExitStatus" = 0;\n};\n')
        by_label = {r.label: r for r in recs}
        self.assertIn("com.example.alpha", by_label)
        alpha = by_label["com.example.alpha"]
        self.assertEqual(alpha.name, "אלפא")
        self.assertEqual(alpha.category, "content")
        self.assertIn(alpha.status, ("ok", "stale", "failed"))
        self.assertIn("com.example.interval", by_label)
        self.assertEqual(by_label["com.example.interval"].category, "אחר")

    def test_failed_run_elevates_status_despite_exit_zero(self):
        # wrapper exits 0 but the run log says exit=1 -> dashboard must show failed
        cfg = {
            "include_prefixes": ["com.example"],
            "exclude_patterns": ["other", "interval"],
            "default_error_signatures": ["failed ", "error:"],
            "agents": {
                "com.example.alpha": {
                    "name": "א", "category": "content", "result_hint": "article_writer",
                    "log_path": os.path.join(FIX_LOGS, "failed_run.log"),
                },
            },
        }
        recs = build_records(cfg, launchagents_dir=FIX_LA,
                             now=datetime(2026, 5, 29, 12, 0, tzinfo=timezone.utc),
                             runner=lambda label: '{\n\t"LastExitStatus" = 0;\n};\n')
        alpha = {r.label: r for r in recs}["com.example.alpha"]
        self.assertEqual(alpha.status, "failed")


if __name__ == "__main__":
    unittest.main()
