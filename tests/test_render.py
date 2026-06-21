import unittest
from status_engine import AgentRecord
from render import render_dashboard, humanize


class TestRender(unittest.TestCase):
    def _recs(self):
        return [
            AgentRecord(label="a", name="כותב בוקר", category="content", status="failed", last_result="תור הנושאים ריק"),
            AgentRecord(label="b", name="דוח יומי", category="ads", status="ok", last_result="נשלח", is_win=True,
                        log_path="/tmp/x.log", last_run="2026-05-29T06:00:05",
                        recent_runs=[{"start": "2026-05-29T06:00:00", "end": "2026-05-29T06:00:05", "ok": True, "result": "נשלח"}]),
            AgentRecord(label="c", name="פיקסל", category="health", status="ok", has_warnings=True, last_result="has_pii=0"),
        ]

    def test_contains_sections_and_counts(self):
        html = render_dashboard(self._recs())
        self.assertIn("<!DOCTYPE html>", html)
        self.assertIn("צריך תשומת לב", html)
        self.assertIn("כותב בוקר", html)
        self.assertIn("פיקסל", html)
        self.assertIn("דוח יומי", html)
        self.assertIn("1", html)

    def test_row_expansion_details(self):
        html = render_dashboard(self._recs())
        self.assertIn("<details", html)
        self.assertIn("/log/b", html)

    def test_no_em_dash(self):
        html = render_dashboard(self._recs())
        self.assertNotIn("—", html)

    def test_hebrew_status_labels_not_english_enum(self):
        recs = [AgentRecord(label="x", name="x", category="content", status="never_ran")]
        html = render_dashboard(recs)
        self.assertIn("עוד לא רץ", html)
        self.assertNotIn("never_ran", html)

    def test_date_picker_renders_active_preset(self):
        html = render_dashboard(self._recs(), window={"start_date":"2026-05-30","end_date":"2026-05-30","label":"היום"}, preset="today")
        self.assertIn('class="picker"', html)
        self.assertIn("היום", html)
        self.assertIn("preset=today", html)

    def test_window_mode_changes_summary(self):
        # An OK agent with one successful run in window
        recs = [AgentRecord(label="a", name="א", desc="d", category="content", status="ok",
                            window_runs=[{"start":"2026-05-29 10:00:00","end":"2026-05-29 10:00:05","ok":True,"result":"פרסם"}],
                            window_summary={"runs":1,"successes":1,"fails":0}),
                AgentRecord(label="b", name="ב", desc="d", category="content", status="failed",
                            window_runs=[{"start":"2026-05-29 11:00:00","end":"2026-05-29 11:00:02","ok":False,"result":"שגיאה"}],
                            window_summary={"runs":1,"successes":0,"fails":1})]
        html = render_dashboard(recs, window={"start_date":"2026-05-29","end_date":"2026-05-29","label":"אתמול"})
        self.assertIn("בטווח", html)
        self.assertIn("ריצות מוצלחות", html)
        self.assertIn("נכשלו בטווח", html)

    def test_humanize_plain_hebrew(self):
        empty = AgentRecord(label="x", name="x", category="content", status="stale", last_result="TOPICS QUEUE EMPTY")
        self.assertIn("אין נושאים בתור", humanize(empty))
        limit = AgentRecord(label="y", name="y", category="content", status="failed",
                            last_result="Last line: You've hit your limit resets May 31")
        self.assertIn("מכסת השימוש", humanize(limit))
        pii = AgentRecord(label="z", name="z", category="health", status="ok", has_warnings=True,
                          last_result="CAPI is firing without PII")
        self.assertIn("בלי פרטי לקוח", humanize(pii))


if __name__ == "__main__":
    unittest.main()
