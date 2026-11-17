"""The suite and the installer must not depend on, or write to, anything outside a test's temp HOME.

A real OpenJev on 127.0.0.1:8080 (where the people who use this skill run it) once shifted the
installer's scripted answers: the ledger answer "2" became a folder `2/` in the checkout."""

from __future__ import annotations

import io
import os
import subprocess
import sys
import unittest
from unittest import mock

from helpers import FO, OPENJEV_SINK, Isolated, clean_env, read_json, run_fo
from test_installer import signed_in_clis
from test_openjev_models import models_server

import fo_config
import fo_install


def install(home, answers, **extra):
    env = clean_env(home, PATH=os.path.join(home, "bin") + ":/usr/bin:/bin", FO_TUI="plain", **extra)
    return subprocess.run([sys.executable, "-I", FO, "install"], input="\n".join(answers) + "\n",
                          capture_output=True, text=True, env=env, timeout=60, cwd=home)


def config_file(home):
    return os.path.join(home, ".config", "frontier-orchestration", "config.json")


class OpenJevAddress(unittest.TestCase):
    def test_default_and_override(self):
        with Isolated() as iso:
            del os.environ["FO_OPENJEV_URL"]
            self.assertEqual(fo_install.openjev_url(), "http://127.0.0.1:8080")
            os.environ["FO_OPENJEV_URL"] = "http://localhost:9123/"
            self.assertEqual(fo_install.openjev_url(), "http://localhost:9123")
            # plain http to another machine would need jev.allow_insecure_http: not taken from here
            os.environ["FO_OPENJEV_URL"] = "http://gpu.example:8080"
            self.assertEqual(fo_install.openjev_url(), "http://127.0.0.1:8080")
            self.assertTrue(iso.home)

    def test_tests_never_look_at_the_real_default_port(self):
        self.assertEqual(clean_env("/nonexistent")["FO_OPENJEV_URL"], OPENJEV_SINK)
        with Isolated():
            self.assertEqual(fo_install.openjev_url(), OPENJEV_SINK)
            self.assertEqual(fo_install.openjev_models(), [])

    def test_installer_uses_the_server_at_FO_OPENJEV_URL(self):
        httpd, url = models_server(["openjev-latest"])
        try:
            with Isolated() as iso:
                os.makedirs(os.path.join(iso.home, "bin"))
                signed_in_clis(os.path.join(iso.home, "bin"))
                # engine 2 = OpenJev: it answers, so no "not running" question; providers 2; telemetry no
                p = install(iso.home, ["2", "2", "2", ""], FO_OPENJEV_URL=url)
                self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
                self.assertIn(f"OpenJev answers at {url}", p.stdout)
                self.assertEqual(read_json(config_file(iso.home))["jev"], {"backend": "openjev", "base_url": url})
        finally:
            httpd.shutdown()

    def test_an_unusable_FO_OPENJEV_URL_is_named_and_ignored(self):
        # In process, with the probe replaced: an ignored value falls back to the real default
        # port, and a test must never look there.
        with Isolated() as iso:
            os.makedirs(os.path.join(iso.home, "bin"))
            signed_in_clis(os.path.join(iso.home, "bin"))
            os.environ.update(FO_OPENJEV_URL="http://gpu.example:8080", FO_TUI="plain",
                              PATH=os.path.join(iso.home, "bin") + ":/usr/bin:/bin")
            out = io.StringIO()
            with mock.patch.object(fo_install, "openjev_models", lambda url=None: []), \
                    mock.patch("sys.stdin", io.StringIO("4\n2\n2\n\n")), mock.patch("sys.stdout", out):
                code = fo_install.run_installer()
            self.assertEqual(code, 0, out.getvalue())
            self.assertIn("FO_OPENJEV_URL must be https://, or http:// to 127.0.0.1, localhost or [::1]", out.getvalue())
            self.assertEqual(fo_install.openjev_url(), "http://127.0.0.1:8080")


