"""Config, Jev client, router and providers. Network tests use a real local HTTP server."""

from __future__ import annotations

import json
import os
import unittest

from helpers import read_json, read_text, Isolated, Server, jev_reply, run_fo

import fo_config
import fo_jev
import fo_providers
import fo_route


class ConfigTests(unittest.TestCase):
    def test_defaults_and_paths(self):
        with Isolated() as iso:
            cfg = fo_config.load()
            self.assertEqual(cfg["jev"]["backend"], "typesafe")
            self.assertIsNone(cfg["telemetry"]["enabled"])
            self.assertEqual(fo_config.config_path(), os.path.join(iso.home, ".config", "frontier-orchestration", "config.json"))
            root, log = fo_config.ledger_paths(cfg)
            self.assertEqual(root, os.path.join(iso.home, ".local", "share", "frontier-orchestration", "ledger"))
            self.assertEqual(log, os.path.join(root, "log.md"))

    def test_env_overrides(self):
        with Isolated(FO_JEV_BACKEND="openjev", FO_JEV_BASE_URL="http://127.0.0.1:9999", FO_TELEMETRY="0"):
            cfg = fo_config.load()
            js = fo_config.jev_settings(cfg)
            self.assertEqual(js["backend"], "openjev")
            self.assertEqual(js["base_url"], "http://127.0.0.1:9999")
            self.assertEqual(js["model"], "openjev-latest")
            self.assertFalse(js["key_required"])
            self.assertIs(cfg["telemetry"]["enabled"], False)

    def test_typesafe_base_url_compat_only_for_typesafe(self):
        with Isolated(TYPESAFE_BASE_URL="https://api.codiv.ai/"):
            self.assertEqual(fo_config.jev_settings(fo_config.load())["base_url"], "https://api.codiv.ai")
        with Isolated(TYPESAFE_BASE_URL="https://x.example", FO_JEV_BACKEND="codiv"):
            self.assertEqual(fo_config.jev_settings(fo_config.load())["base_url"], "https://api.codiv.ai")

    def test_vault_compat(self):
        with Isolated() as iso:
            os.environ["FO_VAULT"] = os.path.join(iso.home, "Vault")
            root, log = fo_config.ledger_paths(fo_config.load())
            self.assertEqual(root, os.path.join(iso.home, "Vault", "wiki", "learning", "frontier-orchestration"))
            self.assertEqual(log, os.path.join(iso.home, "Vault", "wiki", "log.md"))

    def test_set_rejects_unknown_key_and_bad_values(self):
        with self.assertRaises(fo_config.ConfigError):
            fo_config.set_dotted({}, "jev.backnd", "off")
        with self.assertRaises(fo_config.ConfigError):
            fo_config.set_dotted({}, "jev.backend", "nope")
        with self.assertRaises(fo_config.ConfigError):
            fo_config.set_dotted({}, "providers.codex.via", "agy")
        self.assertEqual(fo_config.set_dotted({}, "jev.backend", "off"), {"jev": {"backend": "off"}})

    def test_custom_backend_needs_url(self):
        with Isolated(FO_JEV_BACKEND="custom"):
            with self.assertRaises(fo_config.ConfigError):
                fo_config.load()

    def test_config_file_is_private(self):
        with Isolated():
            path = fo_config.write_file({"jev": {"backend": "off"}})
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)

    def test_user_roster_override_adds_and_removes(self):
        with Isolated():
            os.makedirs(fo_config.config_dir())
            with open(os.path.join(fo_config.config_dir(), "roster.json"), "w") as f:
                json.dump({"models": {"gpt-9": {"vendor": "codex", "tier": "frontier", "description": "new"},
                                      "haiku": None}}, f)
            models = fo_config.roster()["models"]
            self.assertIn("gpt-9", models)
            self.assertNotIn("haiku", models)


