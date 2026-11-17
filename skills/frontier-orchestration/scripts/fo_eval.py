"""eval: measure the decision engine (Jev, OpenJev or the local heuristic) on fixed datasets.

    fo.py eval [sanity|routing|all] [--backend off|typesafe|openjev|codiv|custom] [--repeat N]
               [--json] [--out PATH] [--min-sanity 0.9] [--min-routing X]

Suites (in ../evals/):
    sanity   System One answers on questions with one clear answer (noul, choice, score).
    routing  fo_route.route() on labeled tasks: pick accuracy, stakes accuracy, Brier score,
             decision counts, and two hard checks (no REJECT; independence never broken).
             A case with stakes null is not scored for stakes (counted as stakes_unscored).

Exit codes: 0 every hard check passed and every threshold was met; 1 a threshold or a hard
check failed; 2 errors (a malformed answer, a config error, an unreachable backend, a usage
error, a report that cannot be written, or nothing measured because every suite was skipped).

No telemetry event is sent for eval. The report never holds a key or a full URL, only the host.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sys
import time
import urllib.parse

import fo_config
import fo_jev
import fo_route

EVAL_DIR = os.path.join(fo_config.SKILL_DIR, "evals")
SUITES = ("sanity", "routing")
BACKENDS = tuple(fo_config.JEV_BACKENDS) + ("custom",)
BATCH = 8          # at most this many questions in one System One request
MAX_REPEAT = 10
ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")
USAGE = ("usage: fo.py eval [sanity|routing|all] [--backend off|typesafe|openjev|codiv|custom] "
         "[--repeat N] [--json] [--out PATH] [--min-sanity 0.9] [--min-routing X]")
NOTHING_MEASURED = "nothing measured: every selected suite was skipped"
STAKES_LABELS = ("high", "low", None)  # None: the case is not scored for stakes


class EvalError(Exception):
    pass


# ---------- datasets ----------

def dataset_path(suite: str) -> str:
    return os.path.join(EVAL_DIR, f"{suite}.json")


def load_dataset(suite: str) -> tuple[dict, str]:
    """(dataset, sha256 of the file bytes). The hash pins which labels a result was measured on."""
    with open(dataset_path(suite), "rb") as f:
        raw = f.read()
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


def validate_sanity(data: dict) -> list:
    problems, seen = [], set()
    for it in data.get("items") or []:
        iid = it.get("id")
        if not isinstance(iid, str) or not ID_RE.match(iid):
            problems.append(f"bad item id {iid!r}")
            continue
        if iid in seen:
            problems.append(f"{iid}: duplicate id")
        seen.add(iid)
        q = it.get("question") or {}
        kind, crit, exp = q.get("type"), q.get("criteria"), it.get("expect")
        if not isinstance(q.get("instructions"), str) or not q["instructions"].strip():
            problems.append(f"{iid}: instructions must be a non-empty string")
        if "state" not in it:
            problems.append(f"{iid}: no state")
        if kind == "noul":
            if not isinstance(exp, bool):
                problems.append(f"{iid}: noul expect must be true or false")
            if crit is not None and (not isinstance(crit, dict) or set(crit) != {"true", "false"}):
                problems.append(f"{iid}: noul criteria must be {{true, false}}")
        elif kind == "choice":
            if not isinstance(crit, dict) or len(crit) < 2:
                problems.append(f"{iid}: choice criteria must be a map with 2 or more options")
            elif exp not in crit:
                problems.append(f"{iid}: choice expect is not an option")
        elif kind == "score":
            if not isinstance(crit, list) or not 2 <= len(crit) <= 10:
                problems.append(f"{iid}: score criteria must be a list of 2 to 10 levels")
            elif isinstance(exp, bool) or not isinstance(exp, int) or not 0 <= exp < len(crit):
                problems.append(f"{iid}: score expect must be a 0-based level")
        else:
            problems.append(f"{iid}: unknown question type {kind!r}")
    if not seen:
        problems.append("no items")
    return problems


def validate_routing(data: dict, roster: fo_route.Roster) -> list:
    problems, seen = [], set()
    for c in data.get("cases") or []:
        cid = c.get("id")
        if not isinstance(cid, str) or not ID_RE.match(cid):
            problems.append(f"bad case id {cid!r}")
            continue
        if cid in seen:
            problems.append(f"{cid}: duplicate id")
        seen.add(cid)
        attrs = c.get("attrs") or {}
        cands = attrs.get("candidates") or []
        if not cands:
            problems.append(f"{cid}: no candidates")
        for m in cands + list(c.get("acceptable") or []):
            if m not in roster.models:
                problems.append(f"{cid}: {m} is not a roster id")
        if not c.get("acceptable") or not set(c["acceptable"]) <= set(cands):
            problems.append(f"{cid}: acceptable must be a non-empty subset of candidates")
        if "stakes" not in c or c["stakes"] not in STAKES_LABELS:
            problems.append(f"{cid}: stakes must be high, low or null")
        try:
            allowed, _ = fo_route.independent_candidates(roster, cid, attrs, set(fo_config.VENDORS))
        except fo_route.RouteError as e:
            problems.append(f"{cid}: {e}")
            continue
        if fo_route.needs_independence(attrs):
            producer = str(attrs.get("producer_vendor", "")).lower()
            for m in c.get("acceptable") or []:
                if roster.vendor_of(m) in (producer, "jev"):
                    problems.append(f"{cid}: acceptable {m} breaks the independence rule")
        if not set(c.get("acceptable") or []) & set(allowed):
            problems.append(f"{cid}: no acceptable model survives the independence rule")
    if not seen:
        problems.append("no cases")
    return problems


def shipped_roster() -> fo_route.Roster:
    # The shipped roster, not the user's override: results stay comparable across machines.
    with open(os.path.join(fo_config.DATA_DIR, "roster.json"), encoding="utf-8") as f:
        return fo_route.Roster(json.load(f))


# ---------- settings ----------

def eval_settings(backend: str | None) -> dict:
    """Jev settings for the run. Same rule as setup: another engine never inherits stored settings.

    With --backend: start from the stored file with its jev block replaced. The stored block stays
    only when it is for the same engine; otherwise only {"backend": backend}. FO_JEV_* values
    apply only when FO_JEV_BACKEND names this engine, or when it is unset and the stored engine
    is this engine: a FO_JEV_BASE_URL set for the stored engine must never receive another
    engine's key. The stored engine is not validated on its own, so a broken stored engine never
    blocks the evaluation of another one."""
    data = fo_config.read_file()
    if backend is None:
        return fo_config.jev_settings(fo_config.effective(data))
    stored_jev = data.get("jev") if isinstance(data.get("jev"), dict) else {}
    stored = stored_jev.get("backend") or fo_config.DEFAULTS["jev"]["backend"]
    fresh = dict(data)
    fresh["jev"] = dict(stored_jev, backend=backend) if stored == backend else {"backend": backend}
    env_engine = fo_config.env_overrides().get("jev", {}).get("backend")
    with_env = env_engine == backend or (env_engine is None and stored == backend)
    return fo_config.jev_settings(fo_config.effective(fresh, with_env=with_env))


def host_of(settings: dict) -> str:
    """Host and port only: a URL can carry credentials or a path that is nobody's business."""
    u = urllib.parse.urlsplit(settings.get("base_url") or "")
    if not u.hostname:
        return ""
    try:
        port = u.port
    except ValueError:
        port = None
    return f"{u.hostname}:{port}" if port else u.hostname


