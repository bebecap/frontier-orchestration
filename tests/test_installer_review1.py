"""Regression tests for installer review round 1 (Sol and Astra on 9c44db7, via fo.py ask)."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
import unittest
from unittest import mock

from helpers import FO, Isolated, Server, clean_env, jev_reply, read_json, read_text
from test_installer import fake_cli, signed_in_clis

import fo_config
import fo_install
import fo_jev
import fo_lessons
import fo_providers
import fo_tui


def codex_that_cannot_start(home: str) -> dict:
    """A config whose codex CLI is on PATH but cannot be executed (its interpreter is missing)."""
    b = os.path.join(home, "nostart-bin")
    os.makedirs(b, exist_ok=True)
    path = os.path.join(b, "codex")
    with open(path, "w") as f:
        f.write("#!/no/such/interpreter\n")
    os.chmod(path, 0o755)
    os.environ["PATH"] = b + ":/usr/bin:/bin"
    return fo_config.deep_merge(fo_config.load(), {"providers": {"codex": {"via": "cli"}}})


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


class EngineSwitchDoesNotLeakTheKey(unittest.TestCase):
    def test_typesafe_check_never_goes_to_the_old_server(self):
        with Isolated() as iso, Server(jev_reply()) as old, Server(jev_reply()) as new:
            os.makedirs(os.path.join(iso.home, "bin"))
            signed_in_clis(os.path.join(iso.home, "bin"))
            fo_config.write_file({"jev": {"backend": "openjev", "base_url": old.url, "allow_insecure_http": True}})
            env = clean_env(iso.home, PATH=os.path.join(iso.home, "bin") + ":/usr/bin:/bin", FO_TUI="plain",
                            TYPESAFE_API_KEY="fake-key-0123456789", TYPESAFE_BASE_URL=new.url)
            p = subprocess.run([sys.executable, "-I", FO, "install"], input="1\n1\n1\n2\n\n",
                               capture_output=True, text=True, env=env, timeout=60)
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            self.assertEqual(old.requests, [], "the TypeSafe key went to the previous engine's server")
            self.assertEqual(len(new.requests), 1)
            cfg = read_json(os.path.join(iso.home, ".config", "frontier-orchestration", "config.json"))
            self.assertNotIn("base_url", cfg["jev"])
            self.assertNotIn("allow_insecure_http", cfg["jev"])
            self.assertEqual(cfg["jev"]["key_source"], "env")


class KeySourceIsObeyed(unittest.TestCase):
    def test_keychain_choice_ignores_the_environment(self):
        with Isolated(MYKEY="from-env-123456"):
            js = {"backend": "custom", "api_key_env": "MYKEY", "keychain_service": None, "key_source": "keychain"}
            self.assertEqual(fo_jev.api_key(js, required=False), "")
            js["key_source"] = "env"
            self.assertEqual(fo_jev.api_key(js, required=False), "from-env-123456")

    def test_file_choice(self):
        with Isolated(MYKEY="from-env-123456") as iso:
            p = os.path.join(iso.home, "k")
            fd = os.open(p, os.O_WRONLY | os.O_CREAT, 0o600)
            os.write(fd, b"from-file-123456\n")
            os.close(fd)
            js = {"backend": "custom", "api_key_env": "MYKEY", "api_key_file": p, "key_source": "file"}
            self.assertEqual(fo_jev.api_key(js), "from-file-123456")


class AskFilesAndProcesses(unittest.TestCase):
    def test_default_answer_files_are_unique_and_private(self):
        with Isolated() as iso:
            b = os.path.join(iso.home, "bin")
            os.makedirs(b)
            signed_in_clis(b)
            env = clean_env(iso.home, PATH=b + ":/usr/bin:/bin")
            procs = [subprocess.Popen([sys.executable, "-I", FO, "ask", "claude", "--prompt", "x"], env=env,
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) for _ in range(2)]
            for p in procs:
                self.assertEqual(p.wait(timeout=60), 0)
            d = os.path.join(iso.home, ".local", "state", "frontier-orchestration", "asks")
            files = os.listdir(d)
            self.assertEqual(len(files), 2, files)
            self.assertEqual(os.stat(d).st_mode & 0o777, 0o700)
            for f in files:
                self.assertEqual(os.stat(os.path.join(d, f)).st_mode & 0o777, 0o600)

    def test_timeout_kills_a_child_that_ignores_sigterm(self):
        with Isolated() as iso:
            b = os.path.join(iso.home, "bin")
            os.makedirs(b)
            pidfile = os.path.join(iso.home, "child.pid")
            fake_cli(b, "codex", f"(trap '' TERM; echo $$ > /dev/null; exec sh -c 'echo $$ > {pidfile}; "
                                 "trap \"\" TERM; while :; do sleep 1; done') </dev/null >/dev/null 2>&1 & sleep 30")
            os.environ["PATH"] = b + ":/usr/bin:/bin"
            r = fo_providers.run(fo_config.load(), "codex", "x", out_file=os.path.join(iso.home, "o.md"),
                                 cwd=iso.home, host="unknown", timeout=1)
            self.assertEqual(r["status"], "timeout")
            pid = int(read_text(pidfile).strip())
            time.sleep(0.5)
            self.assertFalse(alive(pid), "a child that ignores SIGTERM survived the timeout")

    def test_ctrl_c_stops_the_vendor_cli(self):
        with Isolated() as iso:
            b = os.path.join(iso.home, "bin")
            os.makedirs(b)
            pidfile = os.path.join(iso.home, "cli.pid")
            fake_cli(b, "codex", f"echo $$ > {pidfile}; while :; do sleep 1; done")
            env = clean_env(iso.home, PATH=b + ":/usr/bin:/bin")
            # A shell runs a background job with SIGINT ignored, and children inherit that: give
            # fo.py the default Ctrl-C handling, as in a terminal.
            p = subprocess.Popen([sys.executable, "-I", FO, "ask", "codex", "--prompt", "x"], env=env,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 preexec_fn=lambda: signal.signal(signal.SIGINT, signal.SIG_DFL))
            deadline = time.time() + 20
            while not os.path.exists(pidfile) and time.time() < deadline:
                time.sleep(0.1)
            time.sleep(0.3)
            p.send_signal(signal.SIGINT)
            p.wait(timeout=20)
            pid = int(read_text(pidfile).strip())
            time.sleep(0.5)
            self.assertFalse(alive(pid), "the vendor CLI outlived Ctrl-C")


class OpenJevInstallState(unittest.TestCase):
    def test_partial_install_is_not_installed(self):
        with Isolated():
            home = fo_install.openjev_home()
            os.makedirs(os.path.join(home, "repo"))
            self.assertFalse(fo_install.openjev_installed(home))
            open(os.path.join(home, ".installed"), "w").close()
            self.assertTrue(fo_install.openjev_installed(home))

    def test_launcher_quotes_any_folder_name(self):
        with Isolated() as iso:
            home = os.path.join(iso.home, "Company's $(touch pwned) data")
            os.makedirs(home)
            path = fo_install.write_launcher(home)
            self.assertEqual(subprocess.run(["/bin/sh", "-n", path]).returncode, 0)
            subprocess.run(["/bin/sh", path], capture_output=True, timeout=20)
            self.assertFalse(os.path.exists(os.path.join(iso.home, "pwned")), "the folder name ran as a command")


class Round2(unittest.TestCase):
    """Installer review round 2 (Sol on 76f72de)."""

    def test_setup_flags_engine_change_drops_old_server(self):
        with Isolated() as iso:
            fo_config.write_file({"jev": {"backend": "openjev", "base_url": "http://gpu.example:8080",
                                          "allow_insecure_http": True, "key_source": "file"}})
            p = subprocess.run([sys.executable, "-I", FO, "setup", "--jev", "typesafe", "--providers", "claude",
                                "--telemetry", "no"], capture_output=True, text=True, env=clean_env(iso.home), timeout=60)
            self.assertEqual(p.returncode, 0, p.stderr)
            cfg = read_json(os.path.join(iso.home, ".config", "frontier-orchestration", "config.json"))
            self.assertEqual(cfg["jev"], {"backend": "typesafe"})

    def test_ctrl_c_during_grace_still_kills(self):
        with Isolated() as iso:
            b = os.path.join(iso.home, "bin")
            os.makedirs(b)
            pidfile = os.path.join(iso.home, "child.pid")
            fake_cli(b, "codex", f"(exec sh -c 'echo $$ > {pidfile}; trap \"\" TERM; while :; do sleep 1; done') "
                                 "</dev/null >/dev/null 2>&1 & trap '' TERM; while :; do sleep 1; done")
            os.environ["PATH"] = b + ":/usr/bin:/bin"
            real = subprocess.Popen.communicate
            calls = []

            def flaky(self, *a, **k):
                calls.append(k.get("timeout"))
                if len(calls) == 2:  # the grace-period wait after SIGTERM: simulate Ctrl-C here
                    raise KeyboardInterrupt()
                return real(self, *a, **k)

            with mock.patch.object(subprocess.Popen, "communicate", flaky):
                with self.assertRaises(KeyboardInterrupt):
                    fo_providers.run(fo_config.load(), "codex", "x", out_file=os.path.join(iso.home, "o.md"),
                                     cwd=iso.home, host="unknown", timeout=1)
            time.sleep(0.5)
            self.assertFalse(alive(int(read_text(pidfile).strip())), "SIGKILL was skipped by Ctrl-C")

    def test_existing_asks_folder_is_tightened_and_output_private_from_start(self):
        with Isolated() as iso:
            b = os.path.join(iso.home, "bin")
            os.makedirs(b)
            seen = os.path.join(iso.home, "mode.txt")
            fake_cli(b, "codex", 'case "$1" in exec) out=""; while [ $# -gt 0 ]; do [ "$1" = "-o" ] && out="$2"; shift; done; '
                                 f'stat -f %Lp "$out" > {seen} 2>/dev/null || stat -c %a "$out" > {seen}; echo ANSWER > "$out";; esac')
            asks = os.path.join(iso.home, ".local", "state", "frontier-orchestration", "asks")
            os.makedirs(asks)
            os.chmod(asks, 0o755)
            p = subprocess.run([sys.executable, "-I", FO, "ask", "codex", "--prompt", "x"], capture_output=True, text=True,
                               env=clean_env(iso.home, PATH=b + ":/usr/bin:/bin"), timeout=60)
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertEqual(os.stat(asks).st_mode & 0o777, 0o700)
            self.assertEqual(read_text(seen).strip(), "600", "the answer file was not private while the CLI ran")


class Round2Astra(unittest.TestCase):
    """Installer review round 2 (Astra on 76f72de)."""

    def test_repair_never_deletes_a_checkout_or_unknown_folder(self):
        with Isolated() as iso:
            home = fo_install.openjev_home()
            repo = os.path.join(home, "repo")
            os.makedirs(repo)
            with open(os.path.join(repo, ".git"), "w") as f:  # a linked worktree has a .git FILE
                f.write("gitdir: /elsewhere\n")
            with open(os.path.join(repo, "my-work.txt"), "w") as f:
                f.write("keep")
            with mock.patch.object(fo_install, "python310", lambda: sys.executable), \
                    mock.patch.object(fo_install.ui, "confirm", lambda *a, **k: True), \
                    mock.patch.object(fo_install, "_run", lambda *a, **k: (1, "stop here")):
                fo_install.install_openjev_mlx(home)
            self.assertTrue(os.path.exists(os.path.join(repo, "my-work.txt")), "the checkout was deleted")
            os.remove(os.path.join(repo, ".git"))  # now an unknown, non-git folder
            with mock.patch.object(fo_install, "python310", lambda: sys.executable), \
                    mock.patch.object(fo_install.ui, "confirm", lambda *a, **k: True):
                self.assertFalse(fo_install.install_openjev_mlx(home))
            self.assertTrue(os.path.exists(os.path.join(repo, "my-work.txt")), "an unknown folder was deleted")

    def test_declining_docker_does_not_save_openjev(self):
        with Isolated():
            with mock.patch.object(fo_install.ui, "confirm", lambda *a, **k: False):
                with self.assertRaises(fo_install._Back):
                    fo_install.install_openjev_docker({"docker": True})

    def test_launch_failure_is_a_status(self):
        with Isolated() as iso:
            cfg = codex_that_cannot_start(iso.home)
            r = fo_providers.run(cfg, "codex", "x", out_file=os.path.join(iso.home, "o.md"), cwd=iso.home, host="unknown")
            self.assertEqual(r["status"], "failed")
            self.assertFalse(os.path.exists(os.path.join(iso.home, "o.md")))


class Round3(unittest.TestCase):
    """Installer review round 3 (Sol and Astra on 233ca16)."""

    def test_same_engine_by_default_keeps_its_settings(self):
        with Isolated() as iso:
            fo_config.write_file({"jev": {"api_key_file": "~/k", "key_source": "file", "model": "jev-1.14.0"}})
            p = subprocess.run([sys.executable, "-I", FO, "setup", "--jev", "typesafe", "--providers", "claude",
                                "--telemetry", "no"], capture_output=True, text=True, env=clean_env(iso.home), timeout=60)
            self.assertEqual(p.returncode, 0, p.stderr)
            cfg = read_json(os.path.join(iso.home, ".config", "frontier-orchestration", "config.json"))
            self.assertEqual(cfg["jev"]["api_key_file"], "~/k")
            self.assertEqual(cfg["jev"]["model"], "jev-1.14.0")

    def test_interrupt_leaves_no_empty_answer(self):
        with Isolated() as iso:
            b = os.path.join(iso.home, "bin")
            os.makedirs(b)
            fake_cli(b, "claude", "sleep 30")
            os.environ["PATH"] = b + ":/usr/bin:/bin"
            out = os.path.join(iso.home, "o.md")
            with mock.patch.object(subprocess.Popen, "communicate", side_effect=KeyboardInterrupt()):
                with self.assertRaises(KeyboardInterrupt):
                    fo_providers.run(fo_config.load(), "claude", "x", out_file=out, cwd=iso.home, host="unknown")
            self.assertFalse(os.path.exists(out))

    def test_sigterm_stops_the_vendor_cli(self):
        with Isolated() as iso:
            b = os.path.join(iso.home, "bin")
            os.makedirs(b)
            pidfile = os.path.join(iso.home, "cli.pid")
            fake_cli(b, "codex", f"echo $$ > {pidfile}; while :; do sleep 1; done")
            p = subprocess.Popen([sys.executable, "-I", FO, "ask", "codex", "--prompt", "x"],
                                 env=clean_env(iso.home, PATH=b + ":/usr/bin:/bin"),
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            deadline = time.time() + 20
            while not os.path.exists(pidfile) and time.time() < deadline:
                time.sleep(0.1)
            time.sleep(0.3)
            p.send_signal(signal.SIGTERM)
            p.wait(timeout=20)
            time.sleep(0.5)
            self.assertFalse(alive(int(read_text(pidfile).strip())), "the vendor CLI outlived SIGTERM")

    def test_installer_saves_the_transport_it_checked(self):
        with Isolated() as iso:
            b = os.path.join(iso.home, "bin")
            os.makedirs(b)
            signed_in_clis(b)
            fo_config.write_file({"providers": {"gemini": {"via": "gemini-cli", "model": "gemini-4-argon"}}})
            env = clean_env(iso.home, PATH=b + ":/usr/bin:/bin", FO_TUI="plain")
            p = subprocess.run([sys.executable, "-I", FO, "install"], input="4\n3\n2\n\n",
                               capture_output=True, text=True, env=env, timeout=60)
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            g = read_json(os.path.join(iso.home, ".config", "frontier-orchestration", "config.json"))["providers"]["gemini"]
            self.assertNotIn("via", g)
            self.assertEqual(g["model"], "gemini-4-argon")

    def test_key_file_replaced_atomically_and_private(self):
        with Isolated() as iso:
            path = os.path.join(fo_config.config_dir(), "typesafe_api_key.key")
            os.makedirs(os.path.dirname(path))
            with open(path, "w") as f:
                f.write("old\n")
            os.chmod(path, 0o644)
            before = os.stat(path).st_ino
            with mock.patch.object(fo_install.sys, "platform", "linux"), \
                    mock.patch.object(fo_install.ui, "password", lambda *a, **k: "new-key-123456"):
                fo_install.store_new_key("TYPESAFE_API_KEY", "TypeSafe")
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            self.assertNotEqual(os.stat(path).st_ino, before, "the old wide-mode file was written in place")
            self.assertEqual(read_text(path).strip(), "new-key-123456")

    def test_consent_text_says_persistent(self):
        src = read_text(fo_install.__file__)
        self.assertIn("any persistent id", src)


class Round4(unittest.TestCase):
    """Installer review round 4 (Astra on 37e4997)."""

    def test_fresh_setup_records_the_engine(self):
        with Isolated() as iso:
            run = lambda *a: subprocess.run([sys.executable, "-I", FO, "setup", *a], capture_output=True,  # noqa: E731
                                            text=True, env=clean_env(iso.home), timeout=60)
            self.assertEqual(run("--jev", "typesafe", "--providers", "claude", "--telemetry", "no").returncode, 0)
            self.assertEqual(run("--non-interactive").returncode, 0, "the engine answer was not recorded")

    def test_unreadable_key_file_is_a_jev_error(self):
        with Isolated() as iso:
            p = os.path.join(iso.home, "k")
            fd = os.open(p, os.O_WRONLY | os.O_CREAT, 0o600)
            os.close(fd)
            with mock.patch("builtins.open", side_effect=PermissionError(13, "Permission denied")):
                with self.assertRaises(fo_jev.JevError):
                    fo_jev.api_key({"backend": "custom", "api_key_file": p, "key_source": "file"})

    def test_child_starts_with_signals_unblocked(self):
        with Isolated() as iso:
            b = os.path.join(iso.home, "bin")
            os.makedirs(b)
            fake_cli(b, "claude", "grep SigBlk /proc/self/status 2>/dev/null || echo SigBlk: ok")
            os.environ["PATH"] = b + ":/usr/bin:/bin"
            out = os.path.join(iso.home, "o.md")
            r = fo_providers.run(fo_config.load(), "claude", "x", out_file=out, cwd=iso.home, host="unknown")
            self.assertEqual(r["status"], "ok")
            self.assertEqual(signal.pthread_sigmask(signal.SIG_BLOCK, set()), set(), "signals stayed blocked here")


class Round4Sol(unittest.TestCase):
    """Installer review round 4 (Sol on 37e4997)."""

    def test_env_engine_counts_when_deciding_to_keep_settings(self):
        with Isolated() as iso:
            fo_config.write_file({"jev": {"base_url": "https://old-openjev.example"}})
            p = subprocess.run([sys.executable, "-I", FO, "setup", "--jev", "typesafe", "--providers", "claude",
                                "--telemetry", "no"], capture_output=True, text=True,
                               env=clean_env(iso.home, FO_JEV_BACKEND="openjev"), timeout=60)
            self.assertEqual(p.returncode, 0, p.stderr)
            cfg = read_json(os.path.join(iso.home, ".config", "frontier-orchestration", "config.json"))
            self.assertEqual(cfg["jev"], {"backend": "typesafe"}, "the old OpenJev URL survived the engine change")
            os.environ.pop("FO_JEV_BACKEND", None)
            self.assertEqual(fo_config.jev_settings(fo_config.load())["base_url"], "https://api.typesafe.ai")

    def test_interrupt_during_timeout_grace_leaves_no_placeholder(self):
        with Isolated() as iso:
            b = os.path.join(iso.home, "bin")
            os.makedirs(b)
            fake_cli(b, "claude", "sleep 30")
            os.environ["PATH"] = b + ":/usr/bin:/bin"
            out = os.path.join(iso.home, "o.md")
            real = subprocess.Popen.communicate
            calls = []

            def seq(self, *a, **k):
                calls.append(1)
                if len(calls) == 1:
                    raise subprocess.TimeoutExpired("claude", 1)
                if len(calls) == 2:
                    raise KeyboardInterrupt()
                return real(self, *a, **k)

            with mock.patch.object(subprocess.Popen, "communicate", seq):
                with self.assertRaises(KeyboardInterrupt):
                    fo_providers.run(fo_config.load(), "claude", "x", out_file=out, cwd=iso.home, host="unknown")
            self.assertFalse(os.path.exists(out))


class Round5(unittest.TestCase):
    """Installer review round 5 (Astra on 8edf57e)."""

    def setup_with(self, iso, stored, env):
        fo_config.write_file({"jev": stored})
        p = subprocess.run([sys.executable, "-I", FO, "setup", "--jev", "typesafe", "--providers", "claude",
                            "--telemetry", "no"], capture_output=True, text=True, env=clean_env(iso.home, **env), timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr)
        return read_json(os.path.join(iso.home, ".config", "frontier-orchestration", "config.json"))["jev"]

    def test_keep_only_when_stored_and_effective_both_match(self):
        with Isolated() as iso:
            # env says typesafe, file says openjev with a URL: must start clean
            jev = self.setup_with(iso, {"backend": "openjev", "base_url": "https://old-openjev.example"},
                                  {"FO_JEV_BACKEND": "typesafe"})
            self.assertEqual(jev, {"backend": "typesafe"})
        with Isolated() as iso:
            # sparse file (default typesafe), no env: same engine, keep its key file
            jev = self.setup_with(iso, {"api_key_file": "~/k", "key_source": "file"}, {})
            self.assertEqual(jev["api_key_file"], "~/k")
        with Isolated() as iso:
            # sparse file, env says openjev: start clean
            jev = self.setup_with(iso, {"base_url": "https://old-openjev.example"}, {"FO_JEV_BACKEND": "openjev"})
            self.assertEqual(jev, {"backend": "typesafe"})

    def test_failed_start_leaves_no_placeholder_and_no_blocked_signals(self):
        with Isolated() as iso:
            out = os.path.join(iso.home, "o.md")
            cfg = codex_that_cannot_start(iso.home)
            with mock.patch.object(subprocess, "Popen", side_effect=ValueError("bad")):
                with self.assertRaises(ValueError):
                    fo_providers.run(cfg, "codex", "x", out_file=out, cwd=iso.home, host="unknown")
            self.assertFalse(os.path.exists(out))
            self.assertEqual(signal.pthread_sigmask(signal.SIG_BLOCK, set()), set())


class Round5Sol(unittest.TestCase):
    """Installer review round 5 (Sol on 8edf57e)."""

    def test_env_engine_never_inherits_saved_key_or_url(self):
        with Isolated(FO_JEV_BACKEND="codiv"):
            fo_config.write_file({"jev": {"backend": "typesafe", "api_key_env": "TYPESAFE_API_KEY",
                                          "keychain_service": "TYPESAFE_API_KEY", "key_source": "env",
                                          "base_url": "http://old-gpu.example:8080", "allow_insecure_http": True}})
            js = fo_config.jev_settings(fo_config.load())
            self.assertEqual(js["backend"], "codiv")
            self.assertEqual(js["base_url"], "https://api.codiv.ai")
            self.assertEqual(js["api_key_env"], "CODIV_API_KEY")
            self.assertFalse(js.get("allow_insecure_http"))

    def test_child_signal_handlers_are_default(self):
        import fo_providers as fp
        with mock.patch.object(fp.signal, "signal") as sig, mock.patch.object(fp.signal, "pthread_sigmask"):
            fp._child_signals()
        self.assertIn(mock.call(signal.SIGINT, signal.SIG_DFL), sig.call_args_list)
        self.assertIn(mock.call(signal.SIGTERM, signal.SIG_DFL), sig.call_args_list)

    def test_unwritable_ledger_is_rejected_before_saving(self):
        with Isolated() as iso:
            locked = os.path.join(iso.home, "locked")
            os.makedirs(locked)
            os.chmod(locked, 0o500)
            try:
                self.assertIsNotNone(fo_install._ledger_problem(os.path.join(locked, "ledger")))
                self.assertIsNone(fo_install._ledger_problem(os.path.join(iso.home, "ok-ledger")))
            finally:
                os.chmod(locked, 0o700)


class Round6(unittest.TestCase):
    """Installer review round 6 (Astra on c1a835e)."""

    def test_a_run_holding_the_answer_path_is_never_disturbed(self):
        with Isolated() as iso:
            os.makedirs(os.path.join(iso.home, "bin"))
            signed_in_clis(os.path.join(iso.home, "bin"))
            os.environ["PATH"] = os.path.join(iso.home, "bin") + ":/usr/bin:/bin"
            out = os.path.join(iso.home, "shared.md")
            with open(out, "w") as f:
                f.write("THE OTHER RUN'S ANSWER")
            import fcntl
            holder = os.open(out, os.O_RDWR)  # the other run, still working: it holds the path
            fcntl.flock(holder, fcntl.LOCK_EX)
            try:
                r = fo_providers.run(fo_config.load(), "codex", "x", out_file=out, cwd=iso.home, host="unknown")
            finally:
                os.close(holder)
            self.assertEqual(r["status"], "failed")
            self.assertIn("another run", r["stderr_tail"])
            self.assertEqual(read_text(out), "THE OTHER RUN'S ANSWER")
            self.assertEqual(signal.pthread_sigmask(signal.SIG_BLOCK, set()), set())
            # once the other run ends, a new run replaces the stale answer with its own
            r = fo_providers.run(fo_config.load(), "codex", "x", out_file=out, cwd=iso.home, host="unknown")
            self.assertEqual((r["status"], read_text(out).strip()), ("ok", "CODEX REVIEW"))

    def test_ledger_with_a_file_named_lessons_is_rejected(self):
        with Isolated() as iso:
            led = os.path.join(iso.home, "led")
            os.makedirs(led)
            open(os.path.join(led, "lessons"), "w").close()
            self.assertIn("not a folder", fo_install._ledger_problem(led))

    def test_ledger_failure_keeps_the_old_config(self):
        with Isolated() as iso:
            os.makedirs(os.path.join(iso.home, "bin"))
            signed_in_clis(os.path.join(iso.home, "bin"))
            fo_config.write_file({"jev": {"backend": "typesafe"}})
            env = clean_env(iso.home, PATH=os.path.join(iso.home, "bin") + ":/usr/bin:/bin", FO_TUI="plain")
            code = ("import sys; sys.path.insert(0, %r); import fo_install, fo_lessons\n"
                    "def boom(): raise OSError(28, 'No space left on device')\n"
                    "fo_lessons.require_vault = boom\n"
                    "sys.exit(fo_install.run_installer())") % os.path.dirname(fo_install.__file__)
            p = subprocess.run([sys.executable, "-c", code], input="4\n1\n2\n\n", capture_output=True, text=True,
                               env=env, timeout=60)
            self.assertNotEqual(p.returncode, 0)
            cfg = read_json(os.path.join(iso.home, ".config", "frontier-orchestration", "config.json"))
            self.assertEqual(cfg["jev"]["backend"], "typesafe", "the config was replaced before the ledger failed")


class Migration(unittest.TestCase):
    def test_bridge_config_still_loads(self):
        with Isolated():
            fo_config.write_file({"providers": {"codex": {"via": "bridge"}, "claude": {"via": "codex-plugin"}}})
            cfg = fo_config.load()
            self.assertEqual(cfg["providers"]["codex"]["via"], "auto")
            self.assertEqual(fo_config.set_dotted(fo_config.read_file(), "jev.backend", "off")["providers"]["codex"]["via"], "auto")


class GeminiOAuth(unittest.TestCase):
    def test_oauth_creds_count_as_signed_in(self):
        with Isolated() as iso:
            b = os.path.join(iso.home, "bin")
            os.makedirs(b)
            fake_cli(b, "gemini", "true")
            os.environ["PATH"] = b + ":/usr/bin:/bin"
            self.assertFalse(fo_providers.session("gemini")["signed_in"])
            os.makedirs(os.path.join(iso.home, ".gemini"))
            creds = os.path.join(iso.home, ".gemini", "oauth_creds.json")
            for bad in ("", "not json", "[]", '{"refresh_token": ""}'):
                with open(creds, "w") as f:
                    f.write(bad)
                self.assertFalse(fo_providers.session("gemini")["signed_in"], f"accepted {bad!r}")
            with open(creds, "w") as f:
                f.write('{"refresh_token": "fake-refresh-token"}')
            self.assertTrue(fo_providers.session("gemini")["signed_in"])


class EscapeAlone(unittest.TestCase):
    def test_lone_escape_cancels_without_a_second_key(self):
        calls = []

        def fake_read(n):
            calls.append(n)
            if len(calls) > 1:
                raise AssertionError("blocked waiting for a second key")
            return "\x1b"

        with mock.patch.object(fo_tui, "_read_tty", fake_read), mock.patch.object(fo_tui, "_more_input", lambda w: False):
            self.assertEqual(fo_tui._key(), "esc")


class ProvidersShowSignIn(unittest.TestCase):
    def test_not_signed_in_is_shown_with_the_fix(self):
        with Isolated() as iso:
            b = os.path.join(iso.home, "bin")
            os.makedirs(b)
            signed_in_clis(b)
            fake_cli(b, "codex", 'echo "Not logged in"; exit 1')
            p = subprocess.run([sys.executable, "-I", FO, "providers"], capture_output=True, text=True,
                               env=clean_env(iso.home, PATH=b + ":/usr/bin:/bin"), timeout=60)
            self.assertIn("[not signed in]", p.stdout)
            self.assertIn("sign in: codex login", p.stdout)
            self.assertIn("[signed in]", p.stdout)


class Round6Sol(unittest.TestCase):
    def test_doctor_live_fails_on_a_signed_out_provider(self):
        with Isolated() as iso:
            b = os.path.join(iso.home, "bin")
            os.makedirs(b)
            signed_in_clis(b)
            fake_cli(b, "codex", 'echo "Not logged in"; exit 1')
            env = clean_env(iso.home, PATH=b + ":/usr/bin:/bin")
            run = lambda *a: subprocess.run([sys.executable, "-I", FO, *a], capture_output=True, text=True,
                                            env=env, timeout=60)
            p = run("setup", "--jev", "off", "--providers", "codex,gemini", "--telemetry", "no")
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            p = run("doctor", "--live")
            self.assertNotEqual(p.returncode, 0, p.stdout)
            self.assertIn("FAIL codex: not signed in", p.stdout)
            self.assertNotIn("FAIL gemini", p.stdout)
            fake_cli(b, "codex", 'echo "Logged in using ChatGPT"')
            p = run("doctor", "--live")
            self.assertNotIn("not signed in", p.stdout)


class Round7(unittest.TestCase):
    """Installer review round 7 (Sol and Astra on 7e1d81a)."""

    def test_installer_sets_up_the_ledger_the_environment_selects(self):
        with Isolated() as iso:
            os.makedirs(os.path.join(iso.home, "bin"))
            signed_in_clis(os.path.join(iso.home, "bin"))
            chosen = os.path.join(iso.home, "env-ledger")
            env = clean_env(iso.home, PATH=os.path.join(iso.home, "bin") + ":/usr/bin:/bin", FO_TUI="plain",
                            FO_LEDGER=chosen)
            p = subprocess.run([sys.executable, "-I", FO, "install"], input="4\n1\n2\n\n", capture_output=True,
                               text=True, env=env, timeout=60)
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            self.assertTrue(os.path.isdir(os.path.join(chosen, "lessons")), "the ledger in effect was not set up")
            default_root = fo_config.ledger_paths(fo_config.load(with_env=False))[0]
            self.assertFalse(os.path.exists(os.path.join(default_root, "lessons")), "a ledger nobody uses was set up")

    def test_doctor_checks_the_gemini_cli_when_that_is_the_transport(self):
        with Isolated() as iso:
            b = os.path.join(iso.home, "bin")
            os.makedirs(b)
            signed_in_clis(b)
            fake_cli(b, "agy", 'echo "You are not logged into Antigravity"; exit 1')
            fake_cli(b, "gemini", "echo GEMINI")
            env = clean_env(iso.home, PATH=b + ":/usr/bin:/bin", GEMINI_API_KEY="fake-gemini-key")
            fo_config.write_file({"jev": {"backend": "off"}, "telemetry": {"enabled": False},
                                  "providers": {"claude": {"enabled": False}, "codex": {"enabled": True},
                                                "gemini": {"enabled": True, "via": "gemini-cli"}}})
            p = subprocess.run([sys.executable, "-I", FO, "doctor", "--live"], capture_output=True, text=True,
                               env=env, timeout=60)
            self.assertNotIn("FAIL gemini", p.stdout)
            self.assertEqual(p.returncode, 0, p.stdout)
            self.assertFalse(os.path.exists(os.path.join(b, "agy.argv")), "doctor asked agy, which is not used")

    def test_failed_cleanup_at_start_still_restores_signals(self):
        self.addCleanup(signal.pthread_sigmask, signal.SIG_SETMASK, set())
        with Isolated() as iso:
            out = os.path.join(iso.home, "o.md")
            cfg = codex_that_cannot_start(iso.home)
            with mock.patch.object(subprocess, "Popen", side_effect=FileNotFoundError(2, "No such file")), \
                    mock.patch.object(fo_providers.os, "remove", side_effect=PermissionError(13, "denied")):
                r = fo_providers.run(cfg, "codex", "x", out_file=out, cwd=iso.home, host="unknown")
            self.assertEqual(r["status"], "failed")
            self.assertEqual(signal.pthread_sigmask(signal.SIG_BLOCK, set()), set())

    def test_setup_flags_keep_the_old_config_when_the_ledger_fails(self):
        with Isolated() as iso:
            env = clean_env(iso.home)
            run = lambda *a: subprocess.run([sys.executable, "-I", FO, "setup", *a], capture_output=True,
                                            text=True, env=env, timeout=60)
            p = run("--jev", "off", "--providers", "codex", "--telemetry", "no")
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            path = os.path.join(iso.home, ".config", "frontier-orchestration", "config.json")
            before = read_text(path)
            p = run("--providers", "claude,codex", "--ledger-log", os.path.join(iso.home, "no", "such", "log.md"))
            self.assertNotEqual(p.returncode, 0, p.stdout)
            self.assertEqual(read_text(path), before, "the config was replaced before the ledger failed")


class Round8(unittest.TestCase):
    """Installer review round 8 (Astra on 8a6bca0)."""

    def test_hard_linked_answer_is_never_emptied(self):
        with Isolated() as iso:
            os.makedirs(os.path.join(iso.home, "bin"))
            signed_in_clis(os.path.join(iso.home, "bin"))
            os.environ["PATH"] = os.path.join(iso.home, "bin") + ":/usr/bin:/bin"
            out, archive = os.path.join(iso.home, "answer.md"), os.path.join(iso.home, "archive.md")
            with open(out, "w") as f:
                f.write("ARCHIVED VERDICT")
            os.link(out, archive)
            r = fo_providers.run(fo_config.load(), "codex", "x", out_file=out, cwd=iso.home, host="unknown")
            self.assertEqual(r["status"], "failed")
            self.assertIn("hard links", r["stderr_tail"])
            self.assertEqual(read_text(archive), "ARCHIVED VERDICT")

    def test_env_engine_cannot_hide_a_bad_stored_engine(self):
        with Isolated() as iso:
            p = subprocess.run([sys.executable, "-I", FO, "setup", "--jev", "typsafe", "--providers", "codex",
                                "--telemetry", "no"], capture_output=True, text=True,
                               env=clean_env(iso.home, FO_JEV_BACKEND="off"), timeout=60)
            self.assertNotEqual(p.returncode, 0, p.stdout)
            self.assertFalse(os.path.exists(os.path.join(iso.home, ".config", "frontier-orchestration", "config.json")))

    def test_a_folder_as_the_ledger_log_is_rejected(self):
        with Isolated() as iso:
            log = os.path.join(iso.home, "log.md")
            os.makedirs(log)
            p = subprocess.run([sys.executable, "-I", FO, "setup", "--jev", "off", "--providers", "codex",
                                "--telemetry", "no", "--ledger-log", log], capture_output=True, text=True,
                               env=clean_env(iso.home), timeout=60)
            self.assertNotEqual(p.returncode, 0, p.stdout)
            self.assertIn("not a regular file", p.stderr)
            self.assertFalse(os.path.exists(os.path.join(iso.home, ".config", "frontier-orchestration", "config.json")))


class Round9(unittest.TestCase):
    """Pre-existing ledger gaps named in review round 9 (Sol and Astra on c481dd3)."""

    def setup(self, home, *extra):
        return subprocess.run([sys.executable, "-I", FO, "setup", "--jev", "off", "--providers", "codex",
                               "--telemetry", "no", *extra], capture_output=True, text=True,
                              env=clean_env(home), timeout=60)

    def cfg_path(self, home):
        return os.path.join(home, ".config", "frontier-orchestration", "config.json")

    def test_read_only_runs_folder_is_rejected_before_saving(self):
        with Isolated() as iso:
            led = os.path.join(iso.home, "led")
            os.makedirs(os.path.join(led, "lessons"))
            os.makedirs(os.path.join(led, "runs"))
            os.chmod(os.path.join(led, "runs"), 0o500)
            try:
                p = self.setup(iso.home, "--ledger", led)
            finally:
                os.chmod(os.path.join(led, "runs"), 0o700)
            self.assertNotEqual(p.returncode, 0, p.stdout)
            self.assertIn("not writable", p.stderr)
            self.assertFalse(os.path.exists(self.cfg_path(iso.home)))

    def test_log_in_a_read_only_folder_is_rejected_before_saving(self):
        with Isolated() as iso:
            d = os.path.join(iso.home, "shared")
            os.makedirs(d)
            log = os.path.join(d, "log.md")
            with open(log, "w") as f:
                f.write(fo_lessons.LOG_HEADER)
            os.chmod(d, 0o500)
            try:
                p = self.setup(iso.home, "--ledger-log", log)
            finally:
                os.chmod(d, 0o700)
            self.assertNotEqual(p.returncode, 0, p.stdout)
            self.assertIn("not writable", p.stderr)
            self.assertFalse(os.path.exists(self.cfg_path(iso.home)))

    def test_starting_the_log_never_replaces_one_made_meanwhile(self):
        with Isolated() as iso:
            root = os.path.join(iso.home, "led")
            log = os.path.join(root, "log.md")
            fo_lessons.configure(root, log)
            os.makedirs(root)
            with open(log, "w") as f:
                f.write("ENTRY FROM ANOTHER PROCESS\n")
            real_exists = os.path.exists
            # the other process creates the log right after our "is it missing?" check
            with mock.patch.object(fo_lessons.os.path, "exists",
                                   lambda p: False if p == log else real_exists(p)):
                fo_lessons.require_vault()
            self.assertEqual(read_text(log), "ENTRY FROM ANOTHER PROCESS\n")


class Round10(unittest.TestCase):
    """Installer review round 10 (Astra on 7f406c7)."""

    def test_new_log_needs_no_hard_links(self):
        with Isolated() as iso:
            root = os.path.join(iso.home, "led")
            log = os.path.join(root, "log.md")
            fo_lessons.configure(root, log)
            # a file system without hard links (exFAT, some network shares)
            with mock.patch.object(fo_lessons.os, "link", side_effect=PermissionError(1, "Operation not permitted")):
                fo_lessons.require_vault()
            self.assertEqual(read_text(log), fo_lessons.LOG_HEADER)


class Round11(unittest.TestCase):
    """Pre-existing item from review round 11 (Sol on 5d5894b)."""

    def test_unwritable_log_lock_is_rejected_before_saving(self):
        with Isolated() as iso:
            d = os.path.join(iso.home, "shared")
            os.makedirs(d)
            log = os.path.join(d, "log.md")
            with open(log, "w") as f:
                f.write(fo_lessons.LOG_HEADER)
            lock = os.path.join(d, ".log.md.fo-lock")
            open(lock, "w").close()
            os.chmod(lock, 0o400)
            p = subprocess.run([sys.executable, "-I", FO, "setup", "--jev", "off", "--providers", "codex",
                                "--telemetry", "no", "--ledger-log", log], capture_output=True, text=True,
                               env=clean_env(iso.home), timeout=60)
            self.assertNotEqual(p.returncode, 0, p.stdout)
            self.assertIn("cannot be opened", p.stderr)
            self.assertFalse(os.path.exists(os.path.join(iso.home, ".config", "frontier-orchestration", "config.json")))


if __name__ == "__main__":
    unittest.main()
