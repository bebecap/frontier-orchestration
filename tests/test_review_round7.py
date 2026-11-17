"""Regression tests for the LOW items of round 7 (both reviewers approved 1d82815)."""

from __future__ import annotations

import os
import unittest

from helpers import UNAVAILABLE_URL, Isolated, read_text, run_fo

import fo_config
import fo_jev


class TimeoutMustBeANumber(unittest.TestCase):
    def test_config_rejects_bad_timeout(self):
        with Isolated():
            for bad in ("oops", 0, -1, True, 99999):
                with self.assertRaises(fo_config.ConfigError, msg=repr(bad)):
                    fo_config.set_dotted({}, "jev.timeout_s", bad)

    def test_client_turns_bad_timeout_into_jev_error(self):
        js = {"backend": "custom", "base_url": UNAVAILABLE_URL, "model": "m", "key_required": False,
              "timeout_s": "fast"}
        with self.assertRaises(fo_jev.JevError):
            fo_jev.ask(js, "s", {}, key="")


class DoctorProbeNeverTouchesExistingFiles(unittest.TestCase):
    def test_existing_probe_name_and_symlink_survive(self):
        with Isolated() as iso:
            root = os.path.join(iso.home, ".local", "share", "frontier-orchestration", "ledger")
            os.makedirs(root)
            target = os.path.join(iso.home, "precious.txt")
            with open(target, "w") as f:
                f.write("keep me")
            os.symlink(target, os.path.join(root, ".fo-write-test"))
            run_fo(iso.home, "doctor")
            self.assertEqual(read_text(target), "keep me")
            self.assertTrue(os.path.islink(os.path.join(root, ".fo-write-test")))


if __name__ == "__main__":
    unittest.main()


class ConsentPerSchema(unittest.TestCase):
    def test_consent_is_per_schema_version(self):
        import fo_telemetry
        with Isolated(FO_TELEMETRY_ENDPOINT="https://x.example/e", FO_TELEMETRY_KEY="fo_pub_x"):
            old = fo_config.deep_merge(fo_config.load(), {"telemetry": {"enabled": True, "schema": 3}})
            on, why = fo_telemetry.status(old)
            self.assertFalse(on)
            self.assertIn("changed", why)
