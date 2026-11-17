"""Network policy: proxies, redirects and FO_OPENJEV_URL parsing, against real local servers.

Proxy rule (fo_jev.direct): plain http and any loopback host never go through a proxy; https to
another host may use the proxy from the environment (the proxy sees only the host name).
"""

from __future__ import annotations

import http.server
import io
import json
import os
import socket
import subprocess
import sys
import threading
import unittest
import urllib.error
import urllib.request
from unittest import mock

from helpers import (FO, OPENJEV_SINK, SCRIPTS, SINK_HEADER, TELEMETRY_SINK, Isolated, Server, clean_env,
                     jev_reply)

import fo_install
import fo_jev


class CountingProxy:
    """A TCP listener that stands in for a hostile proxy: it counts connections and closes them."""

    def __init__(self):
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(16)
        self.url = f"http://127.0.0.1:{self.sock.getsockname()[1]}"
        self.connections = 0
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def _loop(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            self.connections += 1
            conn.close()

    def close(self):
        self.sock.close()


def hostile_env(home: str, proxy: str, **extra) -> dict:
    """Every proxy variable points at `proxy`, and no no_proxy exception exists."""
    env = clean_env(home, FO_TELEMETRY="0", **extra)
    for k in ("no_proxy", "NO_PROXY"):
        env.pop(k, None)
    for k in ("http_proxy", "HTTP_PROXY", "https_proxy", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
        env[k] = proxy
    return env


def get_server(handler_get):
    """A real loopback HTTP server whose GET is handler_get(handler). Returns (httpd, url, hits)."""
    hits = []

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            handler_get(self)

        def log_message(self, *a):
            pass

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}", hits


def models_answer(h):
    out = json.dumps({"models": [{"name": "openjev-latest"}]}).encode()
    h.send_response(200)
    h.send_header("Content-Type", "application/json")
    h.send_header("Content-Length", str(len(out)))
    h.end_headers()
    h.wfile.write(out)


class LoopbackNeverUsesAProxy(unittest.TestCase):
    def setUp(self):
        self.proxy = CountingProxy()

    def tearDown(self):
        self.proxy.close()

    def test_jev_request_to_loopback_bypasses_every_proxy_variable(self):
        with Isolated() as iso, Server(jev_reply()) as srv:
            env = hostile_env(iso.home, self.proxy.url, FO_JEV_BACKEND="openjev", FO_JEV_BASE_URL=srv.url)
            p = subprocess.run([sys.executable, "-I", FO, "jev", "ping"], capture_output=True, text=True,
                               cwd=iso.home, env=env, timeout=60)
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            self.assertEqual(len(srv.requests), 1)
            self.assertEqual(self.proxy.connections, 0, "the prompt went to the proxy")

    def test_installer_probe_bypasses_every_proxy_variable(self):
        httpd, url, hits = get_server(models_answer)
        try:
            with Isolated() as iso:
                code = (f"import sys; sys.path.insert(0, {SCRIPTS!r}); import fo_install; "
                        f"print(fo_install.openjev_models({url!r}))")
                p = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True,
                                   cwd=iso.home, env=hostile_env(iso.home, self.proxy.url), timeout=60)
            self.assertEqual(p.stdout.strip(), "['openjev-latest']", p.stderr)
            self.assertEqual(hits, ["/v1/models"])
            self.assertEqual(self.proxy.connections, 0, "the probe went to the proxy")
        finally:
            httpd.shutdown()
            httpd.server_close()

    def test_telemetry_sender_to_loopback_bypasses_every_proxy_variable(self):
        with Isolated() as iso, Server(lambda *a: (204, {})) as srv:
            p = subprocess.run([sys.executable, "-I", os.path.join(SCRIPTS, "fo_telemetry_send.py"), srv.url + "/e"],
                               input=b'{"event": "fo_doctor"}', capture_output=True, cwd=iso.home,
                               env=hostile_env(iso.home, self.proxy.url), timeout=30)
            self.assertEqual(p.returncode, 0)
            self.assertEqual(len(srv.requests), 1)
            self.assertEqual(self.proxy.connections, 0, "the event went to the proxy")

    def test_https_to_another_host_uses_the_environment_proxy(self):
        # The documented choice: https to a remote host may go through the user's proxy (CONNECT;
        # TLS end to end). The proxy here closes the connection, so the call fails.
        with Isolated() as iso:
            env = hostile_env(iso.home, self.proxy.url, FO_JEV_BACKEND="custom",
                              FO_JEV_BASE_URL="https://jev.invalid")
            p = subprocess.run([sys.executable, "-I", FO, "jev", "ping"], capture_output=True, text=True,
                               cwd=iso.home, env=env, timeout=60)
            self.assertEqual(p.returncode, 1)
            self.assertEqual(self.proxy.connections, 1)

    def test_direct_rule(self):
        for url in ("http://127.0.0.1:8080", "http://gpu-box.example:8080", "https://127.0.0.1",
                    "https://localhost:8443", "https://[::1]:8443", "HTTP://LOCALHOST"):
            self.assertTrue(fo_jev.direct(url), url)
        for url in ("https://api.typesafe.ai", "https://api.codiv.ai/x"):
            self.assertFalse(fo_jev.direct(url), url)