class LedgerPathMustBeFull(unittest.TestCase):
    def test_installer_refuses_a_relative_ledger_folder(self):
        with Isolated() as iso:
            os.makedirs(os.path.join(iso.home, "bin"))
            signed_in_clis(os.path.join(iso.home, "bin"))
            before = sorted(os.listdir(iso.home))
            # engine none; Codex; telemetry no; ledger "2" (refused), then a full path
            p = install(iso.home, ["4", "2", "2", "2", "~/led"])
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            self.assertIn("The ledger folder must be a full path that starts with / or ~, not '2'", p.stdout)
            self.assertFalse(os.path.exists(os.path.join(iso.home, "2")), "a relative answer made a folder")
            self.assertEqual(read_json(config_file(iso.home))["ledger"]["path"], os.path.join(iso.home, "led"))
            self.assertTrue(os.path.isdir(os.path.join(iso.home, "led")))
            self.assertEqual(sorted(set(os.listdir(iso.home)) - set(before)), [".config", "led"])

    def test_config_refuses_relative_ledger_paths(self):
        for key in ("path", "log"):
            with self.assertRaises(fo_config.ConfigError) as c:
                fo_config.effective({"ledger": {key: "notes"}}, with_env=False)
            self.assertIn(f"ledger.{key} must be a full path", str(c.exception))
        fo_config.effective({"ledger": {"path": "~/notes", "log": "/tmp/log.md"}}, with_env=False)

    def test_FO_LEDGER_relative_is_a_config_error_and_creates_nothing(self):
        with Isolated() as iso:
            r = run_fo(iso.home, "lessons", "init", env_extra={"FO_LEDGER": "rel-ledger"})
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn("ledger.path must be a full path", r.stderr)
            self.assertFalse(os.path.exists(os.path.join(iso.home, "rel-ledger")))

    def test_FO_VAULT_relative_is_a_config_error(self):
        with Isolated() as iso:
            r = run_fo(iso.home, "config", "show", env_extra={"FO_VAULT": "vault"})
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn("FO_VAULT must be a full path", r.stderr)

    def test_config_set_refuses_and_keeps_the_file(self):
        with Isolated() as iso:
            self.assertEqual(run_fo(iso.home, "config", "set", "ledger.path", "~/good").returncode, 0)
            r = run_fo(iso.home, "config", "set", "ledger.path", "bad")
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn("ledger.path must be a full path", r.stderr)
            self.assertEqual(read_json(config_file(iso.home))["ledger"]["path"], "~/good")

    def test_setup_refuses_a_relative_ledger(self):
        with Isolated() as iso:
            os.makedirs(os.path.join(iso.home, "bin"))
            signed_in_clis(os.path.join(iso.home, "bin"))
            r = run_fo(iso.home, "setup", "--jev", "off", "--providers", "codex", "--telemetry", "no", "--ledger", "rel",
                       env_extra={"PATH": os.path.join(iso.home, "bin") + ":/usr/bin:/bin"})
            self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("ledger.path must be a full path", r.stdout + r.stderr)
            self.assertFalse(os.path.exists(os.path.join(iso.home, "rel")))
            self.assertFalse(os.path.exists(config_file(iso.home)))


class TestsRunInTheirTempHome(unittest.TestCase):
    def test_isolated_and_run_fo_use_the_temp_home_as_current_folder(self):
        outside = os.getcwd()
        with Isolated() as iso:
            here = os.path.realpath(iso.home)
            self.assertEqual(os.path.realpath(os.getcwd()), here)
            pwd = subprocess.run(["/bin/pwd", "-P"], capture_output=True, text=True).stdout.strip()
            self.assertEqual(pwd, here)
            r = run_fo(iso.home, "config", "path", env_extra={"FO_CONFIG": "rel.json"})
            self.assertEqual(os.path.realpath(r.stdout.strip()), os.path.join(here, "rel.json"))
        self.assertEqual(os.getcwd(), outside)


if __name__ == "__main__":
    unittest.main()
