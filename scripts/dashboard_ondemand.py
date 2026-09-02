#!/usr/bin/env python3
"""Always-on proxy :8082 → Flask :8083 (BTC bot)."""

from __future__ import annotations

import http.client
import os
import socketserver
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, "/root/scripts")
from dashboard_proxy_common import always_on, idle_monitor, start_flask, wait_ready  # noqa: E402

ROOT = "/root/btc-5m-bot"
VENV_PY = f"{ROOT}/venv/bin/python"
APP = f"{ROOT}/dashboard/app.py"
LOG = f"{ROOT}/logs/dashboard.log"
PUBLIC_PORT = int(os.getenv("DASHBOARD_PUBLIC_PORT", "8082"))
DASH_PORT = int(os.getenv("DASHBOARD_PORT", "8083"))
IDLE_SEC = int(os.getenv("DASHBOARD_IDLE_SEC", "0"))

_proc: subprocess.Popen | None = None
_last_activity = 0.0
_lock = threading.Lock()
_proc_holder = {"proc": None}


def _touch() -> None:
    global _last_activity
    _last_activity = time.time()


def _ensure_dashboard() -> bool:
    global _proc
    with _lock:
        _touch()
        if _proc is not None and _proc.poll() is None:
            return True
        _proc = start_flask(
            venv_py=VENV_PY, app=APP, cwd=f"{ROOT}/dashboard", log_path=LOG, dash_port=DASH_PORT
        )
        _proc_holder["proc"] = _proc
    return wait_ready(DASH_PORT)


class DashboardProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _proxy(self) -> None:
        if not _ensure_dashboard():
            self.send_error(502, "BTC dashboard failed to start")
            return
        _touch()
        try:
            conn = http.client.HTTPConnection("127.0.0.1", DASH_PORT, timeout=120)
            headers = {k: v for k, v in self.headers.items() if k.lower() != "host"}
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length) if length else None
            conn.request(self.command, self.path, body=body, headers=headers)
            resp = conn.getresponse()
            self.send_response(resp.status, resp.reason)
            for key, val in resp.getheaders():
                if key.lower() in ("transfer-encoding", "connection"):
                    continue
                self.send_header(key, val)
            self.end_headers()
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                self.wfile.write(chunk)
            conn.close()
        except Exception as exc:
            self.send_error(502, f"Proxy error: {exc}")

    def do_GET(self) -> None:
        self._proxy()

    def do_HEAD(self) -> None:
        self._proxy()

    def do_POST(self) -> None:
        self._proxy()

    def log_message(self, fmt: str, *args) -> None:
        return


def main() -> None:
    global _proc
    mode = "always-on" if always_on() else f"idle-{IDLE_SEC}s"
    if not _ensure_dashboard():
        raise SystemExit("BTC Flask failed to start")
    if not always_on() and IDLE_SEC > 0:
        stop = threading.Event()
        threading.Thread(
            target=idle_monitor,
            args=(stop, IDLE_SEC, lambda: _last_activity, _proc_holder),
            daemon=True,
        ).start()
    with ThreadingHTTPServer(("0.0.0.0", PUBLIC_PORT), DashboardProxyHandler) as httpd:
        print(f"BTC dashboard proxy :{PUBLIC_PORT} → Flask :{DASH_PORT} ({mode})")
        httpd.serve_forever()


if __name__ == "__main__":
    main()
