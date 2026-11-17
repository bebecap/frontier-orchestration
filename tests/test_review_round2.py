"""Regression tests for the round-2 blind review findings (Sol and Astra on 48c3ac6)."""

from __future__ import annotations

import fcntl
import os
import time
import unittest
import urllib.error
from unittest import mock

from helpers import Isolated, Server, read_text, run_fo

import fo_config
import fo_jev
import fo_lessons
import fo_route
import fo_telemetry


class AuditLockNeverStalls(unittest.TestCase):
    def test_busy_log_skips_the_event_quickly(self):
        with Isolated(FO_TELEMETRY_ENDPOINT="https://x.example/e", FO_TELEMETRY_KEY="phc_x"):
            cfg = fo_config.deep_merge(fo_config.load(), {"telemetry": {"enabled": True, "schema": 4}})
            path = os.path.join(fo_config.state_dir(), "telemetry-log.jsonl")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path + ".lock", "w") as holder:
                fcntl.flock(holder, fcntl.LOCK_EX)
                with mock.patch.object(fo_telemetry.subprocess, "Popen") as popen:
                    t0 = time.monotonic()
                    result = fo_telemetry.record("route", {"ok": True}, cfg)
                    self.assertLess(time.monotonic() - t0, 2.0)
                    self.assertEqual(result, "error")
                    popen.assert_not_called()  # an event that was not audited is never sent


class RedactBeforeTruncate(unittest.TestCase):
    def test_key_across_the_display_limit(self):
        key = "fake-key-" + "x" * 40
        for pad in (560, 590, 599, 600, 601):
            body = "e" * pad + key + "tail"
            with Isolated(MY_KEY=key), Server(lambda p, b, h, body=body: (401, {"detail": body})) as srv:
                js = {"backend": "custom", "base_url": srv.url, "model": "m", "api_key_env": "MY_KEY",
                      "key_required": True, "timeout_s": 10}
                with self.assertRaises(fo_jev.JevError) as ctx:
                    fo_jev.ping(js)
                msg = str(ctx.exception)
                for n in range(12, len(key) + 1):
                    self.assertNotIn(key[:n], msg, f"pad {pad}: {n} key chars leaked")

    def test_short_and_escaped_keys_never_shown(self):
        for key in ("abc", "fake-secret/abc+123="):
            escaped = key.replace("/", "\\/")
            reply = lambda p, b, h, e=escaped: (401, {"detail": f"bad token {e}"})  # noqa: E731
            with Isolated(MY_KEY=key), Server(reply) as srv:
                js = {"backend": "custom", "base_url": srv.url, "model": "m", "api_key_env": "MY_KEY",
                      "key_required": True, "timeout_s": 10}
                with self.assertRaises(fo_jev.JevError) as ctx:
                    fo_jev.ping(js)
                self.assertNotIn(key, str(ctx.exception))
                self.assertNotIn(escaped, str(ctx.exception))


class IndependenceIsStrict(unittest.TestCase):
    def test_values(self):
        ni = fo_route.needs_independence
        for v in ("yes", "YES - must fail differently", "true", "y", True, "Yes: reviewer", "yes (strict)"):
            self.assertTrue(ni({"needs_independence": v}), v)
        for v in ("no", "No - same vendor is fine", "false", "n", False, None, ""):
            self.assertFalse(ni({"needs_independence": v}), v)
        for v in ("not sure", "not optional", "nope", "yesterday", "maybe", "required"):
            with self.assertRaises(fo_route.RouteError, msg=v):
                ni({"needs_independence": v})

    def test_ambiguous_value_cannot_route_same_vendor(self):
        with Isolated():
            roster = fo_route.Roster(fo_config.roster())
        spec = {"tasks": {"rev": {"needs_independence": "not optional", "producer_vendor": "claude",
                                  "candidates": ["opus"]}}}
        with self.assertRaises(fo_route.RouteError):
            fo_route.route(spec, roster, {"backend": "off"})
        self.assertTrue(fo_route.self_check(spec, roster))


class LedgerFirstUseUnderLock(unittest.TestCase):
    def test_log_is_created_while_holding_the_lock(self):
        with Isolated() as iso:
            root = os.path.join(iso.home, "led")
            fo_lessons.configure(root, os.path.join(root, "log.md"))
            seen = {}
            real = fo_lessons.require_vault

            def spy():
                with open(os.path.join(root, ".fo-lock"), "w") as probe:
                    try:
                        fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        seen["held"] = False
                        fcntl.flock(probe, fcntl.LOCK_UN)
                    except OSError:
                        seen["held"] = True
                real()

            with mock.patch.object(fo_lessons, "require_vault", spy):
                with fo_lessons.FolderLock():
                    pass
            self.assertTrue(seen["held"], "require_vault must run under the folder lock")
            self.assertTrue(read_text(os.path.join(root, "log.md")).startswith(fo_lessons.LOG_HEADER))


class ProviderOverridesViaCli(unittest.TestCase):
    def test_config_set_model_and_command_is_refused(self):
        with Isolated() as iso:
            # providers.<vendor>.command was removed: setting it is refused and nothing is written.
            refused = run_fo(iso.home, "config", "set", "providers.codex.command", "my-codex --ro")
            self.assertNotEqual(refused.returncode, 0)
            self.assertIn("unknown key providers.codex.command", refused.stdout + refused.stderr)
            self.assertEqual(run_fo(iso.home, "config", "set", "providers.codex.model", "gpt-6-astra").returncode, 0)
            p = run_fo(iso.home, "providers", "--json")
            self.assertNotIn("my-codex", p.stdout)
            self.assertIn("gpt-6-astra", p.stdout)
            bad = run_fo(iso.home, "config", "set", "providers.codex.enabled", "7")
            self.assertNotEqual(bad.returncode, 0)


if __name__ == "__main__":
    unittest.main()
