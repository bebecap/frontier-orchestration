"""Telemetry (allowlist, consent gates, real send to a local collector) and the ledger."""

from __future__ import annotations

import json
import os
import time
import unittest
from unittest import mock

from helpers import UNAVAILABLE_URL, read_json, read_text, Isolated, Server, run_fo

import fo_config
import fo_telemetry

ON = {"telemetry": {"enabled": True, "schema": 4}}


def collector_env(url: str) -> dict:
    return {"FO_TELEMETRY_ENDPOINT": url + "/i/v0/e/", "FO_TELEMETRY_KEY": "phc_test"}


def wait_for(cond, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


class TelemetryGateTests(unittest.TestCase):
    def status(self, **env):
        with Isolated(FO_TELEMETRY_ENDPOINT="https://x.example/e", FO_TELEMETRY_KEY="phc_x", **env):
            return fo_telemetry.status(fo_config.deep_merge(fo_config.load(), ON))

    def test_on_only_with_consent_and_collector(self):
        self.assertTrue(self.status()[0])
        with Isolated(FO_TELEMETRY_ENDPOINT="https://x.example/e", FO_TELEMETRY_KEY="phc_x"):
            self.assertFalse(fo_telemetry.status(fo_config.load())[0])  # consent not given
        with Isolated(), mock.patch.object(fo_telemetry, "collector", return_value={}):  # a build with no collector
            on, why = fo_telemetry.status(fo_config.deep_merge(fo_config.load(), ON))
            self.assertFalse(on)
            self.assertIn("no collector", why)

    def test_env_cannot_override_opt_out(self):
        with Isolated(FO_TELEMETRY="1", FO_TELEMETRY_ENDPOINT="https://x.example/e", FO_TELEMETRY_KEY="phc_x"):
            fo_config.write_file({"telemetry": {"enabled": False, "schema": fo_telemetry.SCHEMA_VERSION}})
            self.assertFalse(fo_telemetry.status(fo_config.load())[0])
            fo_config.write_file({})
            self.assertFalse(fo_telemetry.status(fo_config.load())[0], "env alone is not consent")

    def test_legacy_files_removed(self):
        with Isolated():
            os.makedirs(fo_config.state_dir())
            for n in fo_telemetry.LEGACY_FILES:
                open(os.path.join(fo_config.state_dir(), n), "w").close()
            fo_telemetry.record("route", {}, fo_config.load())
            self.assertEqual([n for n in fo_telemetry.LEGACY_FILES
                              if os.path.exists(os.path.join(fo_config.state_dir(), n))], [])

    def test_kill_switches(self):
        self.assertFalse(self.status(DO_NOT_TRACK="1")[0])
        self.assertFalse(self.status(FO_TELEMETRY_DISABLED="1")[0])
        self.assertFalse(self.status(CI="true")[0])
        self.assertFalse(self.status(GITHUB_ACTIONS="true")[0])

    def test_allowlist(self):
        event, props = fo_telemetry.clean("route", {
            "ok": True, "tasks": 3, "host": "claude-code", "jev_backend": "typesafe",
            "path": "/Users/someone/secret", "prompt": "do x", "host_name": "box", "tasks_text": "x",
            "os": "plan9", "acted": "3", "fo_version": "0.1.0; rm -rf", "python": "3.12",
            "via_codex": "ssh-tunnel"})
        self.assertEqual(event, "route")
        self.assertEqual(props, {"ok": True, "tasks": 3, "host": "claude-code", "jev_backend": "typesafe",
                                 "python": "3.12"})
        self.assertEqual(fo_telemetry.clean("evil", {})[0], "telemetry")

    def test_payload_shape(self):
        with Isolated(FO_TELEMETRY_KEY="phc_x", FO_TELEMETRY_ENDPOINT="https://x.example/e"):
            p = fo_telemetry.build("route", {"ok": True, "secret": "no"})
            self.assertEqual(p["event"], "fo_route")
            self.assertEqual(p["api_key"], "phc_x")
            self.assertFalse(p["properties"]["$process_person_profile"])
            self.assertTrue(p["properties"]["$geoip_disable"])
            self.assertNotIn("secret", p["properties"])
            allowed = set(fo_telemetry.PROPS) | {"$process_person_profile", "$geoip_disable", "$lib", "schema"}
            self.assertLessEqual(set(p["properties"]), allowed)
            self.assertEqual(len(p["distinct_id"]), 36)

    def test_no_install_id(self):
        with Isolated(FO_TELEMETRY_KEY="phc_x", FO_TELEMETRY_ENDPOINT="https://x.example/e"):
            a, b = fo_telemetry.build("route", {}), fo_telemetry.build("route", {})
            self.assertNotEqual(a["distinct_id"], b["distinct_id"], "a fresh id per event: events cannot be linked")
            self.assertFalse(os.path.exists(os.path.join(fo_config.state_dir(), "telemetry-id.json")))


class TelemetrySendTests(unittest.TestCase):
    """End to end: fo.py -> detached sender -> a real HTTP collector on 127.0.0.1."""

    def test_sends_only_after_opt_in(self):
        with Isolated() as iso, Server(lambda p, b, h: (200, {"status": "Ok"})) as srv:
            env = collector_env(srv.url)
            run_fo(iso.home, "setup", "--jev", "off", "--providers", "claude,codex,gemini", "--telemetry", "no", env_extra=env)
            run_fo(iso.home, "providers", env_extra=env)
            time.sleep(1.0)
            self.assertEqual(srv.requests, [], "nothing may be sent without consent")

            self.assertEqual(run_fo(iso.home, "telemetry", "on", env_extra=env).returncode, 0)
            self.assertEqual(run_fo(iso.home, "route", "--demo", env_extra=env).returncode, 0)
            self.assertTrue(wait_for(lambda: len(srv.requests) >= 1), "the event never reached the collector")
            body = srv.requests[0]["body"]
            self.assertEqual(body["event"], "fo_route")
            self.assertEqual(body["api_key"], "phc_test")
            self.assertEqual(body["properties"]["tasks"], 3)
            text = json.dumps(body)
            for leak in (iso.home, "estimate", "customer", os.environ.get("USER", "nobody-xyz")):
                self.assertNotIn(leak, text)
            log = run_fo(iso.home, "telemetry", "log", env_extra=env).stdout
            self.assertIn("fo_route", log)
            self.assertNotIn("phc_test", log)

    def test_do_not_track_sends_nothing(self):
        with Isolated() as iso, Server(lambda p, b, h: (200, {})) as srv:
            env = dict(collector_env(srv.url), DO_NOT_TRACK="1")
            run_fo(iso.home, "telemetry", "on", env_extra=env)
            run_fo(iso.home, "route", "--demo", env_extra=dict(env, FO_JEV_BACKEND="off"))
            time.sleep(1.0)
            self.assertEqual(srv.requests, [])

    def test_debug_prints_and_sends_nothing(self):
        with Isolated() as iso, Server(lambda p, b, h: (200, {})) as srv:
            env = dict(collector_env(srv.url), FO_TELEMETRY_DEBUG="1", FO_JEV_BACKEND="off")
            p = run_fo(iso.home, "route", "--demo", env_extra=env)
            self.assertIn("telemetry debug (not sent)", p.stderr)
            time.sleep(1.0)
            self.assertEqual(srv.requests, [])
            self.assertFalse(os.path.exists(os.path.join(iso.home, ".local", "state", "frontier-orchestration",
                                                         "telemetry-id.json")))

    def test_dead_collector_never_breaks_a_command(self):
        with Isolated() as iso:
            env = {"FO_TELEMETRY_ENDPOINT": UNAVAILABLE_URL + "/e", "FO_TELEMETRY_KEY": "phc_x", "FO_JEV_BACKEND": "off"}
            run_fo(iso.home, "telemetry", "on", env_extra=env)
            t0 = time.time()
            p = run_fo(iso.home, "route", "--demo", env_extra=env)
            self.assertEqual(p.returncode, 0)
            self.assertLess(time.time() - t0, 5)


class LedgerTests(unittest.TestCase):
    def test_status_lifecycle(self):
        with Isolated() as iso:
            fo = lambda *a: run_fo(iso.home, "lessons", *a)  # noqa: E731
            for run in ("2026-01-01-demo-a", "2026-01-02-demo-b", "2026-01-03-demo-c"):
                self.assertEqual(fo("run-new", "--id", run, "--project", "demo", "--artefact", "code",
                                    "--title", run).returncode, 0)
            p = fo("add", "--slug", "freeze", "--rule", "Review one frozen SHA per round.", "--why", "w",
                   "--kind", "review-loop", "--tags", "demo", "--run", "2026-01-01-demo-a", "--evidence", "e1")
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertIn("candidate", fo("recall", "--all").stdout)
            fo("reinforce", "FO-L001", "--run", "2026-01-02-demo-b", "--evidence", "e2")
            self.assertIn("[confirmed ×2]", fo("recall").stdout)
            fo("contradict", "FO-L001", "--run", "2026-01-03-demo-c", "--evidence", "e3")
            fo("contradict", "FO-L001", "--run", "2026-01-02-demo-b", "--evidence", "flip")
            self.assertIn("disputed", fo("recall").stdout)
            dup = fo("add", "--slug", "again", "--rule", "Review one frozen SHA per round.", "--why", "w",
                     "--kind", "review-loop", "--tags", "demo", "--run", "2026-01-01-demo-a", "--evidence", "x")
            self.assertNotEqual(dup.returncode, 0)
            self.assertEqual(fo("lint").returncode, 0, fo("lint").stdout)
            log = read_text(os.path.join(iso.home, ".local", "share", "frontier-orchestration", "ledger", "log.md"))
            self.assertTrue(log.startswith("# frontier-orchestration ledger log"))

    def test_explicit_outside_log_must_exist(self):
        with Isolated() as iso:
            env = {"FO_LEDGER": os.path.join(iso.home, "led"), "FO_LEDGER_LOG": os.path.join(iso.home, "nope", "log.md")}
            p = run_fo(iso.home, "lessons", "run-new", "--id", "2026-01-01-x-y", "--project", "x",
                       "--artefact", "code", "--title", "t", env_extra=env)
            self.assertNotEqual(p.returncode, 0)
            self.assertIn("refusing", p.stderr + p.stdout)

    def test_free_text_cannot_inject_frontmatter(self):
        with Isolated() as iso:
            run_fo(iso.home, "lessons", "run-new", "--id", "2026-01-01-x-y", "--project", "x", "--artefact", "code",
                   "--title", "t")
            run_fo(iso.home, "lessons", "add", "--slug", "s", "--rule", "Rule\nstatus: confirmed\n## Evil", "--why", "w",
                   "--kind", "routing", "--tags", "x", "--run", "2026-01-01-x-y", "--evidence", "e")
            led = os.path.join(iso.home, ".local", "share", "frontier-orchestration", "ledger", "lessons")
            text = read_text(os.path.join(led, os.listdir(led)[0]))
            self.assertIn('status: "candidate"', text)
            self.assertNotIn("\n## Evil", text)


if __name__ == "__main__":
    unittest.main()