class JevTests(unittest.TestCase):
    def test_round_trip_headers_and_key(self):
        with Isolated(MY_KEY="secret-1"), Server(jev_reply()) as srv:
            js = {"backend": "custom", "base_url": srv.url, "model": "m1", "api_key_env": "MY_KEY",
                  "key_required": True, "long_context": True, "timeout_s": 10}
            r = fo_jev.ping(js)
            self.assertEqual(r["p_true"], 0.9)
            req = srv.requests[0]
            self.assertEqual(req["path"], "/v1/systemone")
            self.assertEqual(req["headers"]["authorization"], "Bearer secret-1")
            self.assertEqual(req["headers"]["typesafe-danger-zone-long-context"], "true")
            self.assertEqual(req["body"]["model"], "m1")

    def test_no_key_sends_no_auth_header(self):
        with Isolated(), Server(jev_reply()) as srv:
            js = {"backend": "openjev", "base_url": srv.url, "model": "openjev-latest",
                  "api_key_env": "OPENJEV_API_KEY", "key_required": False, "timeout_s": 10}
            fo_jev.ping(js)
            self.assertNotIn("authorization", srv.requests[0]["headers"])

    def test_http_error_keeps_status(self):
        with Isolated(), Server(lambda p, b, h: (401, {"detail": {"error_type": "auth"}})) as srv:
            js = {"backend": "custom", "base_url": srv.url, "model": "m", "key_required": False, "timeout_s": 10}
            with self.assertRaises(fo_jev.JevError) as ctx:
                fo_jev.ping(js)
            self.assertEqual(ctx.exception.status, 401)

    def test_missing_required_key(self):
        with Isolated():
            js = fo_config.jev_settings(fo_config.load())
            js["keychain_service"] = None  # never touch the real Keychain in tests
            with self.assertRaises(fo_jev.JevError) as ctx:
                fo_jev.api_key(js)
            self.assertIn("TYPESAFE_API_KEY", str(ctx.exception))

    def test_key_file_must_be_private(self):
        with Isolated() as iso:
            p = os.path.join(iso.home, "key")
            with open(p, "w") as f:
                f.write("k\n")
            os.chmod(p, 0o644)
            js = {"backend": "custom", "api_key_file": p}
            with self.assertRaises(fo_jev.JevError):
                fo_jev.api_key(js)
            os.chmod(p, 0o600)
            self.assertEqual(fo_jev.api_key(js), "k")


class RouteTests(unittest.TestCase):
    def roster(self):
        with Isolated():
            return fo_route.Roster(fo_config.roster())

    def test_demo_self_check_clean(self):
        self.assertEqual(fo_route.self_check(fo_route.DEMO, self.roster()), [])

    def test_independence_drops_producer_and_aliases(self):
        r = self.roster()
        attrs = {"needs_independence": "YES", "producer_vendor": "codex",
                 "candidates": ["sol", "astra", "opus", "argon", "mystery-model"]}
        allowed, problems = fo_route.independent_candidates(r, "t", attrs)
        self.assertEqual(allowed, ["opus", "gemini-4-argon"])
        self.assertTrue(any("gpt-6.1-sol" in p for p in problems))
        self.assertTrue(any("mystery-model" in p and "no known vendor" in p for p in problems))

    def test_independence_requires_producer(self):
        allowed, problems = fo_route.independent_candidates(self.roster(), "t", {"needs_independence": "YES - x"})
        self.assertEqual(allowed, [])
        self.assertIn("producer_vendor", problems[0])

    def test_disabled_vendor_dropped(self):
        allowed, _ = fo_route.independent_candidates(self.roster(), "t", {"candidates": ["opus", "flash"]},
                                                     enabled={"claude"})
        self.assertEqual(allowed, ["opus"])

    def test_gate(self):
        self.assertEqual(fo_route.gate(0.85, 0.9)[0], "act")
        self.assertEqual(fo_route.gate(0.42, 0.15)[0], "act")
        self.assertEqual(fo_route.gate(0.48, 0.89)[0], "ESCALATE")
        self.assertEqual(fo_route.gate(None, 0.9)[0], "ESCALATE")

    def test_route_through_jev_server(self):
        r = self.roster()
        with Isolated(), Server(jev_reply({"estimate_review": "gpt-6-astra"}, stakes=0.9, confidence=0.5)) as srv:
            js = {"backend": "custom", "base_url": srv.url, "model": "jev-latest", "key_required": False, "timeout_s": 10}
            out = fo_route.route(fo_route.DEMO, r, js)
            self.assertEqual(out["estimate_review"]["model"], "gpt-6-astra")
            self.assertEqual(out["estimate_review"]["decision"], "ESCALATE")
            q = srv.requests[0]["body"]["questions"]
            self.assertIsInstance(q["route_repo_sweep"]["criteria"], dict)
            self.assertEqual(set(q["stakes_repo_sweep"]["criteria"]), {"true", "false"})

    def test_same_vendor_pick_is_rejected(self):
        r = self.roster()
        spec = {"tasks": {"rev": {"needs_independence": "YES", "producer_vendor": "claude",
                                  "candidates": ["gpt-6-astra", "gemini-4-argon"]}}}
        with Isolated(), Server(jev_reply({"rev": "opus"})) as srv:
            js = {"backend": "custom", "base_url": srv.url, "model": "j", "key_required": False, "timeout_s": 10}
            self.assertEqual(fo_route.route(spec, r, js)["rev"]["decision"], "REJECT")

    def test_heuristic_without_network(self):
        out = fo_route.route(fo_route.DEMO, self.roster(), {"backend": "off"})
        self.assertEqual(out["arithmetic_audit"]["decision"], "act")
        self.assertEqual(out["estimate_review"]["decision"], "ESCALATE")
        self.assertTrue(all(d["judge"] == "heuristic" for d in out.values()))


