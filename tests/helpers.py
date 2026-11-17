"""Shared test helpers: an isolated HOME and a real local HTTP server (no mocks of urllib)."""

from __future__ import annotations

import http.server
import json
import os
import subprocess
import sys
import tempfile
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL = os.path.join(ROOT, "skills", "frontier-orchestration")
SCRIPTS = os.path.join(SKILL, "scripts")
FO = os.path.join(SCRIPTS, "fo.py")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

SCRUB = ("FO_", "XDG_", "DO_NOT_TRACK", "TYPESAFE_", "CODIV_", "OPENJEV_", "CI", "GITHUB_ACTIONS",
         "CLAUDECODE", "CODEX_SANDBOX", "CODEX_THREAD_ID", "CLAUDE_CONFIG_DIR", "CODEX_HOME")


SINK_HEADER = "X-FO-Test-Sink"


class _Unavailable(http.server.BaseHTTPRequestHandler):
    """Answers every request with 503 and an empty body, at once."""

    def _answer(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n:
            self.rfile.read(min(n, 1 << 20))
        self.send_response(503)
        self.send_header(SINK_HEADER, "1")
        self.send_header("Content-Length", "0")
        self.send_header("Connection", "close")
        self.end_headers()

    do_GET = do_POST = do_PUT = do_HEAD = do_DELETE = do_OPTIONS = _answer

    def log_message(self, *a):
        pass


def _start_sink() -> str:
    """A loopback server that this test process owns for its whole lifetime (port 0: no other
    program holds it, and nothing else can take it while it runs). It answers 503 at once."""
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Unavailable)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{httpd.server_address[1]}"


# Tests must never reach the real collector or a real OpenJev. Both sinks are the server above.
UNAVAILABLE_URL = _start_sink()

# SCRUB removes CI markers (so CI runs behave like a user's machine), which also removes the
# "running in CI" guard; this sink takes its place. A test that checks sending passes its own
# local server instead.
TELEMETRY_SINK = UNAVAILABLE_URL + "/fo-test-sink"

# The installer looks for a running OpenJev at 127.0.0.1:8080, where the people who use this skill
# run it. A real server there would change which prompts the installer shows, so every test points
# that check at the sink (an answer of 503 means "no OpenJev"). A test that needs a running
# OpenJev passes FO_OPENJEV_URL of its own local server.
OPENJEV_SINK = UNAVAILABLE_URL


def clean_env(home: str, **extra) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith(SCRUB)}
    env.update({"HOME": home, "PATH": "/usr/bin:/bin", "FO_HOST": "unknown", "FO_PATH_EXTRA": "",
                "CLAUDE_CONFIG_DIR": os.path.join(home, ".claude"), "CODEX_HOME": os.path.join(home, ".codex"),
                "FO_TELEMETRY_ENDPOINT": TELEMETRY_SINK, "FO_OPENJEV_URL": OPENJEV_SINK})
    env.update({k: str(v) for k, v in extra.items()})
    return env


def read_text(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def read_json(path: str):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


class Isolated:
    """Context manager: temp HOME, patched os.environ, and the temp HOME as the current folder.

    The current folder matters: a relative path in a test (or in the code under test) must land
    in the temp HOME, never in the checkout the tests run from. Child processes inherit it."""

    def __init__(self, **extra):
        self.extra = extra

    def __enter__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = self.tmp.name
        self.saved = dict(os.environ)
        self.cwd = os.getcwd()
        os.environ.clear()
        os.environ.update(clean_env(self.home, **self.extra))
        os.chdir(self.home)
        return self

    def __exit__(self, *exc):
        os.chdir(self.cwd)
        os.environ.clear()
        os.environ.update(self.saved)
        self.tmp.cleanup()


def run_fo(home: str, *args, env_extra: dict | None = None, stdin: str = "") -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-I", FO, *args], capture_output=True, text=True, cwd=home,
                          env=clean_env(home, **(env_extra or {})), input=stdin, timeout=60)


class Server:
    """A real HTTP server on 127.0.0.1. `reply(path, body, headers)` returns (status, json)."""

    def __init__(self, reply):
        self.requests = []
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(n)
                body = json.loads(raw or b"{}")
                outer.requests.append({"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}, "body": body})
                status, data = reply(self.path, body, self.headers)
                out = json.dumps(data).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

            def log_message(self, *a):
                pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()


def jev_reply(choices: dict | None = None, stakes: float = 0.9, confidence: float = 0.95):
    """A System One server that picks choices[task] (or the first option) for every route_ question."""
    choices = choices or {}

    def reply(path, body, headers):
        if path != "/v1/systemone":
            return 404, {"detail": "not found"}
        answers = {}
        for qid, q in body["questions"].items():
            if q["type"] == "noul":
                answers[qid] = {"noul": stakes}
            elif q["type"] == "choice":
                tid = qid[len("route_"):]
                pick = choices.get(tid, next(iter(q["criteria"])))
                answers[qid] = {"choice": pick, "confidence": confidence,
                                "probabilities": {k: (1.0 if k == pick else 0.0) for k in q["criteria"]}}
        return 200, {"model": body.get("model"), "answers": answers, "usage": {"input_tokens": 10}}

    return reply