# ---------- answer checks ----------

def _prob(v) -> bool:
    # The range check comes first: math.isfinite() raises OverflowError on a huge JSON integer.
    return not isinstance(v, bool) and isinstance(v, (int, float)) and 0.0 <= v <= 1.0 and math.isfinite(v)


def check_answer(q: dict, ans) -> tuple:
    """(value, None) for a well-formed answer, else (None, local error text). Never echoes server text."""
    try:
        return _check_answer(q, ans)
    except OverflowError:
        return None, "a number in the answer is out of range"


def _check_answer(q: dict, ans) -> tuple:
    if not isinstance(ans, dict):
        return None, "no answer object"
    kind = q["type"]
    if "confidence" in ans and not _prob(ans["confidence"]):
        return None, "confidence is not a probability in [0, 1]"
    probs = ans.get("probabilities")
    if probs is not None:
        if not isinstance(probs, dict) or not all(_prob(v) for v in probs.values()):
            return None, "probabilities are not all in [0, 1]"
    if kind == "noul":
        if not _prob(ans.get("noul")):
            return None, "noul is not a probability in [0, 1]"
        return float(ans["noul"]), None
    if kind == "choice":
        c = ans.get("choice")
        if not isinstance(c, str) or c not in q["criteria"]:
            return None, "choice is not one of the criteria"
        if probs is not None and not set(probs) <= set(q["criteria"]):
            return None, "probabilities name an option that is not in the criteria"
        return c, None
    top = len(q["criteria"]) - 1
    s = ans.get("score")
    if isinstance(s, bool) or not isinstance(s, (int, float)) or not 0 <= s <= top or not math.isfinite(s):
        return None, f"score is not a number in [0, {top}]"
    return float(s), None