class ProviderTests(unittest.TestCase):
    def test_nothing_installed(self):
        with Isolated():
            res = fo_providers.resolve(fo_config.load(), host="unknown")
            self.assertEqual(fo_providers.enabled_vendors(res), set())
            self.assertIn("no transport found", res["codex"]["note"])

    def test_claude_host_and_disable(self):
        with Isolated():
            cfg = fo_config.deep_merge(fo_config.load(), {"providers": {"gemini": {"enabled": False}}})
            res = fo_providers.resolve(cfg, host="claude-code")
            self.assertEqual(res["claude"]["via"], "host")
            self.assertEqual(res["gemini"]["note"], "disabled in config")

    def test_cli_found_on_path(self):
        with Isolated() as iso:
            bin_dir = os.path.join(iso.home, "bin")
            os.makedirs(bin_dir)
            for name in ("codex", "agy"):
                p = os.path.join(bin_dir, name)
                with open(p, "w") as f:
                    f.write("#!/bin/sh\n")
                os.chmod(p, 0o755)
            os.environ["PATH"] = bin_dir + ":/usr/bin:/bin"
            res = fo_providers.resolve(fo_config.load(), host="unknown")
            self.assertEqual(res["codex"]["via"], "cli")
            self.assertEqual(res["gemini"]["via"], "agy")
            self.assertIn("codex exec --sandbox read-only", res["codex"]["how"])


