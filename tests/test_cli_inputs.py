"""Bad user input never shows a traceback, path settings have one normal form, and hints are
runnable commands. Real subprocess runs of fo.py in a temporary home."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import unittest
from contextlib import redirect_stderr
from unittest import mock

from helpers import FO, ROOT, Isolated, clean_env, read_json, read_text, run_fo
from test_installer import fake_cli, signed_in_clis

import fo_config
import fo_install
import fo_jev

CLI = f"python3 -I {FO}"


def config_file(home: str) -> str:
    return os.path.join(home, ".config", "frontier-orchestration", "config.json")


def write_config(home: str, data: dict) -> None:
    os.makedirs(os.path.dirname(config_file(home)), exist_ok=True)
    with open(config_file(home), "w", encoding="utf-8") as f:
        json.dump(data, f)


class PathWhitespace(unittest.TestCase):
    """One normal form for ledger.path, ledger.log, FO_LEDGER, FO_LEDGER_LOG, FO_VAULT, the
    installer answer, `setup --ledger` and `config set`: surrounding spaces are removed before the
    check, the save and the resolution."""

    def test_config_file_values_resolve_to_the_full_path(self):
        with Isolated() as iso:
            for value in (" ~/notes", "~/notes ", f" {iso.home}/notes\t"):
                write_config(iso.home, {"ledger": {"path": value, "log": " ~/notes/log.md "}})
                root, log = fo_config.ledger_paths(fo_config.load())
                self.assertEqual(root, os.path.join(iso.home, "notes"), repr(value))
                self.assertEqual(log, os.path.join(iso.home, "notes", "log.md"))
            for key in ("path", "log"):
                write_config(iso.home, {"ledger": {key: " notes "}})
                with self.assertRaises(fo_config.ConfigError, msg=key):
                    fo_config.load()

    def test_environment_values_resolve_to_the_full_path(self):
        with Isolated(FO_LEDGER=" ~/a ", FO_LEDGER_LOG=" ~/a/l.md ") as iso:
            self.assertEqual(fo_config.ledger_paths(fo_config.load()),
                             (os.path.join(iso.home, "a"), os.path.join(iso.home, "a", "l.md")))
        with Isolated(FO_VAULT=" ~/vault ") as iso:
            root, _ = fo_config.ledger_paths(fo_config.load())
            self.assertEqual(root, os.path.join(iso.home, "vault", "wiki", "learning", fo_config.APP))
        with Isolated(FO_VAULT=" vault "):
            with self.assertRaises(fo_config.ConfigError):
                fo_config.ledger_paths(fo_config.load())

    def test_config_set_and_setup_store_the_clean_value(self):
        with Isolated() as iso:
            p = run_fo(iso.home, "config", "set", "ledger.path", " ~/notes ")
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertEqual(read_json(config_file(iso.home))["ledger"]["path"], "~/notes")
            p = run_fo(iso.home, "setup", "--jev", "off", "--providers", "claude", "--telemetry", "no",
                       "--ledger", " ~/other ", "--ledger-log", " ~/other/log.md ", env_extra={"FO_TELEMETRY": "0"})
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            self.assertEqual(read_json(config_file(iso.home))["ledger"],
                             {"path": "~/other", "log": "~/other/log.md"})
            self.assertTrue(os.path.isdir(os.path.join(iso.home, "other")))
            self.assertEqual([n for n in os.listdir(iso.home) if n.strip() != n or n.startswith("~")], [])

    def test_installer_answer_with_spaces(self):
        with Isolated() as iso:
            os.makedirs(os.path.join(iso.home, "bin"))
            signed_in_clis(os.path.join(iso.home, "bin"))
            env = clean_env(iso.home, PATH=os.path.join(iso.home, "bin") + ":/usr/bin:/bin", FO_TUI="plain")
            p = subprocess.run([sys.executable, "-I", FO, "install"], input="4\n2\n2\n  ~/notes  \n",
                               capture_output=True, text=True, env=env, timeout=60, cwd=iso.home)
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            self.assertEqual(read_json(config_file(iso.home))["ledger"]["path"], os.path.join(iso.home, "notes"))
            self.assertTrue(os.path.isdir(os.path.join(iso.home, "notes", "lessons")))
            self.assertEqual([n for n in os.listdir(iso.home) if n.strip() != n or n.startswith("~")], [])


class RepairTwoBrokenPaths(unittest.TestCase):
    def test_both_keys_in_one_call(self):
        with Isolated() as iso:
            write_config(iso.home, {"ledger": {"path": "notes", "log": "log.md"}})
            p = run_fo(iso.home, "config", "set", "ledger.path", "~/notes")
            self.assertEqual(p.returncode, 1)
            self.assertIn("Fix ledger.log in the same call", p.stderr)
            self.assertIn(f"{CLI} config set ledger.path", p.stderr)
            p = run_fo(iso.home, "config", "set", "ledger.path", "~/notes", "ledger.log", "null")
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertEqual(read_json(config_file(iso.home))["ledger"], {"path": "~/notes", "log": None})
            self.assertEqual(run_fo(iso.home, "config", "show").returncode, 0)

    def test_an_odd_number_of_words_is_a_usage_error(self):
        with Isolated() as iso:
            p = run_fo(iso.home, "config", "set", "ledger.path", "~/a", "ledger.log")
            self.assertEqual(p.returncode, 1)
            self.assertIn("set KEY VALUE [KEY VALUE ...]", p.stderr)


class NoTraceback(unittest.TestCase):
    def assertClean(self, p, code=1):
        out = p.stdout + p.stderr
        self.assertEqual(p.returncode, code, out)
        self.assertNotIn("Traceback", out)
        self.assertNotIn("fo.py failed:", out)  # the guard is the last line of defence, not the answer

    def test_route_file_errors(self):
        with Isolated() as iso:
            files = {"list.json": b"[1, 2]", "bad.json": b"{oops", "latin1.json": b"\xff\xfe{}",
                     "tasks.json": b'{"tasks": [1]}', "cands.json": b'{"tasks": {"a": {"candidates": "x"}}}'}
            for name, data in files.items():
                with open(os.path.join(iso.home, name), "wb") as f:
                    f.write(data)
            for name in list(files) + ["missing.json"]:
                for extra in ([], ["--check"]):
                    p = run_fo(iso.home, "route", *extra, name, env_extra={"FO_JEV_BACKEND": "off"})
                    self.assertClean(p)
                    self.assertTrue(p.stderr.startswith(f"route failed: cannot read {name}: "), p.stderr)
            p = run_fo(iso.home, "route", "list.json", env_extra={"FO_JEV_BACKEND": "off"})
            self.assertIn("the top level must be a JSON object, not a list", p.stderr)
            p = run_fo(iso.home, "route", "latin1.json", env_extra={"FO_JEV_BACKEND": "off"})
            self.assertIn("not UTF-8 text", p.stderr)

    def test_bad_flags_and_config_shapes(self):
        with Isolated() as iso:
            for args in (("telemetry", "log", "x"), ("refresh", "--ttl-hours", "x"), ("refresh", "--ttl-hours"),
                         ("ask", "codex", "--prompt", "x", "--timeout", "soon"),
                         ("ask", "codex", "--prompt-file", "missing.txt")):
                self.assertClean(run_fo(iso.home, *args, env_extra={"FO_JEV_BACKEND": "off"}))
            for data in ({"providers": []}, {"providers": {"claude": "x"}}, {"jev": "x"}, {"ledger": "x"},
                         {"telemetry": 5}):
                write_config(iso.home, data)
                for args in (("doctor",), ("providers",), ("route", "--demo"), ("config", "show"),
                             ("config", "set", "jev.backend", "off"), ("telemetry", "on"), ("lessons", "recall")):
                    p = run_fo(iso.home, *args)
                    self.assertClean(p)
                    self.assertIn("must be a JSON object", p.stdout + p.stderr, (data, args))

    def test_bad_values_are_named(self):
        with Isolated() as iso:
            write_config(iso.home, {"refresh": {"ttl_hours": "x"}, "telemetry": {"enabled": "yes"}})
            p = run_fo(iso.home, "refresh")
            self.assertClean(p)
            self.assertIn("refresh.ttl_hours must be a number of hours above 0", p.stderr)

    def test_guard_prints_one_line_and_FO_DEBUG_shows_the_traceback(self):
        import fo
        err = io.StringIO()
        with mock.patch.object(fo, "main", side_effect=RuntimeError("boom\nsecond line")), redirect_stderr(err), \
                mock.patch.dict(os.environ, {"FO_DEBUG": ""}):
            self.assertEqual(fo.run(["doctor"]), 1)
        self.assertEqual(err.getvalue().count("\n"), 1)
        self.assertIn("fo.py failed: RuntimeError: boom (set FO_DEBUG=1", err.getvalue())
        with mock.patch.object(fo, "main", side_effect=RuntimeError("boom")), \
                mock.patch.dict(os.environ, {"FO_DEBUG": "1"}):
            with self.assertRaises(RuntimeError):
                fo.run(["doctor"])


class AskChecksCwdFirst(unittest.TestCase):
    def test_missing_cwd_is_refused_before_the_cli_runs(self):
        with Isolated() as iso:
            b = os.path.join(iso.home, "bin")
            os.makedirs(b)
            signed_in_clis(b)
            p = run_fo(iso.home, "ask", "codex", "--prompt", "hi", "--cwd", os.path.join(iso.home, "nope"),
                       env_extra={"PATH": b + ":/usr/bin:/bin", "FO_JEV_BACKEND": "off"})
            self.assertEqual(p.returncode, 1)
            self.assertIn("ask refused: --cwd", p.stderr)
            self.assertIn("is not an existing folder", p.stderr)
            self.assertFalse(os.path.exists(os.path.join(b, "codex.argv")), "the vendor CLI ran")


class DoctorBeforeSetup(unittest.TestCase):
    def test_says_run_setup_first(self):
        with Isolated() as iso:
            p = run_fo(iso.home, "doctor")
            self.assertEqual(p.returncode, 1)
            self.assertIn(f"FAIL setup has not run yet", p.stdout)
            self.assertIn(f"{CLI} install", p.stdout)
            self.assertNotIn("no API key", p.stdout)


class EvalHostWhenOff(unittest.TestCase):
    def test_off_backend_shows_no_host(self):
        with Isolated() as iso:
            write_config(iso.home, {"jev": {"backend": "off", "base_url": "http://stale.example:9"}})
            p = run_fo(iso.home, "eval", "routing", "--json")
            self.assertEqual(json.loads(p.stdout)["host"], "")
            p = run_fo(iso.home, "eval", "routing")
            self.assertNotIn("stale.example", p.stdout + p.stderr)


class OneSignInCheck(unittest.TestCase):
    def test_providers_and_doctor_live_agree_on_the_host_session(self):
        with Isolated() as iso:
            b = os.path.join(iso.home, "bin")
            os.makedirs(b)
            signed_in_clis(b)
            fake_cli(b, "claude", 'echo \'{"loggedIn": false}\'')  # the CLI is signed out; the host is not
            env = {"PATH": b + ":/usr/bin:/bin", "FO_HOST": "claude-code", "FO_JEV_BACKEND": "off"}
            run_fo(iso.home, "setup", "--jev", "off", "--providers", "claude,codex", "--telemetry", "no", env_extra=env)
            prov = run_fo(iso.home, "providers", env_extra=env)
            doc = run_fo(iso.home, "doctor", "--live", env_extra=env)
            for out in (prov.stdout, doc.stdout):
                claude = next(line for line in out.splitlines() if line.strip().startswith("Claude"))
                self.assertIn("[host session, assumed signed in]", claude)
            self.assertEqual(doc.returncode, 0, doc.stdout)
            self.assertFalse(os.path.exists(os.path.join(b, "claude.argv")), "the claude CLI status was read")


class LessonsSubcommandHelp(unittest.TestCase):
    def test_each_subcommand_shows_its_own_flags(self):
        with Isolated() as iso:
            for sub, flag in (("recall", "--limit"), ("add", "--slug"), ("run-new", "--artefact"),
                              ("retire", "--reason")):
                p = run_fo(iso.home, "lessons", sub, "--help")
                self.assertEqual(p.returncode, 0, p.stderr)
                self.assertTrue(p.stdout.startswith(f"usage: fo.py lessons {sub}"), p.stdout)
                self.assertIn(flag, p.stdout)
            self.assertFalse(os.path.exists(os.path.join(iso.home, ".local")), "help did work")


class FullCommandHints(unittest.TestCase):
    def test_setup_and_doctor_print_runnable_commands(self):
        with Isolated() as iso:
            p = run_fo(iso.home, "setup", "--jev", "off", "--providers", "claude", "--telemetry", "no")
            self.assertIn(f"next: {CLI} doctor --live", p.stdout)
            p = run_fo(iso.home, "doctor")
            self.assertIn(f"run {CLI} refresh --wait", p.stdout)
        with Isolated() as iso:  # nothing set up: setup asks its questions
            p = run_fo(iso.home, "setup", "--non-interactive")
            self.assertIn(f"then run {CLI} setup again", p.stdout)

    def test_installer_outro(self):
        with Isolated() as iso:
            os.makedirs(os.path.join(iso.home, "bin"))
            signed_in_clis(os.path.join(iso.home, "bin"))
            env = clean_env(iso.home, PATH=os.path.join(iso.home, "bin") + ":/usr/bin:/bin", FO_TUI="plain")
            p = subprocess.run([sys.executable, "-I", FO, "install"], input="4\n2\n2\n\n", capture_output=True,
                               text=True, env=env, timeout=60, cwd=iso.home)
            self.assertIn(f"Next: {CLI} doctor --live", p.stdout)

    def test_docs_define_the_full_form(self):
        head = read_text(os.path.join(ROOT, "docs", "install.md"))[:1500]
        self.assertIn("python3 -I", head)
        self.assertIn("scripts/fo.py", head)


class LinuxMessages(unittest.TestCase):
    TS = {"backend": "typesafe", "api_key_env": "TYPESAFE_API_KEY", "keychain_service": "TYPESAFE_API_KEY",
          "key_required": True}

    def test_missing_key_names_the_routes_that_work_on_linux(self):
        with Isolated(), mock.patch.object(sys, "platform", "linux"):
            with self.assertRaises(fo_jev.JevError) as ctx:
                fo_jev.api_key(self.TS)
        text = str(ctx.exception)
        self.assertNotIn("security add-generic-password", text)
        self.assertNotIn("Keychain", text)
        for part in (f"{CLI} install", "chmod 600", "jev.api_key_file", "export TYPESAFE_API_KEY="):
            self.assertIn(part, text)

    def test_setup_lines_on_linux_do_not_start_with_or(self):
        import fo
        with Isolated(), mock.patch.object(sys, "platform", "linux"):
            lines = fo.key_instructions(dict(self.TS, label="t", signup=""))
        self.assertTrue(lines[0].startswith("API key for typesafe: NOT FOUND"))
        self.assertFalse(any(line.strip().startswith("or:") for line in lines), lines)

    def test_python_hint_puts_uv_first_and_names_the_distro_tools(self):
        linux = fo_install.python310_hint("linux").splitlines()
        self.assertIn("uv python install", linux[0])
        self.assertTrue(any("apt install" in line for line in linux))
        self.assertTrue(any("dnf install" in line for line in linux))
        self.assertNotIn("brew", "\n".join(linux))
        self.assertIn("brew install", fo_install.python310_hint("darwin"))

    def test_readme_says_what_linux_needs(self):
        text = " ".join(read_text(os.path.join(ROOT, "README.md")).split())
        self.assertIn("Self-hosted OpenJev on Linux needs an NVIDIA GPU and Docker", text)
        self.assertIn("The hosted engines (TypeSafe Jev, OpenJev on Codiv) and `off` work on any", text)


if __name__ == "__main__":
    unittest.main()