def judged(kind: str, value):
    """The discrete answer that is compared with expect (and across repeats)."""
    if kind == "noul":
        return value >= 0.5
    if kind == "score":
        return int(math.floor(value + 0.5))  # round half up: Python's round() goes to even
    return value


# ---------- stats ----------

def percentile(xs: list, p: float):
    if not xs:
        return None
    s = sorted(xs)
    return s[max(0, math.ceil(p / 100 * len(s)) - 1)]


def latency(ms: list) -> dict:
    return {"requests": len(ms), "p50_ms": percentile(ms, 50), "p95_ms": percentile(ms, 95)}


def stability(per_item_answers: list, repeat: int):
    if repeat < 2 or not per_item_answers:
        return None
    same = sum(1 for runs in per_item_answers
               if len(runs) == repeat and None not in runs and len({json.dumps(r) for r in runs}) == 1)
    return round(same / len(per_item_answers), 3)


# ---------- suites ----------

def run_sanity(settings: dict, data: dict, repeat: int) -> dict:
    items = data["items"]
    key = fo_jev.api_key(settings)  # once: a Keychain lookup per batch is slow
    recs = {it["id"]: {"id": it["id"], "type": it["question"]["type"], "expect": it["expect"],
                       "answers": [], "correct": [], "errors": []} for it in items}
    ms = []
    for _ in range(repeat):
        for start in range(0, len(items), BATCH):
            batch = items[start:start + BATCH]
            # Question ids never reach the model, so each question names its item's state key.
            state = {it["id"]: it["state"] for it in batch}
            questions = {}
            for it in batch:
                q = dict(it["question"])
                q["instructions"] = (f"Look only at `{it['id']}` in the state; the fields named below are "
                                     f"inside it. {q['instructions']}")
                questions[it["id"]] = q
            t0 = time.monotonic()
            answers = fo_jev.ask(settings, state, questions, key=key)["answers"]
            ms.append(round((time.monotonic() - t0) * 1000, 1))
            for it in batch:
                r = recs[it["id"]]
                value, problem = (check_answer(it["question"], answers.get(it["id"]))
                                  if isinstance(answers, dict) else (None, "'answers' is not an object"))
                if problem:
                    r["answers"].append(None)
                    r["correct"].append(False)
                    r["errors"].append(problem)
                    continue
                got = judged(r["type"], value)
                r["answers"].append(got)
                r["correct"].append(got == it["expect"])
                if r["type"] != "choice":
                    r.setdefault("raw", []).append(round(value, 3))
    out = list(recs.values())
    n, total = len(out), len(out) * repeat
    correct = sum(sum(r["correct"]) for r in out)
    errors = sum(len(r["errors"]) for r in out)
    by_type = {}
    for r in out:
        b = by_type.setdefault(r["type"], {"n": 0, "correct": 0})
        b["n"] += repeat
        b["correct"] += sum(r["correct"])
    return {"n": n, "runs": repeat, "correct": correct, "accuracy": round(correct / total, 3),
            "errors": errors, "by_type": by_type,
            "stability": stability([r["answers"] for r in out], repeat),
            "latency": latency(ms), "items": out}


