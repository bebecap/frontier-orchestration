"""Regression tests for the pre-existing items both reviewers listed in round 5 (on 73537be)."""

from __future__ import annotations

import http.server
import threading
import time
import unittest

from helpers import Isolated, Server

import fo_config
import fo_jev
import fo_route

KEY = "fakereflectedkey0123456789"


class PingNeverPrintsServerModel(unittest.TestCase):
    def test_bare_reflected_key_as_model(self):
        def reply(p, b, h):
            token = h.get("Authorization", "").replace("Bearer ", "")
            return 200, {"model": token, "answers": {"ok": {"noul": 0.9}}}

        with Isolated(MY_KEY=KEY), Server(reply) as srv:
            js = {"backend": "custom", "base_url": srv.url, "model": "jev-latest", "api_key_env": "MY_KEY",
                  "key_required": True, "timeout_s": 10}
            out = fo_jev.ping(js)
            self.assertNotIn(KEY, repr(out))
            self.assertEqual(out["model"], "jev-latest")

    def test_huge_integer_is_refused_cleanly(self):
        with Isolated(), Server(lambda p, b, h: (200, {"answers": {"ok": {"noul": 10 ** 400}}})) as srv:
            js = {"backend": "custom", "base_url": srv.url, "model": "m", "key_required": False, "timeout_s": 10}
            with self.assertRaises(fo_jev.JevError):
                fo_jev.ping(js)


class JudgeIsNotAReviewer(unittest.TestCase):
    def test_only_producer_vendor_enabled_fails(self):
        with Isolated():
            roster = fo_route.Roster(fo_config.roster())
        spec = {"tasks": {"rev": {"needs_independence": True, "producer_vendor": "claude"}}}
        with self.assertRaises(fo_route.RouteError):
            fo_route.route(spec, roster, {"backend": "off"}, enabled={"claude"})
        allowed, problems = fo_route.independent_candidates(
            roster, "rev", {"needs_independence": "yes", "producer_vendor": "claude", "candidates": ["jev", "astra"]})
        self.assertEqual(allowed, ["gpt-6-astra"])
        self.assertTrue(any("judge" in p for p in problems))


class JevHasAWholeRequestDeadline(unittest.TestCase):
    def test_trickling_reply_is_cut_off(self):
        class Slow(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                self.send_response(200)
                self.send_header("Content-Length", "100000")
                self.end_headers()
                for _ in range(100):
                    try:
                        self.wfile.write(b" ")
                        self.wfile.flush()
                    except OSError:
                        return
                    time.sleep(0.1)

            def log_message(self, *a):
                pass

        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Slow)
        srv.daemon_threads = True
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            js = {"backend": "custom", "base_url": f"http://127.0.0.1:{srv.server_address[1]}", "model": "m",
                  "key_required": False, "timeout_s": 0.5}
            t0 = time.monotonic()
            with self.assertRaises(fo_jev.JevError):
                fo_jev.ping(js)
            self.assertLess(time.monotonic() - t0, 2.0)
        finally:
            srv.shutdown()
            srv.server_close()


if __name__ == "__main__":
    unittest.main()
