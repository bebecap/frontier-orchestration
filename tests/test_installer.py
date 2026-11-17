"""Installer, TUI and the read-only ask runner. Fake vendor CLIs on PATH; real local HTTP servers."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import time
import unittest
from unittest import mock

from helpers import FO, Isolated, Server, clean_env, jev_reply, read_json, read_text

import fo_config
import fo_providers
import fo_tui


def fake_cli(bin_dir: str, name: str, body: str) -> str:
    """A tiny shell script that records its argv and stdin, then runs `body`."""
    path = os.path.join(bin_dir, name)
    with open(path, "w") as f:
        f.write("#!/bin/sh\n"
                f'printf "%s\\n" "$@" > "{bin_dir}/{name}.argv"\n'
                f'if [ ! -t 0 ]; then cat > "{bin_dir}/{name}.stdin"; fi\n' + body + "\n")
    os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
    return path


def signed_in_clis(bin_dir: str) -> None:
    fake_cli(bin_dir, "claude", 'case "$1" in auth) echo \'{"loggedIn": true}\';; *) echo "CLAUDE REVIEW";; esac')
    fake_cli(bin_dir, "codex", 'case "$1" in login) echo "Logged in using ChatGPT";; '
                               'exec) out=""; while [ $# -gt 0 ]; do [ "$1" = "-o" ] && out="$2"; shift; done; '
                               'echo "CODEX REVIEW" > "$out";; esac')
    fake_cli(bin_dir, "agy", 'case "$1" in models) echo "gemini-4-argon-high";; *) echo "AGY REVIEW";; esac')


def keys(seq: list):
    """A read() for fo_tui that replays key names."""
    raw = {"up": "\x1b[A", "down": "\x1b[B", "enter": "\r", "space": " ", "ctrl-c": "\x03"}
    buf = list("".join(raw.get(k, k) for k in seq))

    def read(n):
        out = "".join(buf[:n])
        del buf[:n]
        if not out:
            raise AssertionError("ran out of scripted keys")
        return out
    return read


class TuiKeys(unittest.TestCase):
    def setUp(self):
        self.out = mock.patch("sys.stdout", new_callable=lambda: open(os.devnull, "w"))
        self.out.start()

    def tearDown(self):
        sys.stdout.close()
        self.out.stop()

    def test_select_moves_and_wraps(self):
        opts = [("a", "A", ""), ("b", "B", ""), ("c", "C", "")]
        self.assertEqual(fo_tui.select("q", opts, read=keys(["down", "down", "enter"])), "c")
        self.assertEqual(fo_tui.select("q", opts, read=keys(["up", "enter"])), "c")
        self.assertEqual(fo_tui.select("q", opts, read=keys(["j", "enter"])), "b")

    def test_multiselect_requires_one_and_toggles(self):
        opts = [("claude", "Claude", ""), ("codex", "Codex", ""), ("gemini", "Antigravity", "")]
        got = fo_tui.multiselect("q", opts, read=keys(["enter", "space", "down", "down", "space", "enter"]))
        self.assertEqual(got, ["claude", "gemini"])

    def test_ctrl_c_cancels(self):
        with self.assertRaises(fo_tui.Cancelled):
            fo_tui.select("q", [("a", "A", "")], read=keys(["ctrl-c"]))


class Sessions(unittest.TestCase):
    def test_signed_in_and_missing(self):
        with Isolated() as iso:
            b = os.path.join(iso.home, "bin")
            os.makedirs(b)
            signed_in_clis(b)
            os.environ["PATH"] = b + ":/usr/bin:/bin"
            for v in ("claude", "codex", "gemini"):
                self.assertTrue(fo_providers.session(v)["signed_in"], v)
            fake_cli(b, "codex", 'echo "Not logged in"; exit 1')
            self.assertFalse(fo_providers.session("codex")["signed_in"])
            fake_cli(b, "claude", 'echo \'{"loggedIn": true}\'; echo "[wrapper] profile line" >&2')
            self.assertTrue(fo_providers.session("claude")["signed_in"], "trailing wrapper output must not break the check")
            os.remove(os.path.join(b, "agy"))
            s = fo_providers.session("gemini")
            self.assertFalse(s["installed"])
            self.assertEqual(s["detail"], "not installed")


class AskRunner(unittest.TestCase):
    def run_ask(self, iso, vendor, extra=None, cfg_extra=None):
        b = os.path.join(iso.home, "bin")
        os.environ["PATH"] = b + ":/usr/bin:/bin"
        cfg = fo_config.deep_merge(fo_config.load(), cfg_extra or {})
        out = os.path.join(iso.home, "out", f"{vendor}.md")
        return fo_providers.run(cfg, vendor, "PROMPT TEXT", out_file=out, cwd=iso.home, host="unknown",
                                **(extra or {})), out, b

    def test_codex_read_only_and_prompt_on_stdin(self):
        with Isolated() as iso:
            os.makedirs(os.path.join(iso.home, "bin"))
            signed_in_clis(os.path.join(iso.home, "bin"))
            r, out, b = self.run_ask(iso, "codex", {"model": "gpt-6.1-sol", "effort": "high"})
            self.assertEqual(r["status"], "ok")
            self.assertEqual(read_text(out).strip(), "CODEX REVIEW")
            argv = read_text(os.path.join(b, "codex.argv")).split("\n")
            self.assertIn("--sandbox", argv)
            self.assertEqual(argv[argv.index("--sandbox") + 1], "read-only")
            self.assertEqual(argv[argv.index("-m") + 1], "gpt-6.1-sol")
            self.assertEqual(read_text(os.path.join(b, "codex.stdin")), "PROMPT TEXT")

    def test_claude_and_agy_plan_mode(self):
        with Isolated() as iso:
            os.makedirs(os.path.join(iso.home, "bin"))
            signed_in_clis(os.path.join(iso.home, "bin"))
            r, out, b = self.run_ask(iso, "claude")
            self.assertEqual((r["status"], read_text(out).strip()), ("ok", "CLAUDE REVIEW"))
            self.assertIn("plan", read_text(os.path.join(b, "claude.argv")).split("\n"))
            r, out, b = self.run_ask(iso, "gemini")
            argv = read_text(os.path.join(b, "agy.argv")).split("\n")
            self.assertEqual(argv[argv.index("--mode") + 1], "plan")
            self.assertNotIn("--dangerously-skip-permissions", argv)

    def test_failure_timeout_empty_and_stale_output(self):
        with Isolated() as iso:
            b = os.path.join(iso.home, "bin")
            os.makedirs(b)
            fake_cli(b, "codex", "exit 3")
            os.makedirs(os.path.join(iso.home, "out"))
            with open(os.path.join(iso.home, "out", "codex.md"), "w") as f:
                f.write("OLD VERDICT")
            r, out, _ = self.run_ask(iso, "codex")
            self.assertEqual(r["status"], "failed")
            self.assertFalse(os.path.exists(out), "an old answer must not survive a failed run")
            fake_cli(b, "codex", "sleep 30")
            t0 = time.monotonic()
            r, _, _ = self.run_ask(iso, "codex", {"timeout": 1})
            self.assertEqual(r["status"], "timeout")
            self.assertLess(time.monotonic() - t0, 15)
            fake_cli(b, "claude", "true")
            r, _, _ = self.run_ask(iso, "claude")
            self.assertEqual(r["status"], "empty")

    def test_refusals(self):
        with Isolated() as iso:
            os.makedirs(os.path.join(iso.home, "bin"))
            signed_in_clis(os.path.join(iso.home, "bin"))
            with self.assertRaises(fo_providers.AskError):
                self.run_ask(iso, "codex", cfg_extra={"providers": {"codex": {"enabled": False}}})
            cfg = fo_config.load()
            with self.assertRaises(fo_providers.AskError):
                fo_providers.run(cfg, "claude", "x", out_file=os.path.join(iso.home, "o.md"), host="claude-code")

    def test_cli_command(self):
        with Isolated() as iso:
            b = os.path.join(iso.home, "bin")
            os.makedirs(b)
            signed_in_clis(b)
            env = clean_env(iso.home, PATH=b + ":/usr/bin:/bin")
            p = subprocess.run([sys.executable, "-I", FO, "ask", "codex", "--prompt", "hello", "--json",
                                "--out", os.path.join(iso.home, "a.md")], capture_output=True, text=True, env=env, timeout=60)
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertEqual(json.loads(p.stdout)["status"], "ok")


class InstallerFlow(unittest.TestCase):
    """The whole installer through its plain (no terminal) prompts, as a user would answer."""

    def install(self, home, answers, extra_env=None):
        env = clean_env(home, PATH=os.path.join(home, "bin") + ":/usr/bin:/bin", FO_TUI="plain",
                        **(extra_env or {}))
        return subprocess.run([sys.executable, "-I", FO, "install"], input="\n".join(answers) + "\n",
                              capture_output=True, text=True, env=env, timeout=60, cwd=home)

    def test_no_engine_two_providers_no_telemetry(self):
        with Isolated() as iso:
            os.makedirs(os.path.join(iso.home, "bin"))
            signed_in_clis(os.path.join(iso.home, "bin"))
            # engine 4 = none; providers 1,2 = Claude, Codex; telemetry 2 = No; ledger default
            p = self.install(iso.home, ["4", "1,2", "2", ""])
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            cfg = read_json(os.path.join(iso.home, ".config", "frontier-orchestration", "config.json"))
            self.assertEqual(cfg["jev"]["backend"], "off")
            self.assertEqual({v: c["enabled"] for v, c in cfg["providers"].items()},
                             {"claude": True, "codex": True, "gemini": False})
            self.assertIs(cfg["telemetry"]["enabled"], False)

    def test_typesafe_key_from_env_is_checked(self):
        with Isolated() as iso, Server(jev_reply()) as srv:
            os.makedirs(os.path.join(iso.home, "bin"))
            signed_in_clis(os.path.join(iso.home, "bin"))
            # engine 1 = TypeSafe; key option 1 = $TYPESAFE_API_KEY; providers 3 = Antigravity; telemetry yes
            p = self.install(iso.home, ["1", "1", "3", "1", ""],
                             {"TYPESAFE_API_KEY": "fake-key-123456", "TYPESAFE_BASE_URL": srv.url})
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            self.assertEqual(srv.requests[0]["headers"]["authorization"], "Bearer fake-key-123456")
            cfg = read_json(os.path.join(iso.home, ".config", "frontier-orchestration", "config.json"))
            self.assertEqual(cfg["jev"]["backend"], "typesafe")
            self.assertNotIn("fake-key-123456", json.dumps(cfg), "a key must never reach the config")
            self.assertEqual(cfg["telemetry"], {"enabled": True, "schema": 4})

    def test_openjev_at_an_address(self):
        with Isolated() as iso:
            os.makedirs(os.path.join(iso.home, "bin"))
            signed_in_clis(os.path.join(iso.home, "bin"))
            # engine 2 = OpenJev; not running -> option 2 = another address; type it; providers 2
            p = self.install(iso.home, ["2", "2", "http://localhost:9099", "2", "2", ""],
                             {"FO_OPENJEV_WAIT_S": "0"})
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            cfg = read_json(os.path.join(iso.home, ".config", "frontier-orchestration", "config.json"))
            self.assertEqual(cfg["jev"], {"backend": "openjev", "base_url": "http://localhost:9099"})

    def test_requires_a_signed_in_provider(self):
        with Isolated() as iso:
            b = os.path.join(iso.home, "bin")
            os.makedirs(b)
            fake_cli(b, "codex", 'echo "Not logged in"; exit 1')
            # engine none; choose Codex (not signed in) -> "Stop here"
            p = self.install(iso.home, ["4", "2", "2"])
            self.assertNotEqual(p.returncode, 0)
            self.assertIn("At least one chosen provider must be installed and signed in", p.stdout)
            self.assertFalse(os.path.exists(os.path.join(iso.home, ".config", "frontier-orchestration", "config.json")))


if __name__ == "__main__":
    unittest.main()
