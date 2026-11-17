"""`fo.py <command> -h / --help` prints a usage and exits 0 BEFORE the command does any work.

Before this fix, `fo.py refresh --help` ran a real refresh (and could update the vendor CLIs),
`fo.py install --help` started the installer and `fo.py setup --help` printed the setup questions.

Each check runs fo.py in a subprocess with an isolated HOME, fake vendor CLIs (claude, codex, agy,
gemini) first on PATH that leave a marker file when they run, and one real local HTTP server that
is both the Jev backend and the telemetry endpoint and records every request. A positive control
proves that this harness sees a real run (marker and requests), so a green result is not empty.
"""

from __future__ import annotations

import json
import os
import re
import stat
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import FO, Server, jev_reply, run_fo  # noqa: E402

import fo  # noqa: E402
import fo_config  # noqa: E402
import fo_telemetry  # noqa: E402

FAKES = ("claude", "codex", "agy", "gemini")
# Detached children (the refresh worker, the telemetry sender) start after fo.py exits: give them
# this long to leave a marker or send a request before the test looks.
SETTLE_S = 1.5

# One call per command, with the flags that would do the most work if help were ignored.
CASES = {
    "install": ["install"],
    "setup": ["setup", "--jev", "off", "--providers", "claude", "--telemetry", "yes"],
    "doctor": ["doctor", "--live"],
    "providers": ["providers", "--json"],
    "ask": ["ask", "claude", "--prompt", "say hi"],
    "route": ["route", "--demo"],
    "eval": ["eval", "all"],
    "refresh": ["refresh", "--wait", "--upgrade"],
    "config": ["config", "set", "telemetry.enabled", "false"],
    "jev": ["jev", "ping"],
    "lessons": ["lessons", "init"],
    "telemetry": ["telemetry", "off"],
    "version": ["version"],
}


def snapshot(root: str) -> dict:
    """Every file and folder under root, with the bytes of each file."""
    out = {}
    for d, dirs, files in os.walk(root):
        for name in dirs:
            out[os.path.relpath(os.path.join(d, name), root) + "/"] = None
        for name in files:
            p = os.path.join(d, name)
            with open(p, "rb") as f:
                out[os.path.relpath(p, root)] = f.read()
    return out


class Sandbox:
    """A temp HOME, a fake-CLI folder and a marker folder, all outside one another.

    `tag` goes into the Jev and telemetry URLs, so one shared server can tell the sandboxes apart.
    """

    def __init__(self, server_url: str, seeded: bool, tag: str):
        self.tag = tag
        self.tmp = tempfile.TemporaryDirectory()
        base = self.tmp.name
        self.home = os.path.join(base, "home")
        self.bin = os.path.join(base, "bin")
        self.marks = os.path.join(base, "marks")
        for p in (self.home, self.bin, self.marks):
            os.makedirs(p)
        for name in FAKES:
            path = os.path.join(self.bin, name)
            with open(path, "w", encoding="utf-8") as f:
                f.write(f'#!/bin/sh\necho "{name} $*" >> "{self.marks}/{name}.ran"\necho "{name} 1.0.0"\nexit 0\n')
            os.chmod(path, stat.S_IRWXU)
        prefix = f"{server_url}/{tag}"
        self.env = {"PATH": f"{self.bin}:/usr/bin:/bin", "FO_PATH_EXTRA": self.bin,
                    "FO_TELEMETRY_ENDPOINT": prefix + "/telemetry", "FO_TELEMETRY_KEY": "fo_test_key",
                    "FO_TUI": "plain"}
        self.config = os.path.join(self.home, ".config", "frontier-orchestration", "config.json")
        if seeded:  # every switch that would make a real run talk or spawn: Jev, telemetry, CLI upgrades
            os.makedirs(os.path.dirname(self.config))
            data = {"version": fo_config.SCHEMA_VERSION,
                    "jev": {"backend": "custom", "base_url": prefix, "model": "jev-test"},
                    "telemetry": {"enabled": True, "schema": fo_telemetry.SCHEMA_VERSION},
                    "refresh": {"upgrade_clis": True, "ttl_hours": 0.0001},
                    "providers": {v: {"enabled": True} for v in fo_config.VENDORS}}
            with open(self.config, "w", encoding="utf-8") as f:
                json.dump(data, f)
        self.before = snapshot(self.home)

    def marks_left(self) -> list:
        return sorted(os.listdir(self.marks))

    def requests(self, server: Server) -> list:
        return [r["path"] for r in server.requests if r["path"].startswith(f"/{self.tag}/")]

    def close(self):
        self.tmp.cleanup()


