"""Telemetry end to end on the client side, against a local collector. Never the real one."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import unittest

from helpers import FO, TELEMETRY_SINK, Isolated, Server, clean_env

import fo_telemetry
from test_installer import signed_in_clis


class NeverTheRealCollector(unittest.TestCase):
    def test_every_test_environment_points_at_the_sink(self):
        self.assertEqual(clean_env("/nonexistent")["FO_TELEMETRY_ENDPOINT"], TELEMETRY_SINK)
        with Isolated():
            self.assertEqual(fo_telemetry.collector()["endpoint"], TELEMETRY_SINK)

    def test_sink_is_loopback(self):
        self.assertTrue(TELEMETRY_SINK.startswith("http://127.0.0.1:"))


class InstallerSendsOneCleanEvent(unittest.TestCase):
    def run_install(self, home, url):
        env = clean_env(home, PATH=os.path.join(home, "bin") + ":/usr/bin:/bin", FO_TUI="plain",
                        FO_TELEMETRY_ENDPOINT=url)
        # engine none; Claude + Codex; telemetry YES; default ledger
        return subprocess.run([sys.executable, "-I", FO, "install"], input="4\n1,2\n1\n\n",
                              capture_output=True, text=True, env=env, timeout=60)

    def test_setup_event_reaches_the_collector_with_allowlisted_fields_only(self):
        with Isolated() as iso, Server(lambda path, body, headers: (204, {})) as srv:
            os.makedirs(os.path.join(iso.home, "bin"))
            signed_in_clis(os.path.join(iso.home, "bin"))
            ids = []
            for _ in range(2):
                p = self.run_install(iso.home, srv.url + "/api/e")
                self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
                deadline = time.time() + 8
                while len(srv.requests) < len(ids) + 1 and time.time() < deadline:
                    time.sleep(0.1)  # the sender is detached
                self.assertEqual(len(srv.requests), len(ids) + 1, "no event reached the local collector")
                req = srv.requests[-1]
                body = req["body"]
                self.assertEqual(req["path"], "/api/e")
                self.assertEqual(body["event"], "fo_setup")  # the collector stores it as "setup"
                self.assertEqual(body["properties"]["mode"], "interactive")
                fixed = {"schema": fo_telemetry.SCHEMA_VERSION, "$lib": "frontier-orchestration",
                         "$geoip_disable": True, "$process_person_profile": False}  # PRIVACY.md "fixed fields"
                props = dict(body["properties"])
                self.assertEqual({k: props.pop(k, None) for k in fixed}, fixed)
                self.assertLessEqual(set(props), set(fo_telemetry.PROPS), "a property outside the allowlist was sent")
                self.assertEqual(set(body), {"api_key", "event", "distinct_id", "properties", "timestamp"})
                raw = json.dumps(body)
                for secret in (iso.home, os.path.basename(iso.home), os.environ.get("USER", "\0")):
                    self.assertNotIn(secret, raw, "a path or user name was sent")
                ids.append(body["distinct_id"])
            self.assertNotEqual(ids[0], ids[1], "the event id must be fresh each time (no install id)")


if __name__ == "__main__":
    unittest.main()
