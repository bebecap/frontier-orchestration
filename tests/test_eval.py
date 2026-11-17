"""fo.py eval: the decision-engine eval harness. Fake System One servers only, no network."""

from __future__ import annotations

import json
import os
import socket
import tempfile
import unittest

from helpers import SKILL, Server, read_json, run_fo

import fo_eval
import fo_route

SANITY = read_json(os.path.join(SKILL, "evals", "sanity.json"))
ROUTING = read_json(os.path.join(SKILL, "evals", "routing.json"))
EXPECT = {it["id"]: it for it in SANITY["items"]}
CASES = {c["id"]: c for c in ROUTING["cases"]}
KEY = "evaltest-fake-key-0123456789abcdef"  # not "sk-" shaped: the privacy test flags those
HUGE = 10 ** 400  # a valid JSON integer; math.isfinite() raises OverflowError on it


def fake(mode: str = "perfect"):
    """A System One server that knows the labels. mode breaks one thing on purpose."""
    visits = {}  # routing case id -> how many times it was routed (for the flaky mode)

    def reply(path, body, headers):
        if path != "/v1/systemone":
            return 404, {"detail": "not found"}
        answers = {}
        for qid, q in body["questions"].items():
            if qid in EXPECT:
                exp = EXPECT[qid]["expect"]
                if q["type"] == "noul":
                    p = 0.9 if exp else 0.1
                    if mode == "invert_noul":
                        p = 1.0 - p
                    if mode == "bad_prob":
                        p = 1.7
                    if mode == "huge":
                        p = HUGE
                    answers[qid] = {"noul": p}
                elif q["type"] == "choice":
                    pick = "not-an-option" if mode == "bad_choice" else exp
                    answers[qid] = {"choice": pick, "confidence": HUGE if mode == "huge" else 0.9,
                                    "probabilities": {k: (0.9 if k == exp else 0.05) for k in q["criteria"]}}
                else:
                    answers[qid] = {"score": HUGE if mode == "huge" else float(exp), "confidence": 0.9}
            elif qid.startswith("route_"):
                cid = qid[len("route_"):]
                c = CASES[cid]
                pick = c["acceptable"][0]
                if mode == "route_off_criteria":
                    pick = "not-a-model"
                if mode == "flaky" and visits.get(cid):
                    others = [k for k in q["criteria"] if k != pick]
                    pick = others[0] if others else pick
                visits[cid] = visits.get(cid, 0) + 1
                answers[qid] = {"choice": pick} if mode == "no_confidence" else {"choice": pick, "confidence": 0.95}
            elif qid.startswith("stakes_"):
                c = CASES[qid[len("stakes_"):]]
                # 0.5 on unscored cases: any Brier that counted them would move off 0.01.
                high_p = 0.4999 if mode == "stakes_edge" else 0.9  # just under the floor: the gate says low
                answers[qid] = {"noul": 0.5 if c["stakes"] is None else high_p if c["stakes"] == "high" else 0.1}
        return 200, {"answers": answers, "usage": {"input_tokens": 10}}

    return reply


def write_config(home: str, jev: dict) -> None:
    d = os.path.join(home, ".config", "frontier-orchestration")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "config.json"), "w", encoding="utf-8") as f:
        json.dump({"jev": jev}, f)


