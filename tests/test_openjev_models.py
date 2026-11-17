"""OpenJev builds (pinned DiffusionGemma MLX weights), the served-model check, and the launcher."""

from __future__ import annotations

import http.server
import json
import os
import subprocess
import threading
import unittest
from unittest import mock

from helpers import UNAVAILABLE_URL, Isolated

import fo_install


def models_server(names):
    """A real local server whose GET /v1/models lists `names`."""
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps({"models": [{"name": n} for n in names]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


class ServedModel(unittest.TestCase):
    def test_default_model_is_not_written(self):
        httpd, url = models_server(["openjev-latest", "openjev-0.1", "diffusiongemma-26b"])
        try:
            with Isolated(FO_OPENJEV_URL=url):
                self.assertEqual(fo_install.setup_openjev(), {"backend": "openjev", "base_url": url})
        finally:
            httpd.shutdown()

    def test_another_served_model_is_configured_by_name(self):
        httpd, url = models_server(["clm-v0.1"])
        try:
            with Isolated(FO_OPENJEV_URL=url):
                self.assertEqual(fo_install.setup_openjev(), {"backend": "openjev", "base_url": url, "model": "clm-v0.1"})
        finally:
            httpd.shutdown()

    def test_unknown_names_from_the_server_are_never_stored(self):
        httpd, url = models_server(["$(touch /tmp/x)", "evil\nname"])
        try:
            self.assertEqual(fo_install.openjev_models(url), ["jev-latest"])
        finally:
            httpd.shutdown()

    def test_unavailable_server_is_not_running(self):
        self.assertEqual(fo_install.openjev_models(UNAVAILABLE_URL), [])


class Builds(unittest.TestCase):
    def test_pinned_builds_cap_the_mlx_cache_at_1_gb(self):
        with Isolated() as iso:
            for key, b in fo_install.OPENJEV_BUILDS.items():
                self.assertEqual(b["cache_gb"], 1, key)
            text = open(fo_install.write_launcher(iso.home, "/w", 1)).read()
            self.assertIn("OPENJEV_MLX_CACHE_LIMIT_GB=${OPENJEV_MLX_CACHE_LIMIT_GB:-1}", text)

    def test_every_build_is_pinned_and_valid(self):
        self.assertIn(fo_install.DEFAULT_BUILD, fo_install.OPENJEV_BUILDS)
        for key, b in fo_install.OPENJEV_BUILDS.items():
            self.assertTrue(fo_install.HF_ID.fullmatch(b["repo"]), key)
            self.assertRegex(b["revision"], r"^[0-9a-f]{40}$", f"{key} must be pinned to a commit")

    def test_default_build_comes_first_and_custom_ids_are_checked(self):
        seen = {}

        def select(message, options, *a, **k):
            seen["keys"] = [o[0] for o in options]
            return "custom"

        def text(message, default="", placeholder="", validate=None):
            for bad in ("x/$(touch pwned)", "noslash", "a/b@--upload-pack=x", "a/b@"):
                self.assertTrue(validate(bad), bad)
            self.assertIsNone(validate("mlx-community/diffusiongemma-26B-A4B-it-mxfp4@c8982e7"))
            return "mlx-community/diffusiongemma-26B-A4B-it-mxfp4@c8982e704b68d7529678564e99ebbc08112d4e11"
        with mock.patch.object(fo_install.ui, "select", select), mock.patch.object(fo_install.ui, "text", text), \
                mock.patch.object(fo_install.ui, "warn", lambda *a: None):
            repo, rev, _, _, cache = fo_install.choose_openjev_build()
        self.assertEqual(cache, 4, "a custom build keeps OpenJev's 4 GB cache advice")
        self.assertEqual(seen["keys"][0], fo_install.DEFAULT_BUILD)
        self.assertEqual((repo, rev), ("mlx-community/diffusiongemma-26B-A4B-it-mxfp4",
                                       "c8982e704b68d7529678564e99ebbc08112d4e11"))

    def test_download_uses_the_pinned_revision_and_rejects_bad_ids(self):
        with Isolated() as iso:
            home = fo_install.openjev_home()
            snap = os.path.join(iso.home, "hf", "snapshots", "992c8d04")
            os.makedirs(snap)
            open(os.path.join(snap, "config.json"), "w").close()
            calls = []

            def run(cmd, **kw):  # huggingface_hub prints progress and warnings on stderr
                calls.append(list(cmd))
                return subprocess.CompletedProcess(cmd, 0, stdout=snap + "\n",
                                                   stderr="Fetching 13 files: 100%\nWarning: unauthenticated\n")
            b = fo_install.OPENJEV_BUILDS["compact"]
            with mock.patch.object(fo_install.subprocess, "run", run):
                self.assertEqual(fo_install.download_weights(home, b["repo"], b["revision"]), snap)
                self.assertEqual(calls[0][-2:], [b["repo"], b["revision"]])
                self.assertEqual(fo_install.download_weights(home, "x/$(id)", "main"), "")
                self.assertEqual(fo_install.download_weights(home, b["repo"], "--bad"), "")
            self.assertEqual(len(calls), 1, "a bad id reached the downloader")


class Launcher(unittest.TestCase):
    def test_launcher_points_at_the_pinned_snapshot_quoted(self):
        with Isolated() as iso:
            weights = os.path.join(iso.home, "hf cache", "snap's $(touch pwned)")
            os.makedirs(os.path.join(iso.home, "venv", "bin"))
            fake = os.path.join(iso.home, "venv", "bin", "python")  # prints what OpenJev would read
            with open(fake, "w") as f:
                f.write('#!/bin/sh\nprintf "%s|%s" "$OPENJEV_BACKEND" "$OPENJEV_MLX_MODEL"\n')
            os.chmod(fake, 0o755)
            path = fo_install.write_launcher(iso.home, weights)
            out = subprocess.run([path], capture_output=True, text=True, timeout=20).stdout
            self.assertEqual(out, "mlx|" + weights)
            self.assertFalse(os.path.exists(os.path.join(iso.home, "pwned")), "the folder name ran as a command")


if __name__ == "__main__":
    unittest.main()
