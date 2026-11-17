"""tests/e2e/supervise.py: the smoke run's isolation from a real OpenJev, collector and proxy."""

from __future__ import annotations

import http.server
import json
import os
import shlex
import subprocess
import sys
import tempfile
import threading
import unittest

from helpers import ROOT

SUPERVISE = os.path.join(ROOT, "tests", "e2e", "supervise.py")
SMOKE = os.path.join(ROOT, "tests", "e2e", "smoke.sh")
BODY = os.path.join(ROOT, "tests", "e2e", "smoke_body.sh")

# The child prints its environment and the answer it gets from FO_OPENJEV_URL.
CHILD = r"""
import json, os, urllib.request, urllib.error
try:
    urllib.request.urlopen(os.environ["FO_OPENJEV_URL"] + "/v1/models", timeout=5)
    status = 200
except urllib.error.HTTPError as e:
    status = e.code
print(json.dumps({"env": dict(os.environ), "status": status, "ppid": os.getppid()}))
"""


class FakeOpenJev:
    """A stand-in for a real OpenJev (or a hostile proxy): it answers every request with 200 and a
    model list, and with the same text for any token path, and counts what it got."""

    def __init__(self, port: int = 0):
        hits = self.hits = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def _answer(self):
                hits.append(f"{self.command} {self.path}")
                n = int(self.headers.get("Content-Length") or 0)
                if n:
                    self.rfile.read(min(n, 1 << 20))
                body = json.dumps({"data": [{"id": "fake-model"}]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(body)

            do_GET = do_POST = do_PUT = do_HEAD = do_DELETE = do_OPTIONS = do_PATCH = _answer

            def log_message(self, *a):
                pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def fakes() -> list:
    """A fake on a free port, and one on the real 127.0.0.1:8080 when that port is free."""
    out = [FakeOpenJev()]
    try:
        out.append(FakeOpenJev(8080))
    except OSError:
        pass  # a real OpenJev (or something else) holds 8080; the fake on the free port stands in
    return out


def hostile_exports(url: str) -> str:
    """What a parent shell can export before it runs the smoke script."""
    names = {"FO_OPENJEV_URL": url, "FO_TELEMETRY": "1", "FO_TELEMETRY_ENDPOINT": url + "/api/e",
             "FO_JEV_BACKEND": "openjev", "DO_NOT_TRACK": "0", "http_proxy": url, "HTTP_PROXY": url,
             "https_proxy": url, "ALL_PROXY": url, "no_proxy": ""}
    return "export " + " ".join(f"{k}={shlex.quote(v)}" for k, v in names.items())


class SupervisorTests(unittest.TestCase):
    def run_child(self, extra: dict):
        env = {"PATH": "/usr/bin:/bin", "HOME": os.environ.get("HOME", "/tmp"), **extra}
        out = subprocess.run([sys.executable, "-I", SUPERVISE, "--", sys.executable, "-I", "-c", CHILD],
                             capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        first, _, rest = out.stdout.partition("\n")
        return json.loads(first), rest

    def test_scrubs_inherited_settings_and_owns_the_sink(self):
        hostile = {"FO_OPENJEV_URL": "http://127.0.0.1:8080", "FO_JEV_BACKEND": "openjev", "FO_TELEMETRY": "1",
                   "FO_TELEMETRY_ENDPOINT": "https://collector.invalid/api/e", "FO_SMOKE_SUPERVISOR_PID": "1",
                   "FO_CONFIG": "/nowhere", "XDG_CONFIG_HOME": "/nowhere", "TYPESAFE_API_KEY": "x",
                   "http_proxy": "http://127.0.0.1:9", "HTTPS_PROXY": "http://127.0.0.1:9",
                   "ALL_PROXY": "http://127.0.0.1:9", "no_proxy": "*", "BASH_ENV": "/nowhere",
                   "SHELLOPTS": "xtrace", "BASH_FUNC_grep%%": "() { true; }"}
        got, report = self.run_child(hostile)
        env = got["env"]
        sink = env["FO_OPENJEV_URL"]
        self.assertRegex(sink, r"^http://127\.0\.0\.1:\d+$")
        self.assertNotEqual(sink, "http://127.0.0.1:8080")
        self.assertEqual(env["FO_TELEMETRY_ENDPOINT"], sink + "/fo-test-sink")
        self.assertEqual((env["FO_JEV_BACKEND"], env["FO_TELEMETRY"], env["DO_NOT_TRACK"]), ("off", "0", "1"))
        for name in ("FO_CONFIG", "XDG_CONFIG_HOME", "TYPESAFE_API_KEY", "http_proxy", "HTTPS_PROXY",
                     "ALL_PROXY", "no_proxy", "FO_SMOKE_SUPERVISOR_PID", "BASH_ENV", "SHELLOPTS",
                     "BASH_FUNC_grep%%"):
            self.assertNotIn(name, env)
        self.assertEqual(got["status"], 503, "the sink answers 503: no OpenJev")
        self.assertIn("the loopback sink got 1 requests", report)
        self.assertIn("GET /v1/models", report)

    def test_parent_shell_cannot_skip_the_supervisor(self):
        """The old marker check let a parent shell skip supervision: export
        FO_SMOKE_SUPERVISOR_PID=$$ and a hostile FO_OPENJEV_URL, telemetry and proxy, then run
        smoke.sh. Now the entry point always starts the supervisor, and the body proves its sink.
        The whole smoke run passes, the fakes get no request, and the real sink gets the probe."""
        servers = fakes()
        try:
            with tempfile.TemporaryDirectory() as home:
                script = (hostile_exports(servers[0].url) + "; export FO_SMOKE_SUPERVISOR_PID=$$; "
                          + "bash " + shlex.quote(SMOKE) + "; exit $?")  # not exec: $$ stays the parent
                env = {"PATH": "/usr/bin:/bin", "HOME": home, "PYTHON": sys.executable}
                out = subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env,
                                     cwd=home, timeout=300)
        finally:
            for f in servers:
                f.close()
        self.assertEqual(out.returncode, 0, out.stdout[-3000:] + out.stderr[-3000:])
        self.assertIn("== smoke run ok", out.stdout)
        for f in servers:
            self.assertEqual(f.hits, [], f"{f.url} got requests")
        report = out.stdout[out.stdout.rindex("supervisor: the loopback sink got"):]
        self.assertIn("GET /fo-smoke-token", report)

    def test_body_refuses_to_run_without_the_supervisor(self):
        """The body runs only with a token on a pipe that the server at FO_OPENJEV_URL echoes."""
        fake = FakeOpenJev()
        try:
            env = {"PATH": "/usr/bin:/bin", "HOME": tempfile.gettempdir(), "FO_OPENJEV_URL": fake.url,
                   "FO_SMOKE_SUPERVISOR_PID": str(os.getpid())}
            cases = [([], None), (["--token-fd", "9"], None),
                     (["--token-fd", "0"], "0123456789abcdef0123456789abcdef\n")]
            for args, stdin in cases:
                out = subprocess.run(["bash", BODY, *args], input=stdin, capture_output=True, text=True,
                                     env=env, timeout=60)
                self.assertEqual(out.returncode, 3, (args, out.stdout, out.stderr))
                self.assertIn("Run tests/e2e/smoke.sh instead", out.stderr)
                self.assertNotIn("==", out.stdout)
            self.assertEqual(fake.hits, ["GET /fo-smoke-token"], "only the last case reaches the probe")
        finally:
            fake.close()

    def test_smoke_entry_point_has_no_way_around_the_supervisor(self):
        with open(SMOKE, encoding="utf-8") as f:
            code = [ln for ln in f.read().splitlines() if ln and not ln.startswith("#")]
        self.assertEqual(code[-1], 'exec "${PYTHON:-python3}" -I "$ROOT/tests/e2e/supervise.py"')
        self.assertNotIn("FO_", "\n".join(code))


if __name__ == "__main__":
    unittest.main()