class HelpBeforeWork(unittest.TestCase):
    def setUp(self):
        self.server = Server(jev_reply())
        self.server.__enter__()
        self.boxes = []

    def tearDown(self):
        for box in self.boxes:
            box.close()
        self.server.__exit__(None, None, None)

    def run_all(self, calls: list) -> list:
        """Run each (argv, seeded) in its own sandbox; wait once for detached children; return results."""
        out = []
        for i, (argv, seeded) in enumerate(calls):
            box = Sandbox(self.server.url, seeded, f"s{i}")
            self.boxes.append(box)
            out.append((argv, seeded, box, run_fo(box.home, *argv, env_extra=box.env)))
        time.sleep(SETTLE_S)
        return out

    def assert_help_only(self, argv: list, seeded: bool, box: Sandbox, p):
        where = f"fo.py {' '.join(argv)} ({'seeded' if seeded else 'fresh'} HOME)"
        self.assertEqual(p.returncode, 0, f"{where}: exit {p.returncode}\n{p.stdout}\n{p.stderr}")
        self.assertIn("usage", p.stdout.lower(), f"{where}: no usage\n{p.stdout}")
        self.assertIn(f"usage: fo.py {argv[0]}", p.stdout, f"{where}: not this command's usage\n{p.stdout}")
        self.assertEqual(box.marks_left(), [], f"{where}: a vendor CLI ran")
        self.assertEqual(box.requests(self.server), [], f"{where}: a network request was sent")
        self.assertEqual(snapshot(box.home), box.before, f"{where}: HOME changed (config, state or ledger)")
        if not seeded:
            self.assertFalse(os.path.exists(box.config), f"{where}: a config file was created")

    def test_every_command_prints_usage_and_does_nothing(self):
        calls = [(argv + [word], seeded) for argv in CASES.values()
                 for word in ("--help", "-h") for seeded in (False, True)]
        for argv, seeded, box, p in self.run_all(calls):
            with self.subTest(argv=argv, seeded=seeded):
                self.assert_help_only(argv, seeded, box, p)

    def test_help_word_anywhere_before_double_dash(self):
        calls = [(["refresh", "--help", "--wait", "--upgrade"], True),
                 (["setup", "-h", "--jev", "off", "--providers", "claude", "--telemetry", "yes"], False),
                 (["install", "--help", "--", "x"], True),
                 (["ask", "claude", "-h", "--prompt", "say hi"], True)]
        for argv, seeded, box, p in self.run_all(calls):
            with self.subTest(argv=argv, seeded=seeded):
                self.assert_help_only(argv, seeded, box, p)

    def test_help_after_double_dash_is_data(self):
        [(argv, seeded, box, p)] = self.run_all([(["config", "path", "--", "--help"], False)])
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertNotIn("usage", p.stdout.lower())
        self.assertEqual(p.stdout.strip(), box.config)

    def test_positive_control_harness_sees_real_work(self):
        """Without --help the same call runs CLIs and sends telemetry: the checks above can fail."""
        [(argv, seeded, box, p)] = self.run_all([(["refresh", "--wait", "--upgrade"], True)])
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertNotEqual(box.marks_left(), [], "the fake CLIs were not reached: the harness is blind")
        self.assertIn(f"/{box.tag}/telemetry", box.requests(self.server),
                      "the telemetry request was not seen: the harness is blind")
        self.assertNotEqual(snapshot(box.home), box.before, "the refresh left no snapshot: the harness is blind")


class HelpTable(unittest.TestCase):
    def test_every_dispatched_command_has_help_and_a_case(self):
        with open(FO, encoding="utf-8") as f:
            src = f.read()
        main_src = src[src.index("def main(argv: list)"):]
        dispatched = set(re.findall(r'cmd == "([a-z-]+)"', main_src))
        self.assertTrue(dispatched >= {"install", "setup", "refresh", "lessons", "telemetry"}, dispatched)
        self.assertEqual(dispatched - set(fo.HELP), set(), "a command without help text")
        self.assertEqual(set(fo.HELP) - set(CASES), set(), "a command with help text but no test case")

    def test_wants_help(self):
        self.assertTrue(fo.wants_help(["--help"]))
        self.assertTrue(fo.wants_help(["--wait", "-h"]))
        self.assertTrue(fo.wants_help(["-h", "--", "x"]))
        self.assertFalse(fo.wants_help(["--", "--help"]))
        self.assertFalse(fo.wants_help(["--prompt", "help"]))
        self.assertFalse(fo.wants_help([]))

    def test_top_level_help_unchanged(self):
        with tempfile.TemporaryDirectory() as home:
            for word in ("--help", "-h", "help"):
                p = run_fo(home, word)
                self.assertEqual(p.returncode, 0)
                self.assertEqual(p.stdout.strip(), fo.__doc__.strip())


if __name__ == "__main__":
    unittest.main()