def check_decision(case: dict, d: dict, roster: fo_route.Roster, allowed: list) -> tuple:
    """(error, hard check failures) for one route decision.

    A pick that is not one of the criteria the judge was given is a malformed answer: an error,
    as in sanity, not a REJECT. Any other REJECT, and a pick from the producer's vendor where
    independence is required, fail a hard check."""
    cid, attrs = case["id"], case["attrs"]
    if d.get("decision") == "REJECT" and d.get("model") not in allowed:
        return "the judge picked a model that is not one of the criteria", []
    hard = []
    if d.get("decision") == "REJECT":
        hard.append(f"{cid}: REJECT ({d.get('why')})")
    if fo_route.needs_independence(attrs):
        producer = str(attrs.get("producer_vendor", "")).lower()
        if roster.vendor_of(d.get("model") or "") == producer:
            hard.append(f"{cid}: pick {d.get('model')} is the producer's vendor {producer}")
    return None, hard


def run_routing(settings: dict, data: dict, roster: fo_route.Roster, repeat: int) -> dict:
    cases = data["cases"]
    enabled = set(fo_config.VENDORS)
    recs = {c["id"]: {"id": c["id"], "acceptable": c["acceptable"], "stakes_label": c["stakes"],
                      "runs": [], "correct": [], "stakes_correct": [], "errors": []} for c in cases}
    ms, brier, counts, hard = [], [], {"act": 0, "ESCALATE": 0, "REJECT": 0}, []

    def failed(r: dict, c: dict, problem: str) -> None:
        r["runs"].append(None)
        r["correct"].append(False)
        if c["stakes"] is not None:
            r["stakes_correct"].append(False)
        r["errors"].append(problem)

    for _ in range(repeat):
        for c in cases:
            r = recs[c["id"]]
            allowed, _ = fo_route.independent_candidates(roster, c["id"], c["attrs"], enabled)
            # One task per spec, so no task's attributes can steer another's pick.
            spec = {"purpose": data.get("purpose", ""), "policy": data.get("policy", ""),
                    "tasks": {c["id"]: c["attrs"]}}
            t0 = time.monotonic()
            try:
                d = fo_route.route(spec, roster, settings, enabled=enabled)[c["id"]]
            except fo_route.RouteError as e:
                ms.append(round((time.monotonic() - t0) * 1000, 1))
                failed(r, c, str(e))
                continue
            ms.append(round((time.monotonic() - t0) * 1000, 1))
            problem, failures = check_decision(c, d, roster, allowed)
            if problem:
                failed(r, c, problem)
                continue
            hard += failures
            r["runs"].append({k: d.get(k) for k in ("model", "confidence", "stakes", "decision")})
            r["correct"].append(d["model"] in c["acceptable"])
            counts[d["decision"]] = counts.get(d["decision"], 0) + 1
            if c["stakes"] is not None:
                high = c["stakes"] == "high"
                p = d.get("stakes_exact", d["stakes"])  # the value the gate used, not the rounded one
                r["stakes_correct"].append((p >= fo_route.STAKES_FLOOR) == high)
                brier.append((p - (1.0 if high else 0.0)) ** 2)
    out = list(recs.values())
    n, total = len(out), len(out) * repeat
    correct = sum(sum(r["correct"]) for r in out)
    scored = [r for r in out if r["stakes_label"] is not None]
    stakes_ok = sum(sum(r["stakes_correct"]) for r in scored)
    return {"n": n, "runs": repeat, "correct": correct, "accuracy": round(correct / total, 3),
            "stakes_accuracy": round(stakes_ok / (len(scored) * repeat), 3) if scored else None,
            "stakes_brier": round(sum(brier) / len(brier), 4) if brier else None,
            "stakes_unscored": n - len(scored),
            "decisions": counts, "errors": sum(len(r["errors"]) for r in out),
            "hard_check_failures": hard,
            "stability": stability([[x["model"] if x else None for x in r["runs"]] for r in out], repeat),
            "latency": latency(ms), "items": out}


# ---------- command ----------

def usage_error(msg: str):
    """A usage error exits 2, like every other error of this command."""
    print(msg, file=sys.stderr)
    raise SystemExit(2)


