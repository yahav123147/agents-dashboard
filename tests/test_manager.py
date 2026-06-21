import unittest
from status_engine import AgentRecord
import manager


def rec(**kw):
    base = dict(label="com.x", name="x", category="content")
    base.update(kw)
    return AgentRecord(**base)


class TestIsProblem(unittest.TestCase):
    def test_never_ran_is_not_a_problem(self):
        self.assertFalse(manager._is_problem(rec(status="never_ran")))

    def test_unknown_is_not_a_problem(self):
        self.assertFalse(manager._is_problem(rec(status="unknown")))

    def test_failed_is_problem(self):
        self.assertTrue(manager._is_problem(rec(status="failed")))

    def test_ok_with_warning_is_problem(self):
        self.assertTrue(manager._is_problem(rec(status="ok", has_warnings=True)))

    def test_clean_ok_is_not_problem(self):
        self.assertFalse(manager._is_problem(rec(status="ok")))


class TestClassify(unittest.TestCase):
    def test_allowlisted_failure_is_autofixed(self):
        action, _ = manager.classify(rec(status="failed", auto_fix=True, category="health"), False)
        self.assertEqual(action, "fix")

    def test_non_allowlisted_failure_escalates(self):
        action, _ = manager.classify(rec(status="failed", auto_fix=False), False)
        self.assertEqual(action, "escalate")

    def test_limit_always_escalates(self):
        action, why = manager.classify(
            rec(status="failed", auto_fix=True, last_result="You've hit your limit resets May 31"), False)
        self.assertEqual(action, "escalate")
        self.assertIn("מכסת MAX", why)

    def test_empty_queue_escalates(self):
        action, why = manager.classify(
            rec(status="stale", auto_fix=True, last_result="תור הנושאים ריק"), False)
        self.assertEqual(action, "escalate")
        self.assertIn("תור נושאים ריק", why)

    def test_warning_reason_is_not_a_success_phrase(self):
        action, why = manager.classify(rec(status="ok", has_warnings=True, last_result="Done"), False)
        self.assertEqual(action, "escalate")
        self.assertNotIn("בהצלחה", why)
        self.assertIn("שווה לבדוק", why)

    def test_content_writer_not_rerun_when_quota_exhausted(self):
        action, why = manager.classify(rec(status="failed", auto_fix=True, category="content"), True)
        self.assertEqual(action, "escalate")
        self.assertIn("מכסת MAX", why)


if __name__ == "__main__":
    unittest.main()
