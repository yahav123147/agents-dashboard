import unittest
import os

from extractors import (
    read_log_lines, parse_startend_runs, scan_warnings, read_log,
)

FIX_LOGS = os.path.join(os.path.dirname(__file__), "fixtures", "logs")
FIX_STATE = os.path.join(os.path.dirname(__file__), "fixtures", "state")
FIX_MARK = os.path.join(os.path.dirname(__file__), "fixtures", "markers")
DEFAULT_SIGS = ["error", "Forbidden", "WARN", "Traceback", "FAIL"]
TIGHT_SIGS = ["traceback", "forbidden", "exception", "failed ", "error:", "http 4", "http 5"]


class TestReadLog(unittest.TestCase):
    def test_missing_returns_empty(self):
        self.assertEqual(read_log_lines("/no/such/file.log"), [])

    def test_empty_returns_empty(self):
        self.assertEqual(read_log_lines(os.path.join(FIX_LOGS, "empty.log")), [])

    def test_reads_lines(self):
        lines = read_log_lines(os.path.join(FIX_LOGS, "startend.log"))
        self.assertTrue(any("START" in ln for ln in lines))


class TestParseRuns(unittest.TestCase):
    def test_parses_two_runs(self):
        lines = read_log_lines(os.path.join(FIX_LOGS, "startend.log"))
        runs = parse_startend_runs(lines)
        self.assertEqual(len(runs), 2)
        self.assertEqual(runs[-1].start, "2026-05-29 11:00:00")
        self.assertEqual(runs[-1].end, "2026-05-29 11:00:02")

    def test_exit_marker_failed_run(self):
        # real format: "END (exit=1) ===" must be recognized and marked not ok
        lines = read_log_lines(os.path.join(FIX_LOGS, "failed_run.log"))
        runs = parse_startend_runs(lines)
        self.assertEqual(len(runs), 1)
        self.assertFalse(runs[-1].ok)


class TestWarnings(unittest.TestCase):
    def test_detects_403(self):
        lines = read_log_lines(os.path.join(FIX_LOGS, "python_freeform.log"))
        self.assertTrue(scan_warnings(lines, DEFAULT_SIGS))

    def test_clean_log_no_warning(self):
        self.assertFalse(scan_warnings(["all good", "Done"], DEFAULT_SIGS))

    def test_tight_sigs_fail_equals_zero_not_warning(self):
        self.assertFalse(scan_warnings(["DONE: ok=1 fail=0"], TIGHT_SIGS))

    def test_tight_sigs_skip_message_not_warning(self):
        self.assertFalse(scan_warnings(["Already approved (flag exists), skipping check"], TIGHT_SIGS))

    def test_tight_sigs_real_failure_warns(self):
        self.assertTrue(scan_warnings(["FAILED (exit=1) API Error: timeout"], TIGHT_SIGS))

    def test_old_failure_recovered_does_not_warn(self):
        # last run is clean; the failure is weeks old -> must not warn
        cfg = {"result_hint": "generic", "log_path": os.path.join(FIX_LOGS, "recovered.log")}
        out = read_log(cfg, DEFAULT_SIGS)
        self.assertFalse(out["has_warnings"])
        self.assertEqual(out["last_result"], "published fine")
        self.assertEqual(len(out["recent_runs"]), 2)

    def test_recent_failure_does_warn(self):
        # most recent run contains a 403 -> warns
        cfg = {"result_hint": "python_freeform", "log_path": os.path.join(FIX_LOGS, "python_freeform.log")}
        out = read_log(cfg, DEFAULT_SIGS)
        self.assertTrue(out["has_warnings"])


class TestReadLogDispatch(unittest.TestCase):
    def test_article_writer_uses_state(self):
        cfg = {"result_hint": "article_writer",
               "log_path": os.path.join(FIX_LOGS, "startend.log"),
               "state_file": os.path.join(FIX_STATE, "state_demo.json")}
        out = read_log(cfg, DEFAULT_SIGS)
        # startend.log ends with TOPICS QUEUE EMPTY, so this is the empty-queue path
        self.assertEqual(out["last_result"], "תור הנושאים ריק")

    def test_article_writer_published(self):
        # a clean log (no empty marker) + state file -> published URL
        cfg = {"result_hint": "article_writer",
               "log_path": os.path.join(FIX_LOGS, "nl_report.log"),
               "state_file": os.path.join(FIX_STATE, "state_demo.json")}
        out = read_log(cfg, DEFAULT_SIGS)
        self.assertIn("some-article", out["last_result"])
        self.assertTrue(out["is_win"])

    def test_python_freeform_warns(self):
        cfg = {"result_hint": "python_freeform",
               "log_path": os.path.join(FIX_LOGS, "python_freeform.log")}
        out = read_log(cfg, DEFAULT_SIGS)
        self.assertTrue(out["has_warnings"])

    def test_article_writer_failed_shows_reason_not_stale_url(self):
        # last run failed; must show the failure, NOT the previous success URL
        cfg = {"result_hint": "article_writer",
               "log_path": os.path.join(FIX_LOGS, "failed_run.log"),
               "state_file": os.path.join(FIX_STATE, "state_demo.json")}
        out = read_log(cfg, TIGHT_SIGS)
        self.assertFalse(out["is_win"])
        self.assertIn("API Error", out["last_result"])
        self.assertIs(out["last_run_ok"], False)
        self.assertTrue(out["has_warnings"])

    def test_line_prefixed_skip(self):
        cfg = {"result_hint": "line_prefixed",
               "log_path": os.path.join(FIX_LOGS, "line_prefixed.log")}
        out = read_log(cfg, DEFAULT_SIGS)
        self.assertIn("כבר הושלם היום", out["last_result"])

    def test_marker_file_today(self):
        cfg = {"result_hint": "marker_file",
               "log_path": os.path.join(FIX_LOGS, "line_prefixed.log"),
               "marker_glob": os.path.join(FIX_MARK, "last-success-*.marker")}
        out = read_log(cfg, DEFAULT_SIGS, today="2026-05-29")
        self.assertIn("הצליח היום", out["last_result"])

    def test_generic_last_line(self):
        cfg = {"result_hint": "generic",
               "log_path": os.path.join(FIX_LOGS, "python_freeform.log")}
        out = read_log(cfg, DEFAULT_SIGS)
        self.assertEqual(out["last_result"], "Done")

    def test_nl_report_result(self):
        cfg = {"result_hint": "nl_report",
               "log_path": os.path.join(FIX_LOGS, "nl_report.log")}
        out = read_log(cfg, DEFAULT_SIGS)
        self.assertIn('"ok":true', out["last_result"])
        self.assertEqual(len(out["recent_runs"]), 1)


class TestGlobLog(unittest.TestCase):
    def test_glob_picks_newest(self):
        lines = read_log_lines(os.path.join(FIX_LOGS, "*.log"))
        self.assertIsInstance(lines, list)


if __name__ == "__main__":
    unittest.main()
