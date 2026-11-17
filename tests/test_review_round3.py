"""Regression tests for the round-3 blind review findings (Sol and Astra on c6d3e87)."""

from __future__ import annotations

import fcntl
import http.server
import os
import subprocess
import sys
import threading
import time
import unittest

from helpers import SCRIPTS, Isolated, Server, read_text

import fo_config
import fo_lessons
import fo_route


def judge(conf, stakes):
    def reply(p, b, h):
        answers = {}
        for qid, q in b["questions"].items():
            answers[qid] = {"noul": stakes} if q["type"] == "noul" else \
                {"choice": next(iter(q["criteria"])), "confidence": conf}
        return 200, {"answers": answers}
    return reply


class JudgeNumbersMustBeProbabilities(unittest.TestCase):
    def test_malformed_numbers_fail_closed(self):
        with Isolated():
            roster = fo_route.Roster(fo_config.roster())
        spec = {"tasks": {"t": {"candidates": ["opus", "sonnet"]}}}
        for conf, stakes in ((True, 0.99), ("0.9", 0.99), (1.5, 0.99), (0.1, -1), (0.9, None), (float("nan"), 0.5)):
            with self.subTest(conf=conf, stakes=stakes), Isolated(), Server(judge(conf, stakes)) as srv:
                js = {"backend": "custom", "base_url": srv.url, "model": "j", "key_required": False, "timeout_s": 10}
                with self.assertRaises(fo_route.RouteError):
                    fo_route.route(spec, roster, js)

    def test_infinity_from_json(self):
        class H(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                body = b'{"answers": {"route_t": {"choice": "opus", "confidence": 1e309}, "stakes_t": {"noul": 0.99}}}'
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            with Isolated():
                roster = fo_route.Roster(fo_config.roster())
                js = {"backend": "custom", "base_url": f"http://127.0.0.1:{srv.server_address[1]}", "model": "j",
                      "key_required": False, "timeout_s": 10}
                with self.assertRaises(fo_route.RouteError):
                    fo_route.route({"tasks": {"t": {"candidates": ["opus", "sonnet"]}}}, roster, js)
        finally:
            srv.shutdown()
            srv.server_close()


class SharedLogIsLocked(unittest.TestCase):
    def test_log_entry_waits_for_the_log_lock(self):
        with Isolated() as iso:
            log = os.path.join(iso.home, "vault-log.md")
            with open(log, "w") as f:
                f.write("# vault log\n")
            fo_lessons.configure(os.path.join(iso.home, "ledgerA"), log)
            lock_path = os.path.join(iso.home, ".vault-log.md.fo-lock")
            done = {}
            with open(lock_path, "w") as held:
                fcntl.flock(held, fcntl.LOCK_EX)
                t = threading.Thread(target=lambda: (fo_lessons.log_entry("x", ["y"]), done.setdefault("ok", 1)))
                t.start()
                time.sleep(0.3)
                self.assertNotIn("ok", done, "log_entry must wait while another writer holds the log lock")
            t.join(5)
            self.assertIn("ok", done)
            self.assertIn("learn | x", read_text(log))

    def test_two_ledgers_one_log_lose_nothing(self):
        with Isolated() as iso:
            log = os.path.join(iso.home, "shared.md")
            with open(log, "w") as f:
                f.write("# shared\n")
            code = ("import sys; sys.path.insert(0, %r); import fo_lessons as L\n"
                    "L.configure(sys.argv[1], sys.argv[2])\n"
                    "for i in range(20): L.log_entry('e%%d' %% i, [sys.argv[1]])") % SCRIPTS
            procs = [subprocess.Popen([sys.executable, "-c", code, os.path.join(iso.home, f"led{w}"), log])
                     for w in range(3)]
            for p in procs:
                self.assertEqual(p.wait(timeout=60), 0)
            self.assertEqual(read_text(log).count("learn | e"), 60)


class SenderHasAWallClockDeadline(unittest.TestCase):
    def test_trickling_collector_cannot_keep_the_sender_alive(self):
        class Slow(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                self.send_response(200)
                self.send_header("Content-Length", "20")
                self.end_headers()
                for _ in range(20):
                    try:
                        self.wfile.write(b"x")
                        self.wfile.flush()
                    except OSError:
                        return
                    time.sleep(0.9)

            def log_message(self, *a):
                pass

        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Slow)
        srv.daemon_threads = True
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            t0 = time.monotonic()
            p = subprocess.run([sys.executable, "-I", os.path.join(SCRIPTS, "fo_telemetry_send.py"),
                                f"http://127.0.0.1:{srv.server_address[1]}/e"], input=b"{}", timeout=30)
            self.assertEqual(p.returncode, 0)
            self.assertLess(time.monotonic() - t0, 4.0)
        finally:
            srv.shutdown()
            srv.server_close()


if __name__ == "__main__":
    unittest.main()
