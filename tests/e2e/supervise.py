#!/usr/bin/env python3
"""Run the smoke body with no way out to a real OpenJev, the real collector or a proxy.

    python3 -I tests/e2e/supervise.py              # what tests/e2e/smoke.sh runs
    python3 -I tests/e2e/supervise.py -- COMMAND   # the same isolation for COMMAND (tests use it)

With no arguments it runs `bash tests/e2e/smoke_body.sh --token-fd N`, found next to this file.
No environment variable selects that path, and tests/e2e/smoke.sh always starts this supervisor:
there is no marker that skips it. The supervisor:

- starts a loopback HTTP server on a free port (port 0: no other program holds it) that answers
  every request with 503 at once, and keeps it for the whole run;
- makes a one-time token and gives it to the body on a pipe (file descriptor N, named on the
  command line). The server answers GET /fo-smoke-token with that token once, so the body can
  prove that the server at FO_OPENJEV_URL is this supervisor's, and refuses to run if not;
- removes every inherited FO_*, XDG_*, TYPESAFE_*, CODIV_*, OPENJEV_* and proxy variable
  (http_proxy, https_proxy, all_proxy, no_proxy, ftp_proxy, in any case), and the bash start-up
  hooks (BASH_ENV, ENV, SHELLOPTS, BASHOPTS, PS4, CDPATH, GLOBIGNORE and exported functions);
- points FO_OPENJEV_URL (the installer's OpenJev check) and FO_TELEMETRY_ENDPOINT at that
  server, and sets FO_JEV_BACKEND=off, FO_TELEMETRY=0, DO_NOT_TRACK=1 and PYTHON (its own
  interpreter);
- prints the requests the server got, then exits with the body's exit code.

So a real OpenJev on 127.0.0.1:8080 cannot see or change the run. Standard library only.
"""

from __future__ import annotations

import http.server
import os
import secrets
import subprocess
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
BODY = os.path.join(HERE, "smoke_body.sh")
TOKEN_PATH = "/fo-smoke-token"
SCRUB_PREFIXES = ("FO_", "XDG_", "TYPESAFE_", "CODIV_", "OPENJEV_", "BASH_FUNC_")
SCRUB_NAMES = {"BASH_ENV", "ENV", "SHELLOPTS", "BASHOPTS", "PS4", "CDPATH", "GLOBIGNORE"}
PROXY_NAMES = {"http_proxy", "https_proxy", "all_proxy", "no_proxy", "ftp_proxy"}


class Sink(http.server.BaseHTTPRequestHandler):
    hits: list = []
    lock = threading.Lock()
    token = ""
    token_given = False

    def _answer(self):
        with self.lock:
            self.hits.append(f"{self.command} {self.path}")
            give = self.command == "GET" and self.path == TOKEN_PATH and not Sink.token_given
            if give:
                Sink.token_given = True
        n = int(self.headers.get("Content-Length") or 0)
        if n:
            self.rfile.read(min(n, 1 << 20))
        body = Sink.token.encode() if give else b""
        self.send_response(200 if give else 503)
        self.send_header("X-FO-Test-Sink", "1")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    do_GET = do_POST = do_PUT = do_HEAD = do_DELETE = do_OPTIONS = do_PATCH = _answer

    def log_message(self, *a):
        pass


def clean_env(environ: dict, sink: str) -> dict:
    env = {k: v for k, v in environ.items()
           if not k.startswith(SCRUB_PREFIXES) and k not in SCRUB_NAMES and k.lower() not in PROXY_NAMES}
    env.update({"FO_OPENJEV_URL": sink, "FO_TELEMETRY_ENDPOINT": sink + "/fo-test-sink",
                "FO_JEV_BACKEND": "off", "FO_TELEMETRY": "0", "DO_NOT_TRACK": "1",
                "PYTHON": sys.executable})
    return env


def main(argv: list) -> int:
    if not argv:
        command = None
    elif argv[0] == "--" and len(argv) >= 2:
        command = argv[1:]
    else:
        print("usage: supervise.py [-- COMMAND [ARG...]]", file=sys.stderr)
        return 2
    Sink.token = secrets.token_hex(16)
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Sink)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    sink = f"http://127.0.0.1:{httpd.server_address[1]}"
    read_end, write_end = os.pipe()
    os.write(write_end, Sink.token.encode() + b"\n")
    os.close(write_end)
    if command is None:
        command = ["bash", BODY, "--token-fd", str(read_end)]
    try:
        code = subprocess.run(command, env=clean_env(dict(os.environ), sink), pass_fds=(read_end,)).returncode
    finally:
        os.close(read_end)
        httpd.shutdown()
        httpd.server_close()
    print(f"\nsupervisor: the loopback sink got {len(Sink.hits)} requests"
          + "".join(f"\n  {h}" for h in Sink.hits))
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
