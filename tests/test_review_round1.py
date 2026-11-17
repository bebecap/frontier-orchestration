"""Regression tests for the round-1 cross-vendor review findings (all reproduced first)."""

from __future__ import annotations

import io
import os
import subprocess
import sys
import threading
import unittest
import urllib.error
from unittest import mock

from helpers import ROOT, SCRIPTS, SKILL, Isolated, Server, read_text

import fo_config
import fo_jev
import fo_providers
import fo_refresh
import fo_route
import fo_telemetry

sys.path.insert(0, SCRIPTS)
import fo_telemetry_send  # noqa: E402


class JevSecrets(unittest.TestCase):
    def test_error_body_echoing_the_key_is_redacted(self):
        key = "fake-secret-key-123456"
        reply = lambda p, b, h: (401, {"detail": f"bad token {h.get('Authorization')}"})  # noqa: E731
        with Isolated(MY_KEY=key), Server(reply) as srv:
            js = {"backend": "custom", "base_url": srv.url, "model": "m", "api_key_env": "MY_KEY",
                  "key_required": True, "timeout_s": 10}
            with self.assertRaises(fo_jev.JevError) as ctx:
                fo_jev.ping(js)
            self.assertNotIn(key, str(ctx.exception))
            self.assertNotIn("bad token", str(ctx.exception), "the server body is never shown")
            self.assertIn("API key was refused", str(ctx.exception))
            self.assertIsNone(ctx.exception.__cause__)

    def test_redirect_is_refused_and_key_not_forwarded(self):
        hits = []

        def target(p, b, h):
            hits.append(dict(h))
            return 200, {"answers": {"ok": {"noul": 1.0}}}

        with Isolated(MY_KEY="fake-secret-key-123456"), Server(target) as other:
            import http.server

            class R(http.server.BaseHTTPRequestHandler):
                def do_POST(self):
                    self.send_response(302)
                    self.send_header("Location", other.url + "/v1/systemone")
                    self.end_headers()

                def log_message(self, *a):
                    pass

            redirector = http.server.ThreadingHTTPServer(("127.0.0.1", 0), R)
            threading.Thread(target=redirector.serve_forever, daemon=True).start()
            try:
                js = {"backend": "custom", "base_url": f"http://127.0.0.1:{redirector.server_address[1]}",
                      "model": "m", "api_key_env": "MY_KEY", "key_required": True, "timeout_s": 10}
                with self.assertRaises(fo_jev.JevError) as ctx:
                    fo_jev.ping(js)
                self.assertEqual(ctx.exception.status, 302)
                self.assertEqual(hits, [], "the key must never reach the redirect target")
            finally:
                redirector.shutdown()
                redirector.server_close()


class AgyGuard(unittest.TestCase):
    def test_no_unguarded_agy_without_opt_in(self):
        calls = []

        def fake_run(cmd, timeout):
            calls.append(cmd)
            return 1, "Please sign in"

        with mock.patch.object(fo_refresh, "run", fake_run), mock.patch.object(fo_refresh.os.path, "exists",
                                                                               return_value=True):
            gem, err = fo_refresh.gemini_models("/bin/agy")
            self.assertEqual(gem, {})
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][0], fo_refresh.SANDBOX)
            self.assertIn("agy_keychain_fallback", err)
            calls.clear()
            fo_refresh.gemini_models("/bin/agy", keychain_fallback=True)
            self.assertEqual(len(calls), 2)
            self.assertEqual(calls[1][0], "/bin/agy")


class Independence(unittest.TestCase):
    def roster(self):
        with Isolated():
            return fo_route.Roster(fo_config.roster())

    def test_boolean_true_is_enforced(self):
        spec = {"tasks": {"rev": {"needs_independence": True, "producer_vendor": "claude",
                                  "candidates": ["opus", "gpt-6-astra"]}}}
        out = fo_route.route(spec, self.roster(), {"backend": "off"})
        self.assertEqual(out["rev"]["model"], "gpt-6-astra")

    def test_unknown_value_fails_closed(self):
        spec = {"tasks": {"rev": {"needs_independence": "maybe", "producer_vendor": "claude",
                                  "candidates": ["opus"]}}}
        with self.assertRaises(fo_route.RouteError):
            fo_route.route(spec, self.roster(), {"backend": "off"})
        self.assertTrue(fo_route.self_check(spec, self.roster()))

    def test_plain_no_and_missing(self):
        self.assertFalse(fo_route.needs_independence({"needs_independence": "no"}))
        self.assertFalse(fo_route.needs_independence({}))
        self.assertTrue(fo_route.needs_independence({"needs_independence": "YES - must differ"}))