def parse(args: list) -> dict:
    opts = {"suite": "all", "backend": None, "repeat": 1, "json": False, "out": None,
            "min_sanity": 0.9, "min_routing": None}
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--json":
            opts["json"] = True
        elif a in ("--backend", "--repeat", "--out", "--min-sanity", "--min-routing"):
            if i + 1 >= len(args):
                usage_error(f"{a} needs a value\n{USAGE}")
            v = args[i + 1]
            i += 1
            if a == "--backend":
                if v not in BACKENDS:
                    usage_error(f"--backend must be one of {', '.join(BACKENDS)}")
                opts["backend"] = v
            elif a == "--repeat":
                # ASCII digits only: str.isdigit() also accepts other Unicode digits.
                if not re.fullmatch(r"[0-9]+", v) or not 1 <= int(v) <= MAX_REPEAT:
                    usage_error(f"--repeat must be a whole number from 1 to {MAX_REPEAT}")
                opts["repeat"] = int(v)
            elif a == "--out":
                opts["out"] = v
            else:
                try:
                    x = float(v)
                except ValueError:
                    x = -1.0
                if not 0.0 <= x <= 1.0:
                    usage_error(f"{a} must be a number from 0 to 1")
                opts["min_sanity" if a == "--min-sanity" else "min_routing"] = x
        elif a in ("all",) + SUITES and i == 0:
            opts["suite"] = a
        else:
            usage_error(f"unknown argument {a!r}\n{USAGE}")
        i += 1
    return opts


def exact(suite: dict) -> float:
    """The unrounded accuracy. The report rounds to 3 places; a threshold must never see that."""
    total = suite["n"] * suite["runs"]
    return suite["correct"] / total if total else 0.0


def verdict(report: dict, opts: dict) -> tuple[str, list]:
    errors, misses = [], []
    if all(s.get("skipped") for s in report["suites"].values()):
        errors.append(NOTHING_MEASURED)
    for name, s in report["suites"].items():
        if s.get("skipped"):
            continue
        if s["errors"]:
            errors.append(f"{name}: {s['errors']} error(s)")
        for h in s.get("hard_check_failures", []):
            misses.append(f"routing hard check: {h}")
    s = report["suites"].get("sanity")
    if s and not s.get("skipped") and exact(s) < opts["min_sanity"]:
        misses.append(f"sanity accuracy {s['accuracy']:.3f} below {opts['min_sanity']:.3f}")
    r = report["suites"].get("routing")
    if r and opts["min_routing"] is not None and exact(r) < opts["min_routing"]:
        misses.append(f"routing pick accuracy {r['accuracy']:.3f} below {opts['min_routing']:.3f}")
    if errors:
        return "error", errors + misses
    return ("fail", misses) if misses else ("pass", [])


def render(report: dict) -> str:
    lines = [f"eval: backend {report['backend']}, model {report['model']}"
             + (f" @ {report['host']}" if report["host"] else "")
             + f", roster {report['roster_checked']}, repeat {report['repeat']}", ""]
    s = report["suites"].get("sanity")
    if s and s.get("skipped"):
        lines.append(f"sanity   skipped: {s['note']}")
    elif s:
        types = "  ".join(f"{k} {v['correct']}/{v['n']}" for k, v in s["by_type"].items())
        lines.append(f"sanity   {s['n']} items  accuracy {s['accuracy']:.3f} ({s['correct']}/{s['n'] * s['runs']})  "
                     f"errors {s['errors']}  stability {_fmt(s['stability'])}  "
                     f"p50 {_ms(s['latency']['p50_ms'])}  p95 {_ms(s['latency']['p95_ms'])}")
        lines.append(f"  by type: {types}")
        for it in s["items"]:
            if not all(it["correct"]):
                got = ", ".join("ERROR" if a is None else json.dumps(a) for a in it["answers"])
                lines.append(f"  wrong: {it['id']} expected {json.dumps(it['expect'])}, got {got}")
    r = report["suites"].get("routing")
    if r:
        d = r["decisions"]
        lines.append(f"routing  {r['n']} cases  pick accuracy {r['accuracy']:.3f} ({r['correct']}/{r['n'] * r['runs']})  "
                     f"stakes accuracy {_fmt(r['stakes_accuracy'])}  Brier {_fmt(r['stakes_brier'])}  "
                     f"stakes unscored {r['stakes_unscored']}  "
                     f"errors {r['errors']}  stability {_fmt(r['stability'])}")
        lines.append(f"  decisions: act {d.get('act', 0)}  ESCALATE {d.get('ESCALATE', 0)}  "
                     f"REJECT {d.get('REJECT', 0)}  p50 {_ms(r['latency']['p50_ms'])}  "
                     f"p95 {_ms(r['latency']['p95_ms'])}")
        lines.append("  hard checks: " + ("ok" if not r["hard_check_failures"] else
                                          f"{len(r['hard_check_failures'])} FAILED"))
        for it in r["items"]:
            if not all(it["correct"]):
                got = ", ".join("ERROR" if x is None else x["model"] for x in it["runs"])
                lines.append(f"  wrong: {it['id']} acceptable {', '.join(it['acceptable'])}; got {got}")
    for n in report["notes"]:
        lines.append(f"note: {n}")
    lines.append("")
    lines.append(f"verdict: {report['verdict']}" + (f" ({'; '.join(report['reasons'])})" if report["reasons"] else ""))
    return "\n".join(lines)