class InstallerProbeRefusesRedirects(unittest.TestCase):
    def test_redirect_is_not_followed(self):
        target, target_url, target_hits = get_server(models_answer)

        def redirect(h):
            h.send_response(302)
            h.send_header("Location", target_url + "/v1/models")
            h.send_header("Content-Length", "0")
            h.end_headers()

        start, start_url, start_hits = get_server(redirect)
        try:
            self.assertEqual(fo_install.openjev_models(start_url), [])
            self.assertEqual(start_hits, ["/v1/models"])
            self.assertEqual(target_hits, [], "the probe followed the redirect")
        finally:
            for h in (start, target):
                h.shutdown()
                h.server_close()


class OpenJevUrlParsing(unittest.TestCase):
    BAD = ("http://[", "http://127.0.0.1:99999", "http://127.0.0.1:port", "http://user@127.0.0.1",
           "http://user:pw@127.0.0.1:8080", "http://localhost.:8080", "http://127.0.0.1.evil.example",
           "http://example.com:8080", "ftp://127.0.0.1", "127.0.0.1:8080")
    GOOD = ("http://127.0.0.1:8080", "HTTP://LOCALHOST:8080", "http://[::1]:8080", "https://jev.example")

    def test_bad_values_are_a_problem_and_never_raise(self):
        for url in self.BAD:
            self.assertTrue(fo_install.openjev_url_problem(url), url)

    def test_good_values_pass(self):
        for url in self.GOOD:
            self.assertIsNone(fo_install.openjev_url_problem(url), url)

    def test_openjev_url_falls_back_for_bad_and_empty_values(self):
        for raw in self.BAD + ("", "   "):
            with Isolated(FO_OPENJEV_URL=raw):
                self.assertEqual(fo_install.openjev_url(), fo_install.OPENJEV_URL, raw)
        with Isolated(FO_OPENJEV_URL="http://[::1]:8080/"):
            self.assertEqual(fo_install.openjev_url(), "http://[::1]:8080")

    def test_check_transport_turns_parse_errors_into_jev_error(self):
        for url in ("http://[", "http://127.0.0.1:99999", "http://u@127.0.0.1"):
            with self.assertRaises(fo_jev.JevError):
                fo_jev.check_transport({"base_url": url})

    def test_installer_warns_and_does_not_crash(self):
        # In process, with the probe replaced: an ignored value falls back to the real default
        # port, and a test must never look there.
        with Isolated(FO_OPENJEV_URL="http://[", FO_TUI="plain"):
            out = io.StringIO()
            with mock.patch.object(fo_install, "openjev_models", lambda url=None: []), \
                    mock.patch("sys.stdin", io.StringIO("")), mock.patch("sys.stdout", out), \
                    mock.patch("sys.stderr", out):
                code = fo_install.run_installer()
            self.assertEqual(code, 130, out.getvalue())  # no answers: cancelled, not crashed
            self.assertIn("FO_OPENJEV_URL must be https://", out.getvalue())
            self.assertIn("It is ignored", out.getvalue())

    def test_custom_address_prompt_validates_with_the_url_parser(self):
        self.assertIsNone(fo_install._address_problem("http://gpu-box.example:8080"))
        self.assertIsNone(fo_install._address_problem(" https://jev.example "))
        for bad in ("gpu-box:8080", "http://[", "http://h.example:99999", "http://u@example.com"):
            self.assertTrue(fo_install._address_problem(bad), bad)


class TestSinksAreOwned(unittest.TestCase):
    def test_sinks_answer_from_this_test_process(self):
        for url in (OPENJEV_SINK + "/v1/models", TELEMETRY_SINK):
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.build_opener(urllib.request.ProxyHandler({})).open(url, timeout=5)
            self.assertEqual(ctx.exception.code, 503)
            self.assertEqual(ctx.exception.headers.get(SINK_HEADER), "1", url)
            ctx.exception.close()


if __name__ == "__main__":
    unittest.main()