def closed_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class EvalCommandTests(unittest.TestCase):
    def run_eval(self, mode, *args, env=None):
        with tempfile.TemporaryDirectory() as home, Server(fake(mode)) as srv:
            write_config(home, {"backend": "custom", "base_url": srv.url})
            r = run_fo(home, "eval", *args, env_extra={"FO_JEV_API_KEY": KEY, **(env or {})})
            return r, srv

    def test_perfect_backend_passes(self):
        r, srv = self.run_eval("perfect", "--repeat", "2", "--json")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        rep = json.loads(r.stdout)
        self.assertEqual(rep["verdict"], "pass")
        s, ro = rep["suites"]["sanity"], rep["suites"]["routing"]
        self.assertEqual((s["n"], s["accuracy"], s["errors"], s["stability"]), (24, 1.0, 0, 1.0))
        self.assertEqual((ro["n"], ro["accuracy"], ro["stakes_accuracy"], ro["stability"]), (20, 1.0, 1.0, 1.0))
        self.assertEqual(ro["decisions"]["REJECT"], 0)
        # Each scored case is off by 0.1: Brier is exactly 0.01. The two null cases are left out.
        self.assertEqual((ro["stakes_brier"], ro["stakes_unscored"]), (0.01, 2))
        self.assertEqual(set(rep["dataset_sha256"]), {"sanity", "routing"})
        # Batches of at most 8 questions; one routing task per request (2 questions).
        sizes = [len(q["body"]["questions"]) for q in srv.requests]
        self.assertEqual(sizes.count(8), 6)
        self.assertEqual(sizes.count(2), 40)
        self.assertLessEqual(max(sizes), fo_eval.BATCH)
        for q in srv.requests:
            if len(q["body"]["questions"]) == 2:
                self.assertEqual(len(q["body"]["state"]["tasks"]), 1)

    def test_human_report(self):
        r, _ = self.run_eval("perfect", "sanity")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("sanity   24 items  accuracy 1.000", r.stdout)
        self.assertIn("verdict: pass", r.stdout)

    def test_inverted_noul_fails_sanity(self):
        r, _ = self.run_eval("invert_noul", "sanity", "--json")
        self.assertEqual(r.returncode, 1, r.stderr)
        rep = json.loads(r.stdout)
        self.assertEqual(rep["verdict"], "fail")
        self.assertEqual(rep["suites"]["sanity"]["by_type"]["noul"]["correct"], 0)
        self.assertAlmostEqual(rep["suites"]["sanity"]["accuracy"], 14 / 24, places=3)

    def test_bad_probability_is_an_error(self):
        r, _ = self.run_eval("bad_prob", "sanity", "--json")
        self.assertEqual(r.returncode, 2, r.stderr)
        rep = json.loads(r.stdout)
        self.assertEqual((rep["verdict"], rep["suites"]["sanity"]["errors"]), ("error", 10))
        # A server value is never echoed. Look at the items only: a latency such as 1.7 ms is ours.
        self.assertNotIn("1.7", json.dumps(rep["suites"]["sanity"]["items"]) + r.stderr)

    def test_choice_outside_criteria_is_an_error(self):
        r, _ = self.run_eval("bad_choice", "sanity", "--json")
        self.assertEqual(r.returncode, 2, r.stderr)
        rep = json.loads(r.stdout)
        self.assertEqual(rep["suites"]["sanity"]["errors"], 8)
        self.assertNotIn("not-an-option", r.stdout)

    def test_huge_integer_is_an_error_not_a_traceback(self):
        r, _ = self.run_eval("huge", "sanity", "--json")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        rep = json.loads(r.stdout)
        s = rep["suites"]["sanity"]
        self.assertEqual((rep["verdict"], s["errors"], s["correct"]), ("error", 24, 0))
        self.assertNotIn(str(HUGE)[:40], r.stdout)

    def test_routing_pick_outside_the_criteria_is_an_error(self):
        r, _ = self.run_eval("route_off_criteria", "routing", "--json")
        self.assertEqual(r.returncode, 2, r.stderr)
        rep = json.loads(r.stdout)
        ro = rep["suites"]["routing"]
        # A malformed answer, as in sanity: an error, not a REJECT hard-check failure.
        self.assertEqual((rep["verdict"], ro["errors"], ro["decisions"]["REJECT"], ro["hard_check_failures"]),
                         ("error", 20, 0, []))
        self.assertTrue(all(x["errors"] == ["the judge picked a model that is not one of the criteria"]
                            for x in ro["items"]))
        self.assertNotIn("not-a-model", r.stdout)

    def test_routing_missing_confidence_is_an_error(self):
        r, _ = self.run_eval("no_confidence", "routing", "--json")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        rep = json.loads(r.stdout)
        ro = rep["suites"]["routing"]
        self.assertEqual((rep["verdict"], ro["errors"], ro["correct"]), ("error", 20, 0))
        self.assertTrue(all("confidence" in x["errors"][0] for x in ro["items"]))

    def test_routing_flaky_judge_lowers_stability(self):
        r, _ = self.run_eval("flaky", "routing", "--repeat", "2", "--json")
        rep = json.loads(r.stdout)
        ro = rep["suites"]["routing"]
        roster = fo_eval.shipped_roster()
        single = sum(1 for c in ROUTING["cases"]
                     if len(fo_route.independent_candidates(roster, c["id"], c["attrs"], {"claude", "codex", "gemini"})[0]) == 1)
        self.assertLess(ro["stability"], 1.0)
        self.assertEqual(ro["stability"], round(single / 20, 3))
        self.assertEqual(ro["errors"], 0)

    def test_routing_env_naming_another_engine_sends_nothing_there(self):
        # FO_JEV_BACKEND=custom names another engine than --backend openjev: none of the FO_JEV_*
        # values apply, so the other server gets nothing; the stored openjev settings stay.
        with tempfile.TemporaryDirectory() as home, Server(fake()) as stored, Server(fake()) as other:
            write_config(home, {"backend": "openjev", "base_url": stored.url})
            r = run_fo(home, "eval", "routing", "--backend", "openjev", "--json",
                       env_extra={"FO_JEV_BACKEND": "custom", "FO_JEV_BASE_URL": other.url})
            self.assertEqual(other.requests, [])
            self.assertTrue(stored.requests)
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)

    def test_backend_override_ignores_a_broken_stored_engine(self):
        # Each stored engine fails validation on its own; neither may block an openjev run.
        for broken in ({"backend": "custom", "base_url": "http://127.0.0.1:1", "timeout_s": -1},
                       {"backend": "bogus", "base_url": "http://127.0.0.1:1"}):
            with tempfile.TemporaryDirectory() as home, Server(fake()) as srv:
                write_config(home, broken)
                r = run_fo(home, "eval", "sanity", "--backend", "openjev", "--json",
                           env_extra={"FO_JEV_BACKEND": "openjev", "FO_JEV_BASE_URL": srv.url})
                self.assertTrue(srv.requests, broken)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(json.loads(r.stdout)["backend"], "openjev")

    def test_same_backend_keeps_stored_settings_when_env_names_another(self):
        with tempfile.TemporaryDirectory() as home, Server(fake()) as srv:
            write_config(home, {"backend": "custom", "base_url": srv.url, "model": "jev-test-1"})
            r = run_fo(home, "eval", "sanity", "--backend", "custom", "--json",
                       env_extra={"FO_JEV_API_KEY": KEY, "FO_JEV_BACKEND": "openjev",
                                  "FO_JEV_MODEL": "env-model"})
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue(srv.requests)
            # The stored block stays; FO_JEV_* values for the other engine do not apply.
            self.assertTrue(all(q["body"]["model"] == "jev-test-1" for q in srv.requests))

    def test_env_for_the_same_engine_applies(self):
        with tempfile.TemporaryDirectory() as home, Server(fake()) as old, Server(fake()) as new:
            write_config(home, {"backend": "custom", "base_url": old.url, "model": "jev-old"})
            r = run_fo(home, "eval", "sanity", "--backend", "openjev", "--json",
                       env_extra={"FO_JEV_BACKEND": "openjev", "FO_JEV_BASE_URL": new.url})
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(old.requests, [])
            self.assertTrue(all(q["body"]["model"] == "openjev-latest" for q in new.requests))

    def test_env_url_for_the_stored_engine_never_gets_another_engines_key(self):
        # FO_JEV_BASE_URL was set for the stored custom engine (FO_JEV_BACKEND unset). Evaluating
        # typesafe must not send the TypeSafe key there.
        with tempfile.TemporaryDirectory() as home, Server(fake()) as old, Server(fake()) as ts:
            write_config(home, {"backend": "custom", "base_url": old.url})
            r = run_fo(home, "eval", "sanity", "--backend", "typesafe", "--json",
                       env_extra={"FO_JEV_BASE_URL": old.url, "TYPESAFE_API_KEY": KEY, "TYPESAFE_BASE_URL": ts.url})
            self.assertEqual(old.requests, [], "another engine's URL received the TypeSafe key")
            self.assertTrue(ts.requests)
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)

    def test_nothing_measured_exits_2(self):
        with tempfile.TemporaryDirectory() as home:
            r = run_fo(home, "eval", "sanity", "--backend", "off")
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn("eval failed: nothing measured", r.stderr)
        self.assertIn("verdict: error (nothing measured", r.stdout)
        self.assertNotIn("Traceback", r.stderr)

    def test_all_on_off_passes_on_routing_alone(self):
        with tempfile.TemporaryDirectory() as home:
            r = run_fo(home, "eval", "all", "--backend", "off", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        rep = json.loads(r.stdout)
        self.assertEqual(rep["verdict"], "pass")
        self.assertTrue(rep["suites"]["sanity"]["skipped"])
        self.assertEqual(rep["suites"]["routing"]["errors"], 0)

    def test_out_write_failure_exits_2_after_the_report(self):
        with tempfile.TemporaryDirectory() as home:
            out = os.path.join(home, "no-such-dir", "report.json")
            r = run_fo(home, "eval", "routing", "--backend", "off", "--out", out)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn(f"eval failed: cannot write --out {out}", r.stderr)
        self.assertIn("verdict: pass", r.stdout)
        self.assertNotIn("Traceback", r.stderr)

    def test_unreachable_backend_exits_2_without_traceback(self):
        with tempfile.TemporaryDirectory() as home:
            write_config(home, {"backend": "custom", "base_url": f"http://127.0.0.1:{closed_port()}"})
            r = run_fo(home, "eval", env_extra={"FO_JEV_API_KEY": KEY})
        self.assertEqual(r.returncode, 2)
        self.assertIn("eval failed: cannot reach", r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        self.assertNotIn(KEY, r.stderr + r.stdout)

    def test_backend_override_never_carries_the_stored_settings(self):
        with tempfile.TemporaryDirectory() as home, Server(fake()) as old, Server(fake()) as new:
            write_config(home, {"backend": "custom", "base_url": old.url, "model": "jev-old",
                                "api_key_env": "OLD_JEV_KEY"})
            r = run_fo(home, "eval", "sanity", "--backend", "openjev", "--json",
                       env_extra={"FO_JEV_BACKEND": "openjev", "FO_JEV_BASE_URL": new.url, "OLD_JEV_KEY": "old-engine-key",
                                  "OPENJEV_API_KEY": "k-openjev-test"})
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(old.requests, [])
            self.assertTrue(new.requests)
            rep = json.loads(r.stdout)
            self.assertEqual((rep["backend"], rep["model"]), ("openjev", "openjev-latest"))
            # The stored engine's model and key never travel to the other engine.
            self.assertTrue(all(q["body"]["model"] == "openjev-latest" for q in new.requests))
            self.assertTrue(all(q["headers"].get("authorization") == "Bearer k-openjev-test" for q in new.requests))

    def test_backend_override_without_a_url_never_uses_the_stored_url(self):
        # custom needs a URL of its own: the stored openjev URL must not fill the gap.
        with tempfile.TemporaryDirectory() as home, Server(fake()) as old:
            write_config(home, {"backend": "openjev", "base_url": old.url})
            r = run_fo(home, "eval", "sanity", "--backend", "custom", env_extra={"FO_JEV_API_KEY": KEY})
            self.assertEqual(old.requests, [])
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn("config error: jev.backend custom needs jev.base_url", r.stderr)
        self.assertNotIn("Traceback", r.stderr)

    def test_same_backend_keeps_stored_settings(self):
        with tempfile.TemporaryDirectory() as home, Server(fake()) as srv:
            write_config(home, {"backend": "custom", "base_url": srv.url, "model": "jev-test-1"})
            r = run_fo(home, "eval", "sanity", "--backend", "custom", "--json", env_extra={"FO_JEV_API_KEY": KEY})
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue(srv.requests)
            self.assertEqual(srv.requests[0]["body"]["model"], "jev-test-1")

    def test_off_backend_runs_routing_heuristic_and_skips_sanity(self):
        with tempfile.TemporaryDirectory() as home, Server(fake()) as srv:
            write_config(home, {"backend": "custom", "base_url": srv.url})
            r = run_fo(home, "eval", "--backend", "off", "--json")
            self.assertEqual(srv.requests, [])
        self.assertEqual(r.returncode, 0, r.stderr)
        rep = json.loads(r.stdout)
        self.assertTrue(rep["suites"]["sanity"]["skipped"])
        self.assertIn("no System One", rep["suites"]["sanity"]["note"])
        ro = rep["suites"]["routing"]
        self.assertEqual((rep["model"], ro["n"], ro["decisions"]["REJECT"], ro["hard_check_failures"]),
                         ("heuristic", 20, 0, []))
        self.assertTrue(all(x["runs"][0]["confidence"] is None for x in ro["items"]))

    def test_routing_threshold_miss_exits_1(self):
        with tempfile.TemporaryDirectory() as home:
            r = run_fo(home, "eval", "routing", "--backend", "off", "--min-routing", "0.99")
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn("routing pick accuracy", r.stdout)

    def test_json_report_has_no_key_material(self):
        with tempfile.TemporaryDirectory() as home, Server(fake()) as srv:
            write_config(home, {"backend": "custom", "base_url": srv.url + "/"})
            out = os.path.join(home, "report.json")
            r = run_fo(home, "eval", "--json", "--out", out, env_extra={"FO_JEV_API_KEY": KEY})
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue(all(q["headers"].get("authorization") == f"Bearer {KEY}" for q in srv.requests))
            with open(out, encoding="utf-8") as f:
                saved = f.read()
            self.assertEqual(os.stat(out).st_mode & 0o777, 0o600)
            for text in (r.stdout, r.stderr, saved):
                self.assertNotIn(KEY, text)
                self.assertNotIn("FO_JEV_API_KEY", text)
                self.assertNotIn("http://", text)
            rep = json.loads(saved)
            self.assertEqual(rep["host"], srv.url.split("//", 1)[1])

    def test_repeat_takes_ascii_digits_only(self):
        # "\u0662" is ARABIC-INDIC DIGIT TWO: str.isdigit() accepts it and int() reads it as 2.
        with tempfile.TemporaryDirectory() as home:
            for v in ("\u0662", "\u00b2", "\uff12"):
                r = run_fo(home, "eval", "routing", "--backend", "off", "--repeat", v)
                self.assertEqual(r.returncode, 2, v)
                self.assertIn("--repeat must be a whole number from 1 to 10", r.stderr)
                self.assertNotIn("Traceback", r.stderr)

    def test_bad_arguments(self):
        with tempfile.TemporaryDirectory() as home:
            # "\u0662" is ARABIC-INDIC DIGIT TWO: str.isdigit() accepts it, int() reads it as 2.
            for args in (["--repeat", "0"], ["--repeat", "11"], ["--repeat", "\u0662"], ["--repeat", "\u00b2"],
                         ["--repeat", " 2"], ["--backend", "nope"], ["--min-sanity", "2"], ["bogus"], ["--out"]):
                r = run_fo(home, "eval", *args)
                self.assertEqual(r.returncode, 2, args)
                self.assertNotIn("Traceback", r.stderr)


class DatasetTests(unittest.TestCase):
    def test_shipped_datasets_are_valid(self):
        roster = fo_eval.shipped_roster()
        self.assertEqual(fo_eval.validate_sanity(SANITY), [])
        self.assertEqual(fo_eval.validate_routing(ROUTING, roster), [])

    def test_dataset_shape(self):
        kinds = [it["question"]["type"] for it in SANITY["items"]]
        self.assertEqual((len(kinds), kinds.count("noul"), kinds.count("choice"), kinds.count("score")), (24, 10, 8, 6))
        self.assertEqual(len(ROUTING["cases"]), 20)
        ids = [it["id"] for it in SANITY["items"]] + [c["id"] for c in ROUTING["cases"]]
        self.assertEqual(len(ids), len(set(ids)))
        attr_keys = {"what", "context_volume", "output_kind", "needs_independence", "verifiable_by_code",
                     "cost_sensitivity", "needs_long_context", "candidates"}
        roster = fo_eval.shipped_roster()
        for c in ROUTING["cases"]:
            self.assertTrue(attr_keys <= set(c["attrs"]), c["id"])
            self.assertTrue(set(c["attrs"]) <= attr_keys | {"producer_vendor"}, c["id"])
            for m in c["acceptable"]:
                if fo_route.needs_independence(c["attrs"]):
                    self.assertNotEqual(roster.vendor_of(m), c["attrs"]["producer_vendor"], c["id"])
                    self.assertNotEqual(roster.vendor_of(m), "jev", c["id"])
        # Every producer vendor and every label kind is covered.
        producers = {c["attrs"].get("producer_vendor") for c in ROUTING["cases"]} - {None}
        self.assertEqual(producers, {"claude", "codex", "gemini"})
        self.assertTrue(any(c["acceptable"] == ["jev"] for c in ROUTING["cases"]))
        self.assertEqual({c["stakes"] for c in ROUTING["cases"]}, {"high", "low", None})
        self.assertEqual(sorted(c["id"] for c in ROUTING["cases"] if c["stakes"] is None),
                         ["duplicate_record_check", "security_ticket_flag"])

    def test_validators_catch_bad_items(self):
        roster = fo_eval.shipped_roster()
        bad = {"items": [
            {"id": "a", "state": 1, "question": {"type": "noul", "instructions": "x"}, "expect": "yes"},
            {"id": "a", "state": 1, "question": {"type": "choice", "instructions": "x", "criteria": {"p": "", "q": ""}}, "expect": "r"},
            {"id": "s", "state": 1, "question": {"type": "score", "instructions": "x", "criteria": ["l0", "l1"]}, "expect": 2},
        ]}
        problems = fo_eval.validate_sanity(bad)
        self.assertEqual(len(problems), 4, problems)
        case = {"id": "r", "attrs": {"what": "x", "needs_independence": "yes", "producer_vendor": "claude",
                                     "candidates": ["opus", "gpt-6.1-sol", "gpt-99"]},
                "acceptable": ["opus", "jev"], "stakes": "medium"}
        problems = fo_eval.validate_routing({"cases": [case]}, roster)
        joined = "; ".join(problems)
        for want in ("gpt-99 is not a roster id", "subset of candidates", "stakes must be", "breaks the independence rule"):
            self.assertIn(want, joined)

    def test_stakes_null_is_valid_and_missing_stakes_is_not(self):
        roster = fo_eval.shipped_roster()
        base = {"id": "r", "attrs": {"what": "x", "candidates": ["haiku", "sonnet"]}, "acceptable": ["haiku"]}
        self.assertEqual(fo_eval.validate_routing({"cases": [dict(base, stakes=None)]}, roster), [])
        self.assertIn("r: stakes must be high, low or null", fo_eval.validate_routing({"cases": [base]}, roster))

    def test_check_decision_hard_checks(self):
        # route() drops producer-vendor candidates before it asks, so the hard check is called directly.
        roster = fo_eval.shipped_roster()
        case = CASES["unit_test_coverage_check"]
        self.assertEqual(case["attrs"]["producer_vendor"], "claude")
        allowed, _ = fo_route.independent_candidates(roster, case["id"], case["attrs"], {"claude", "codex", "gemini"})
        producer_pick = {"model": "opus", "decision": "act", "why": "x"}
        err, hard = fo_eval.check_decision(case, producer_pick, roster, allowed)
        self.assertIsNone(err)
        self.assertEqual(hard, ["unit_test_coverage_check: pick opus is the producer's vendor claude"])
        # A REJECT of an allowed pick is still a hard-check failure.
        err, hard = fo_eval.check_decision(case, {"model": allowed[0], "decision": "REJECT", "why": "y"}, roster, allowed)
        self.assertIsNone(err)
        self.assertEqual(hard, ["unit_test_coverage_check: REJECT (y)"])
        # A pick outside the criteria is a malformed answer: an error, no hard check.
        for model in ("(not a roster id)", "opus"):
            err, hard = fo_eval.check_decision(case, {"model": model, "decision": "REJECT", "why": "z"}, roster, allowed)
            self.assertEqual((err, hard), ("the judge picked a model that is not one of the criteria", []))
        self.assertEqual(fo_eval.check_decision(case, {"model": allowed[0], "decision": "act"}, roster, allowed),
                         (None, []))

    def test_answer_checks(self):
        noul = {"type": "noul"}
        choice = {"type": "choice", "criteria": {"a": "", "b": ""}}
        score = {"type": "score", "criteria": ["0", "1", "2"]}
        self.assertEqual(fo_eval.check_answer(noul, {"noul": 0.2}), (0.2, None))
        for bad in ({"noul": 1.7}, {"noul": float("nan")}, {"noul": True}, {"noul": "0.5"}, None):
            self.assertIsNone(fo_eval.check_answer(noul, bad)[0])
        self.assertIsNone(fo_eval.check_answer(choice, {"choice": "c"})[0])
        self.assertIsNone(fo_eval.check_answer(choice, {"choice": "a", "confidence": 2})[0])
        self.assertIsNone(fo_eval.check_answer(choice, {"choice": "a", "probabilities": {"z": 0.1}})[0])
        self.assertEqual(fo_eval.check_answer(score, {"score": 1.5}), (1.5, None))
        self.assertIsNone(fo_eval.check_answer(score, {"score": 2.5})[0])
        for bad in ({"noul": HUGE}, {"noul": -HUGE}, {"noul": 0.5, "confidence": HUGE},
                    {"noul": 0.5, "probabilities": {"true": HUGE}}):
            self.assertIsNone(fo_eval.check_answer(noul, bad)[0])
        self.assertIsNone(fo_eval.check_answer(score, {"score": HUGE})[0])
        self.assertIs(fo_eval._prob(HUGE), False)
        self.assertEqual(fo_eval.judged("score", 1.5), 2)
        self.assertEqual(fo_eval.judged("score", 0.49), 0)
        self.assertIs(fo_eval.judged("noul", 0.5), True)


class ThresholdIsExact(unittest.TestCase):
    def test_rounded_accuracy_never_passes_a_missed_threshold(self):
        # 22/24 = 0.91666...: the report shows 0.917, but --min-sanity 0.917 is missed.
        suite = {"n": 24, "runs": 1, "correct": 22, "accuracy": 0.917, "errors": 0}
        routing = {"n": 20, "runs": 8, "correct": 159, "accuracy": 0.994, "errors": 0, "hard_check_failures": []}
        v, why = fo_eval.verdict({"suites": {"sanity": suite, "routing": routing}},
                                 {"min_sanity": 0.917, "min_routing": 0.994})
        self.assertNotEqual(v, "pass", why)
        self.assertEqual(sum("below" in w for w in why), 2, why)


class StakesScoredOnTheExactValue(unittest.TestCase):
    def test_just_under_the_floor_is_low_even_if_it_rounds_to_0_5(self):
        with tempfile.TemporaryDirectory() as home, Server(fake("stakes_edge")) as srv:
            write_config(home, {"backend": "custom", "base_url": srv.url})
            r = run_fo(home, "eval", "routing", "--json", env_extra={"FO_JEV_API_KEY": KEY})
        rep = json.loads(r.stdout)["suites"]["routing"]
        high = sum(1 for c in fo_eval.load_dataset("routing")[0]["cases"] if c["stakes"] == "high")
        scored = sum(1 for c in fo_eval.load_dataset("routing")[0]["cases"] if c["stakes"] is not None)
        self.assertAlmostEqual(rep["stakes_accuracy"], round((scored - high) / scored, 3))


class ReportIsPrivate(unittest.TestCase):
    def test_an_existing_report_file_becomes_0600(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "report.json")
            with open(path, "w") as f:
                f.write("old")
            os.chmod(path, 0o644)
            fo_eval.write_private(path, "{}")
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