def _fmt(v) -> str:
    return "-" if v is None else f"{v:.3f}"


def _ms(v) -> str:
    return "-" if v is None else f"{v:.1f} ms"


def write_private(path: str, text: str) -> None:
    fd = os.open(os.path.expanduser(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)  # an existing file keeps its old mode on open: make it private too
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)


def main(args: list) -> int:
    opts = parse(args)
    suites = SUITES if opts["suite"] == "all" else (opts["suite"],)
    try:
        settings = eval_settings(opts["backend"])
        roster = shipped_roster()
        datasets, problems = {}, []
        for name in SUITES:
            data, sha = load_dataset(name)
            datasets[name] = (data, sha)
            if name in suites:
                problems += [f"{name}: {p}" for p in (validate_sanity(data) if name == "sanity"
                                                      else validate_routing(data, roster))]
        if problems:
            raise EvalError("dataset problems: " + "; ".join(problems))
        off = settings["backend"] == "off"
        report = {"version": fo_config.VERSION, "backend": settings["backend"],
                  "model": "heuristic" if off else (settings.get("model") or "jev-latest"),
                  "host": "" if off else host_of(settings), "roster_checked": roster.checked, "repeat": opts["repeat"],
                  "dataset_sha256": {n: datasets[n][1] for n in suites}, "suites": {}, "notes": []}
        for name in suites:
            if name == "sanity":
                if off:
                    note = "the local heuristic (jev.backend = off) has no System One to test"
                    report["suites"]["sanity"] = {"skipped": True, "note": note}
                    continue
                report["suites"]["sanity"] = run_sanity(settings, datasets["sanity"][0], opts["repeat"])
            else:
                report["suites"]["routing"] = run_routing(settings, datasets["routing"][0], roster, opts["repeat"])
        if off and "routing" in suites:
            report["notes"].append("routing used the local heuristic: no calibrated confidence, rule-based stakes")
    except fo_jev.JevError as e:  # its text is local; server bytes never reach it
        print(f"eval failed: {e}", file=sys.stderr)
        return 2
    except fo_config.ConfigError as e:
        print(f"eval failed: config error: {e}", file=sys.stderr)
        return 2
    except (EvalError, OSError, ValueError) as e:
        print(f"eval failed: {e}", file=sys.stderr)
        return 2
    report["verdict"], report["reasons"] = verdict(report, opts)
    text = json.dumps(report, indent=1)
    print(text if opts["json"] else render(report))
    if NOTHING_MEASURED in report["reasons"]:
        print(f"eval failed: {NOTHING_MEASURED} ({'; '.join(s['note'] for s in report['suites'].values())})",
              file=sys.stderr)
    if opts["out"]:
        try:
            write_private(opts["out"], text + "\n")
        except OSError as e:
            print(f"eval failed: cannot write --out {opts['out']}: {e.strerror or e}", file=sys.stderr)
            return 2
    return {"pass": 0, "fail": 1}.get(report["verdict"], 2)
