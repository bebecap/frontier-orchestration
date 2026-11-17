"""Regression tests for the round-4 blind review findings (Sol and Astra on b572fbb)."""

from __future__ import annotations

import os
import socket
import threading
import unittest

from helpers import Isolated, Server, read_text

import fo_config
import fo_jev
import fo_lessons
import fo_route

KEY = "fake-reflected-key-0123456789"


def reflecting_judge(field: str):
    """A judge that puts the request's Authorization header into one answer field."""
    def reply(p, b, h):
        token = h.get("Authorization")
        answers = {}
        for qid, q in b["questions"].items():
            if q["type"] == "noul":
                answers[qid] = {"noul": {"diag": token} if field == "noul" else 0.9}
            else:
                answers[qid] = {"choice": token if field == "choice" else next(iter(q["criteria"])),
                                "confidence": {"diag": token} if field == "confidence" else 0.9}
        return 200, {"answers": answers, "model": token}
    return reply


class NoServerTextInErrorsOrOutput(unittest.TestCase):
    def setUp(self):
        with Isolated():
            self.roster = fo_route.Roster(fo_config.roster())

    def run_route(self, field):
        spec = {"tasks": {"t": {"candidates": ["opus", "sonnet"]}}}
        with Isolated(MY_KEY=KEY), Server(reflecting_judge(field)) as srv:
            js = {"backend": "custom", "base_url": srv.url, "model": "j", "api_key_env": "MY_KEY",
                  "key_required": True, "timeout_s": 10}
            try:
                return repr(fo_route.route(spec, self.roster, js))
            except fo_route.RouteError as e:
                return str(e)

    def test_reflected_key_never_printed(self):
        for field in ("confidence", "noul", "choice"):
            with self.subTest(field=field):
                self.assertNotIn(KEY, self.run_route(field))

    def test_ping_never_prints_server_model_text(self):
        with Isolated(MY_KEY=KEY), Server(reflecting_judge("none")) as srv:
            js = {"backend": "custom", "base_url": srv.url, "model": "j", "api_key_env": "MY_KEY",
                  "key_required": True, "timeout_s": 10}
            self.assertNotIn(KEY, repr(fo_jev.ping(js)))


class BadStatusLineIsCaught(unittest.TestCase):
    def test_protocol_error_is_a_jev_error_without_server_text(self):
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)

        def serve():
            conn, _ = srv.accept()
            data = conn.recv(65536).decode("latin-1")
            auth = [l for l in data.split("\r\n") if l.lower().startswith("authorization")]
            conn.sendall(("GARBAGE " + (auth[0] if auth else "") + "\r\n\r\n").encode("latin-1"))
            conn.close()

        threading.Thread(target=serve, daemon=True).start()
        try:
            with Isolated(MY_KEY=KEY):
                js = {"backend": "custom", "base_url": f"http://127.0.0.1:{srv.getsockname()[1]}", "model": "j",
                      "api_key_env": "MY_KEY", "key_required": True, "timeout_s": 10}
                with self.assertRaises(fo_jev.JevError) as ctx:
                    fo_jev.ping(js)
                self.assertNotIn(KEY, str(ctx.exception))
                self.assertIsNone(ctx.exception.__cause__)
        finally:
            srv.close()


class PlainHttpOnlyToLoopback(unittest.TestCase):
    def test_remote_http_refused_before_any_send(self):
        js = {"backend": "custom", "base_url": "http://gpu-box.example:8080", "model": "j", "key_required": False}
        with self.assertRaises(fo_jev.JevError) as ctx:
            fo_jev.ask(js, "s", {}, key="k")
        self.assertIn("allow_insecure_http", str(ctx.exception))
        for url in ("ftp://x.example", "http://", "nonsense"):
            with self.assertRaises(fo_jev.JevError):
                fo_jev.check_transport({"base_url": url})

    def test_allowed_forms(self):
        for url in ("https://api.typesafe.ai", "http://127.0.0.1:8080", "http://localhost:8080"):
            fo_jev.check_transport({"base_url": url})
        fo_jev.check_transport({"base_url": "http://gpu-box.example:8080", "allow_insecure_http": True})

    def test_config_flag_reaches_settings(self):
        with Isolated():
            cfg = fo_config.deep_merge(fo_config.load(), {"jev": {"backend": "custom", "base_url": "http://h.example",
                                                                   "allow_insecure_http": True}})
            self.assertTrue(fo_config.jev_settings(cfg)["allow_insecure_http"])


class SymlinkedLogSharesTheLock(unittest.TestCase):
    def test_lock_follows_the_real_file(self):
        with Isolated() as iso:
            common = os.path.join(iso.home, "common.md")
            with open(common, "w") as f:
                f.write("# log\n")
            link = os.path.join(iso.home, "link.md")
            os.symlink(common, link)
            fo_lessons.configure(os.path.join(iso.home, "ledA"), link)
            fo_lessons.log_entry("via link", [])
            self.assertTrue(os.path.exists(os.path.join(iso.home, ".common.md.fo-lock")))
            self.assertFalse(os.path.exists(os.path.join(iso.home, ".link.md.fo-lock")))
            self.assertIn("via link", read_text(common))


if __name__ == "__main__":
    unittest.main()