class CliTests(unittest.TestCase):
    def test_setup_asks_when_non_interactive(self):
        with Isolated() as iso:
            p = run_fo(iso.home, "setup")
            self.assertEqual(p.returncode, 3, p.stdout + p.stderr)
            self.assertIn('"needs"', p.stdout)

    def test_setup_then_doctor_then_route(self):
        with Isolated() as iso:
            p = run_fo(iso.home, "setup", "--jev", "off", "--providers", "claude,codex", "--telemetry", "no")
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            data = read_json(os.path.join(iso.home, ".config", "frontier-orchestration", "config.json"))
            self.assertEqual(data["jev"]["backend"], "off")
            self.assertFalse(data["providers"]["gemini"]["enabled"])
            self.assertIs(data["telemetry"]["enabled"], False)
            self.assertTrue(os.path.exists(os.path.join(iso.home, ".local", "share", "frontier-orchestration",
                                                        "ledger", "reviewer-priors.md")))
            d = run_fo(iso.home, "doctor")
            self.assertEqual(d.returncode, 0, d.stdout + d.stderr)
            r = run_fo(iso.home, "route", "--demo", "--json")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual(json.loads(r.stdout)["repo_sweep"]["judge"], "heuristic")

    def test_route_with_jev_server_via_cli(self):
        with Isolated() as iso, Server(jev_reply()) as srv:
            p = run_fo(iso.home, "route", "--demo", env_extra={"FO_JEV_BACKEND": "custom", "FO_JEV_BASE_URL": srv.url})
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            self.assertIn("estimate_review", p.stdout)

    def test_check_exit_code(self):
        with Isolated() as iso:
            self.assertEqual(run_fo(iso.home, "route", "--check").returncode, 0)

    def test_route_exit_codes_match_help(self):
        """Exit 1: the independence rule leaves no candidate (no decision). Exit 2: a REJECT decision."""
        with Isolated() as iso:
            help_text = run_fo(iso.home, "route", "--help").stdout
            flat = " ".join(help_text.split())
            self.assertIn("exit 1 when the route fails and gives no decision (for example, the independence "
                          "rule leaves no candidate)", flat)
            self.assertIn("exit 2 when it gives a REJECT decision (the judge picked a model that is not an "
                          "allowed candidate)", flat)
            self.assertNotIn("REJECTED by the independence rule", flat)

            # Exit 1, offline: the only candidate has the producer's vendor, so none is left.
            spec = os.path.join(iso.home, "only-same-vendor.json")
            with open(spec, "w", encoding="utf-8") as f:
                json.dump({"tasks": {"rev": {"needs_independence": "YES", "producer_vendor": "claude",
                                             "candidates": ["opus"]}}}, f)
            p = run_fo(iso.home, "route", spec, env_extra={"FO_JEV_BACKEND": "off"})
            self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
            self.assertIn("route failed: independence rule:", p.stderr)
            self.assertNotIn("REJECT", p.stdout)

            # Exit 2: a local Jev picks a same-vendor model that is not an allowed candidate.
            spec = os.path.join(iso.home, "judge-picks-outside.json")
            with open(spec, "w", encoding="utf-8") as f:
                json.dump({"tasks": {"rev": {"needs_independence": "YES", "producer_vendor": "claude",
                                             "candidates": ["gpt-6-astra", "gemini-4-argon"]}}}, f)
            with Server(jev_reply({"rev": "opus"})) as srv:
                p = run_fo(iso.home, "route", spec, "--json",
                           env_extra={"FO_JEV_BACKEND": "custom", "FO_JEV_BASE_URL": srv.url})
            self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
            self.assertEqual(json.loads(p.stdout)["rev"]["decision"], "REJECT")
            self.assertEqual(len(srv.requests), 1, "the exit-2 run did not ask the local Jev")

    def test_reject_label_names_the_real_reason(self):
        """A REJECT is a pick outside the allowed candidates, also on a task with no independence rule."""
        with Isolated() as iso:
            top = " ".join(run_fo(iso.home, "--help").stdout.split())
            self.assertIn("2 a route pick was REJECTED (not an allowed candidate)", top)
            self.assertNotIn("REJECTED by the independence rule", top)
            spec = os.path.join(iso.home, "no-independence.json")
            with open(spec, "w", encoding="utf-8") as f:
                json.dump({"tasks": {"fmt": {"needs_independence": "no", "candidates": ["haiku", "sonnet"]}}}, f)
            with Server(jev_reply({"fmt": "opus"})) as srv:
                p = run_fo(iso.home, "route", spec,
                           env_extra={"FO_JEV_BACKEND": "custom", "FO_JEV_BASE_URL": srv.url})
            self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
            self.assertIn("REJECTED (pick is not an allowed candidate): fmt", p.stdout)
            self.assertNotIn("independence rule", p.stdout)


if __name__ == "__main__":
    unittest.main()
