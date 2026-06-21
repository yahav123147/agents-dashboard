import json
import os
import subprocess
import urllib.parse
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import status_engine
import extractors
import render

DASHBOARD_STATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard_state.json")


def _load_dashboard_state() -> dict:
    try:
        with open(DASHBOARD_STATE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _save_dashboard_state(state: dict) -> None:
    with open(DASHBOARD_STATE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)


def _dismissed_today() -> set:
    today = date.today().isoformat()
    st = _load_dashboard_state()
    if st.get("date") != today:
        return set()
    return set(st.get("dismissed", []))


def _dismiss(label: str) -> None:
    today = date.today().isoformat()
    st = _load_dashboard_state()
    if st.get("date") != today:
        st = {"date": today, "dismissed": []}
    if label not in st["dismissed"]:
        st["dismissed"].append(label)
    _save_dashboard_state(st)


def _kickstart(label: str) -> tuple[bool, str]:
    # Cross-platform: launchd on macOS, Task Scheduler on Windows.
    return status_engine.kickstart(label)


PRESETS = {"today", "yesterday", "7d", "14d", "30d", "week"}


def _parse_window(qs: str):
    """Return ({"start_date","end_date","label"}, preset|None) or (None, None)."""
    if not qs:
        return None, None
    p = urllib.parse.parse_qs(qs)
    preset = (p.get("preset", [None])[0] or "").lower()
    today = date.today()
    if preset in PRESETS:
        if preset == "today":
            s, e, label = today, today, "היום"
        elif preset == "yesterday":
            s, e, label = today - timedelta(days=1), today - timedelta(days=1), "אתמול"
        elif preset == "7d":
            s, e, label = today - timedelta(days=6), today, "7 הימים האחרונים"
        elif preset == "14d":
            s, e, label = today - timedelta(days=13), today, "14 הימים האחרונים"
        elif preset == "30d":
            s, e, label = today - timedelta(days=29), today, "30 הימים האחרונים"
        else:  # week
            s = today - timedelta(days=today.weekday() + 1) if today.weekday() != 6 else today
            e, label = today, "השבוע"
        return {"start_date": s.isoformat(), "end_date": e.isoformat(), "label": label}, preset
    fr = p.get("from", [None])[0]
    to = p.get("to", [None])[0]
    if fr and to and len(fr) == 10 and len(to) == 10:
        return {"start_date": fr, "end_date": to, "label": f"{fr} עד {to}"}, "custom"
    return None, None

PORT = 8420
HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "agents.json")


def _records(window=None):
    cfg = status_engine.load_config(CONFIG_PATH)
    recs = status_engine.build_records(cfg, window=window)
    dismissed = _dismissed_today()
    for r in recs:
        if r.label in dismissed:
            r.dismissed = True
    return recs


def _log_tail_for(label: str):
    cfg = status_engine.load_config(CONFIG_PATH)
    agent_cfg = cfg.get("agents", {}).get(label)
    if agent_cfg is None:
        return None
    path = agent_cfg.get("log_path", "")
    lines = extractors.read_log_lines(path, max_lines=400)
    return "\n".join(lines)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path, qs = parsed.path, parsed.query
        if path == "/api/agents.json":
            window, _ = _parse_window(qs)
            payload = json.dumps([r.to_dict() for r in _records(window)], ensure_ascii=False).encode("utf-8")
            self._send(200, "application/json; charset=utf-8", payload)
        elif path.startswith("/log/"):
            label = path[len("/log/"):]
            tail = _log_tail_for(label)
            if tail is None:
                self.send_response(404)
                self.end_headers()
                return
            self._send(200, "text/plain; charset=utf-8", tail.encode("utf-8"))
        elif path == "/":
            window, preset = _parse_window(qs)
            body = render.render_dashboard(_records(window), window=window, preset=preset).encode("utf-8")
            self._send(200, "text/html; charset=utf-8", body)
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/api/action":
            self.send_response(404)
            self.end_headers()
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            data = json.loads(body or b"{}")
        except (ValueError, json.JSONDecodeError):
            self._send(400, "application/json", b'{"ok":false,"error":"bad json"}')
            return
        action = data.get("action")
        label = data.get("label", "")
        if not label or not isinstance(label, str):
            self._send(400, "application/json", b'{"ok":false,"error":"missing label"}')
            return
        if action == "kickstart":
            ok, msg = _kickstart(label)
            payload = json.dumps({"ok": ok, "msg": msg}, ensure_ascii=False).encode("utf-8")
            self._send(200 if ok else 500, "application/json; charset=utf-8", payload)
        elif action == "dismiss":
            _dismiss(label)
            self._send(200, "application/json", b'{"ok":true}')
        else:
            self._send(400, "application/json", b'{"ok":false,"error":"unknown action"}')

    def _send(self, code, ctype, body):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def main():
    # ThreadingHTTPServer: one thread per connection. The previous single-threaded
    # HTTPServer serialized every request, so the browser's 30s auto-refresh (and any
    # second client) starved each other and loads hung 9-30s. daemon_threads lets the
    # process exit cleanly without waiting on in-flight request threads.
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    server.daemon_threads = True
    print(f"Agents dashboard on http://localhost:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
