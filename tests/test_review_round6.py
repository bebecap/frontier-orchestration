"""Regression tests for the pre-existing items both reviewers listed in round 6 (on 4dc7c7d)."""

from __future__ import annotations

import os
import socket
import threading
import time
import unittest

from helpers import Isolated, read_text, run_fo

import fo_jev
import fo_lessons
import fo_route


def trickle_server(payload: bytes, gap: float):
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)

    def serve():
        try:
            conn, _ = srv.accept()
            conn.recv(65536)
            for i in range(len(payload)):
                conn.sendall(payload[i:i + 1])
                time.sleep(gap)
            conn.close()
        except OSError:
            pass

    threading.Thread(target=serve, daemon=True).start()
    return srv


class DeadlineCoversEveryPhase(unittest.TestCase):
    def check(self, payload):
        srv = trickle_server(payload, 0.04)
        try:
            js = {"backend": "custom", "base_url": f"http://127.0.0.1:{srv.getsockname()[1]}", "model": "m",
                  "key_required": False, "timeout_s": 0.3}
            t0 = time.monotonic()
            with self.assertRaises(fo_jev.JevError):
                fo_jev.ping(js)
            self.assertLess(time.monotonic() - t0, 0.9)
        finally:
            srv.close()

    def test_trickled_headers(self):
        self.check(b"HTTP/1.1 200 OK\r\n" + b"X-Slow: " + b"a" * 200 + b"\r\n\r\n")

    def test_trickled_chunk_framing(self):
        self.check(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n" + b"0" * 200 + b"1\r\n{\r\n0\r\n\r\n")


class HugeIntegerInRoute(unittest.TestCase):
    def test_probability_overflow_is_a_route_error(self):
        with self.assertRaises(fo_route.RouteError):
            fo_route.probability(10 ** 400, "x")


class NoHeadingInjection(unittest.TestCase):
    def test_why_cannot_forge_a_section(self):
        with Isolated() as iso:
            run_fo(iso.home, "lessons", "run-new", "--id", "2026-01-01-x-a", "--project", "x", "--artefact", "code",
                   "--title", "t")
            run_fo(iso.home, "lessons", "run-new", "--id", "2026-01-02-x-b", "--project", "x", "--artefact", "code",
                   "--title", "t")
            run_fo(iso.home, "lessons", "add", "--slug", "s", "--rule", "Rule one.", "--why", "## Evidence",
                   "--apply", "# Top", "--kind", "routing", "--tags", "x", "--run", "2026-01-01-x-a", "--evidence", "e1")
            run_fo(iso.home, "lessons", "reinforce", "FO-L001", "--run", "2026-01-02-x-b", "--evidence", "second")
            led = os.path.join(iso.home, ".local", "share", "frontier-orchestration", "ledger", "lessons")
            text = read_text(os.path.join(led, os.listdir(led)[0]))
            self.assertEqual(text.count("\n## Evidence\n"), 1)
            self.assertNotIn("\n# Top", text)
            evidence = text.split("\n## Evidence\n", 1)[1]
            self.assertIn("second", evidence.split("\n## ")[0])
            self.assertEqual(fo_lessons.one_line("## x"), "\\## x")


if __name__ == "__main__":
    unittest.main()