class ForcedTransport(unittest.TestCase):
    def test_missing_forced_transport_is_not_reachable(self):
        with Isolated():
            cfg = fo_config.deep_merge(fo_config.load(), {"providers": {"codex": {"via": "cli"},
                                                                         "gemini": {"enabled": False}}})
            res = fo_providers.resolve(cfg, host="claude-code")
            self.assertEqual(res["codex"]["via"], "")
            self.assertIn("not reachable", res["codex"]["note"])
            self.assertIn("codex exec", res["codex"]["how"])
            self.assertEqual(fo_providers.enabled_vendors(res), {"claude"})
            self.assertIn("independent review possible: NO", fo_providers.render(res, "claude-code"))

    def test_a_command_override_in_the_config_is_ignored_and_reported(self):
        # providers.<vendor>.command was removed: it could replace the read-only command line.
        import subprocess, sys
        from helpers import FO, clean_env
        with Isolated() as iso:
            fo_config.write_file({"providers": {"codex": {"command": "codex exec --sandbox workspace-write -"}}})
            cfg = fo_config.load()
            self.assertNotIn("command", cfg["providers"]["codex"])
            self.assertNotIn("command", fo_config.DEFAULTS["providers"]["codex"])
            a, _, _ = fo_providers.argv("codex", "cli", model=None, effort=None, cwd=iso.home,
                                        out_file=os.path.join(iso.home, "o"), prompt="x")
            self.assertEqual(a[a.index("--sandbox") + 1], "read-only")
            p = subprocess.run([sys.executable, "-I", FO, "doctor"], capture_output=True, text=True,
                               env=clean_env(iso.home), timeout=60)
            self.assertIn("note providers.codex.command was removed and is ignored", p.stdout)

    def test_installer_drops_a_removed_command_override(self):
        import subprocess, sys
        from helpers import FO, clean_env
        from test_installer import signed_in_clis
        with Isolated() as iso:
            os.makedirs(os.path.join(iso.home, "bin"))
            signed_in_clis(os.path.join(iso.home, "bin"))
            fo_config.write_file({"providers": {"codex": {"command": "codex exec --sandbox workspace-write -"}}})
            env = clean_env(iso.home, PATH=os.path.join(iso.home, "bin") + ":/usr/bin:/bin", FO_TUI="plain")
            p = subprocess.run([sys.executable, "-I", FO, "install"], input="4\n1,2\n2\n\n", capture_output=True,
                               text=True, env=env, timeout=60)
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            self.assertNotIn("command", fo_config.read_file()["providers"]["codex"])
            fo_config.load()  # loads again


class SenderUrl(unittest.TestCase):
    def test_allowed(self):
        ok = fo_telemetry_send.allowed
        self.assertTrue(ok("https://eu.i.posthog.com/i/v0/e/"))
        self.assertTrue(ok("http://127.0.0.1:9/e"))
        self.assertTrue(ok("http://localhost:9/e"))
        self.assertFalse(ok("http://127.0.0.1.attacker.example/collect"))
        self.assertFalse(ok("http://example.com/e"))
        self.assertFalse(ok("https://user:pw@example.com/e"))
        self.assertFalse(ok("file:///etc/passwd"))

    def test_lookalike_host_never_contacted(self):
        with mock.patch.object(fo_telemetry_send.urllib.request, "build_opener") as opener, \
                mock.patch.object(sys, "argv", ["x", "http://127.0.0.1.attacker.example/c"]), \
                mock.patch.object(sys, "stdin", io.TextIOWrapper(io.BytesIO(b"{}"))):
            self.assertEqual(fo_telemetry_send.main(), 0)
            opener.assert_not_called()


class AuditLogConcurrency(unittest.TestCase):
    def test_parallel_writers_lose_nothing(self):
        with Isolated() as iso:
            # A writer that cannot get the lock within the bounded wait skips its event by design
            # (record() never sends an unaudited event). What must never happen: a written entry lost.
            code = ("import sys; sys.path.insert(0, %r); import fo_telemetry as t\n"
                    "ok = 0\n"
                    "for i in range(25):\n"
                    "    try:\n"
                    "        t.audit({'event': 'fo_route', 'n': i, 'w': sys.argv[1]}); ok += 1\n"
                    "    except TimeoutError:\n"
                    "        pass\n"
                    "print(ok)" % SCRIPTS)
            procs = [subprocess.Popen([sys.executable, "-c", code, str(w)], env=dict(os.environ),
                                      stdout=subprocess.PIPE, text=True) for w in range(4)]
            written = 0
            for p in procs:
                out, _ = p.communicate(timeout=60)
                self.assertEqual(p.returncode, 0)
                written += int(out.strip())
            log = read_text(os.path.join(iso.home, ".local", "state", "frontier-orchestration", "telemetry-log.jsonl"))
            self.assertGreater(written, 0)
            self.assertEqual(len(log.splitlines()), written)


class RelativeConfig(unittest.TestCase):
    def test_bare_relative_fo_config_writes(self):
        with Isolated() as iso:
            cwd = os.getcwd()
            os.chdir(iso.home)
            try:
                os.environ["FO_CONFIG"] = "config.json"
                path = fo_config.write_file({"jev": {"backend": "off"}})
                self.assertEqual(path, os.path.join(os.path.realpath(iso.home), "config.json")
                                 if path.startswith("/private") else os.path.join(iso.home, "config.json"))
                self.assertTrue(os.path.exists(path))
            finally:
                os.chdir(cwd)


class PrivacyNoticeShipsWithSkill(unittest.TestCase):
    def test_skill_copy_matches_root(self):
        self.assertEqual(read_text(os.path.join(SKILL, "references", "privacy.md")),
                         read_text(os.path.join(ROOT, "PRIVACY.md")))


if __name__ == "__main__":
    unittest.main()
