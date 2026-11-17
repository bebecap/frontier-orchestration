"""OpenJev install: finding a Python 3.10+, the uv offer, and the pinned upstream commit."""

from __future__ import annotations

import os
import stat
import sys
import unittest
from unittest import mock

from helpers import Isolated

import fo_install


def script(path: str, body: str) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write("#!/bin/sh\n" + body + "\n")
    os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
    return path


class FindPython(unittest.TestCase):
    def test_finds_python_in_local_bin_when_not_on_path(self):
        # A fresh Mac: fo.py runs on /usr/bin/python3 (3.9); uv or pipx put 3.11 in ~/.local/bin.
        with Isolated() as iso:
            py = script(os.path.join(iso.home, ".local", "bin", "python3.11"), "echo True")
            empty = os.path.join(iso.home, "empty-bin")  # no Python on PATH (Debian has /usr/bin/python3.11)
            os.makedirs(empty)
            os.environ["PATH"] = empty
            with mock.patch.object(fo_install.sys, "executable", "/usr/bin/false"):
                self.assertEqual(fo_install.python310(), py)

    def test_offers_uv_when_there_is_no_python(self):
        with Isolated() as iso:
            log = os.path.join(iso.home, "uv.argv")
            uv = script(os.path.join(iso.home, "bin", "uv"), f'printf "%s " "$@" > {log}')
            with mock.patch.object(fo_install, "python310", side_effect=["", "/new/python3.12"]), \
                    mock.patch.object(fo_install, "uv_path", lambda: uv), \
                    mock.patch.object(fo_install.ui, "confirm", lambda *a, **k: True):
                self.assertEqual(fo_install.get_python310(), "/new/python3.12")
            with open(log) as f:
                self.assertEqual(f.read().split(), ["python", "install", "3.12"])

    def test_no_python_and_no_uv_explains_both_fixes(self):
        # The hint depends on the platform: Homebrew on macOS, the distribution's tools on Linux.
        for platform, tool, absent in (("darwin", "brew install python@3.12", "apt install"),
                                       ("linux", "apt install python3.12", "brew")):
            with Isolated():
                shown = []
                with mock.patch.object(fo_install, "python310", lambda: ""), \
                        mock.patch.object(fo_install, "uv_path", lambda: ""), \
                        mock.patch.object(fo_install.sys, "platform", platform), \
                        mock.patch.object(fo_install.ui, "error", shown.append):
                    self.assertEqual(fo_install.get_python310(), "")
                self.assertIn(tool, shown[0], platform)
                self.assertNotIn(absent, shown[0], platform)
                self.assertIn("uv python install 3.12", shown[0].splitlines()[1], platform)  # uv first


class PinnedCommit(unittest.TestCase):
    def fake_run(self, calls, repo, dirty=""):
        def run(cmd, timeout=1800, cwd=None, env=None):
            calls.append(list(cmd))
            if cmd[:2] == ["git", "clone"]:
                os.makedirs(os.path.join(repo, ".git"))
            if cmd[-3:] == ["status", "--porcelain", "--untracked-files=no"]:
                return 0, dirty
            if cmd[-2:] == ["rev-parse", "HEAD"]:
                return 0, fo_install.OPENJEV_COMMIT + "\n"
            return 0, ""
        return run

    def test_fresh_install_checks_out_the_tested_commit(self):
        with Isolated():
            home = fo_install.openjev_home()
            repo = os.path.join(home, "repo")
            calls = []
            with mock.patch.object(fo_install, "python310", lambda: sys.executable), \
                    mock.patch.object(fo_install.ui, "confirm", lambda *a, **k: True), \
                    mock.patch.object(fo_install, "_run", self.fake_run(calls, repo)):
                self.assertTrue(fo_install.install_openjev_mlx(home))
            self.assertIn(["git", "clone", "--filter=blob:none", "--no-checkout", fo_install.OPENJEV_REPO, repo], calls)
            self.assertIn(["git", "-C", repo, "checkout", "-q", "--detach", fo_install.OPENJEV_COMMIT], calls)
            with open(os.path.join(home, ".installed")) as f:
                self.assertEqual(f.read().strip(), fo_install.OPENJEV_COMMIT)

    def test_reused_checkout_with_local_changes_is_left_alone(self):
        with Isolated():
            home = fo_install.openjev_home()
            repo = os.path.join(home, "repo")
            os.makedirs(os.path.join(repo, ".git"))
            calls = []
            with mock.patch.object(fo_install, "python310", lambda: sys.executable), \
                    mock.patch.object(fo_install.ui, "confirm", lambda *a, **k: True), \
                    mock.patch.object(fo_install, "_run", self.fake_run(calls, repo, dirty=" M openjev/server.py")):
                self.assertFalse(fo_install.install_openjev_mlx(home))
            self.assertFalse(any("checkout" in c for c in calls), "a checkout with local changes was moved")

    def test_ref_that_looks_like_an_option_is_refused(self):
        with Isolated():
            os.environ["FO_OPENJEV_REF"] = "--upload-pack=touch /tmp/pwned"
            calls = []
            home = fo_install.openjev_home()
            with mock.patch.object(fo_install, "python310", lambda: sys.executable), \
                    mock.patch.object(fo_install.ui, "confirm", lambda *a, **k: True), \
                    mock.patch.object(fo_install, "_run", self.fake_run(calls, os.path.join(home, "repo"))):
                self.assertFalse(fo_install.install_openjev_mlx(home))
            self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
