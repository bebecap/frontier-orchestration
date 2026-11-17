"""route: ask Jev which model should do each task, then gate the answer on stakes.

Input (tasks.json):

    {
      "purpose": "one line on what the pipeline produces and who acts on it",
      "policy":  "Prefer the cheapest model that does the job reliably.",
      "tasks": {
        "<task_id>": {
          "what": "what this task does",
          "context_volume": "low | medium | high | very high",
          "output_kind": "prose | code | structured findings | pass/fail",
          "needs_independence": "no | YES - must fail differently from the producer",
          "verifiable_by_code": "no | partly | FULLY",
          "cost_sensitivity": "low | medium | high",
          "producer_vendor": "claude | codex | gemini (who made the artefact)",
          "needs_long_context": "no | yes",
          "candidates": ["model-id or alias", "..."]
        }
      }
    }

Why TWO questions per task: Choice confidence measures how concentrated the distribution is,
NOT whether the answer is right. Pruning obviously-wrong options makes confidence FALL while the
routing gets better. So a low-confidence route matters only when the task is also high-stakes.
"""

from __future__ import annotations

import math
import re

import fo_jev

ACT_THRESHOLD = 0.80   # "act above roughly 0.8"
STAKES_FLOOR = 0.50    # below this, a routing tie is harmless
REVIEW_VENDORS = ("claude", "codex", "gemini")
# "yes", "no", "true", "false", "y", "n", optionally followed by a separator and a note
# ("YES - must fail differently"). Anything else ("not sure", "not optional") is rejected.
_YESNO = re.compile(r"^(yes|no|true|false|y|n)(?:\s*[-:,(\u2013\u2014].*)?$", re.S)
TIER_RANK = {"top": 4, "frontier": 3, "mid": 2, "small": 1, "judge": 0}


class RouteError(Exception):
    pass


class Roster:
    def __init__(self, data: dict):
        self.models = data.get("models", {})
        self.checked = data.get("checked", "")
        self.alias = {}
        for mid, m in self.models.items():
            for a in m.get("aliases", []) or []:
                self.alias[a.lower()] = mid

    def canonical(self, candidate: str) -> str:
        c = str(candidate).strip()
        return self.alias.get(c.lower(), c)

    def vendor_of(self, candidate: str) -> str:
        c = self.canonical(candidate)
        if c in self.models:
            return self.models[c].get("vendor", "")
        low = c.lower()
        if low.startswith(("gpt-", "codex")):
            return "codex"
        if low.startswith("gemini"):
            return "gemini"
        if low.startswith("claude"):
            return "claude"
        if low.startswith(("jev", "openjev")):
            return "jev"
        return ""

    def describe(self, candidate: str) -> str:
        m = self.models.get(self.canonical(candidate))
        return m["description"] if m else candidate

    def tier(self, candidate: str) -> int:
        m = self.models.get(self.canonical(candidate))
        return TIER_RANK.get((m or {}).get("tier", ""), 2)


def needs_independence(attrs: dict) -> bool:
    """YES / true -> True; no / false / missing -> False; anything else is an input error (fail closed)."""
    v = attrs.get("needs_independence")
    if isinstance(v, bool):
        return v
    if v is None:
        return False
    s = str(v).strip().lower()
    if s == "":
        return False
    m = _YESNO.match(s)
    if not m:
        raise RouteError(f"needs_independence must be yes or no (optionally followed by ' - note'), not {v!r}")
    return m.group(1) in ("yes", "true", "y")


def independent_candidates(roster: Roster, tid: str, attrs: dict, enabled: set | None = None):
    """(allowed candidates, problems). Drops the producer's vendor, unknown vendors and disabled vendors."""
    cands = [roster.canonical(c) for c in (attrs.get("candidates") or list(roster.models))]
    problems = []
    if enabled is not None:
        kept = [c for c in cands if roster.vendor_of(c) in enabled or roster.vendor_of(c) == "jev"]
        problems += [f"{tid}: candidate {c} is from vendor {roster.vendor_of(c) or 'unknown'}, which is not enabled"
                     for c in cands if c not in kept]
        cands = kept
    if not needs_independence(attrs):
        if not cands:
            problems.append(f"{tid}: no candidate left")
        return cands, problems
    producer = str(attrs.get("producer_vendor") or "").strip().lower()
    if producer not in REVIEW_VENDORS:
        return [], problems + [f"{tid}: needs_independence YES but producer_vendor is {producer or 'missing'} "
                               f"(must be one of {', '.join(REVIEW_VENDORS)})"]
    # A reviewer must be a model that writes a review: Claude, Codex or Gemini, not the producer's
    # vendor. A judge (Jev) never counts as an independent reviewer.
    allowed = [c for c in cands if roster.vendor_of(c) in REVIEW_VENDORS and roster.vendor_of(c) != producer]
    for c in cands:
        if c in allowed:
            continue
        v = roster.vendor_of(c)
        problems.append(f"{tid}: candidate {c} is the producer's vendor {producer}" if v == producer else
                        f"{tid}: candidate {c} is a judge, not a reviewer (dropped)" if v == "jev" else
                        f"{tid}: candidate {c} has no known vendor (dropped; independence cannot be checked)")
    if not allowed:
        problems.append(f"{tid}: no candidate from a vendor other than {producer}")
    return allowed, problems


def probability(v, where: str) -> float:
    """A judge's number must be a finite probability. Anything else stops the route (fail closed)."""
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0.0 <= v <= 1.0 or not math.isfinite(v):
        # Never echo the value: a judge reply is untrusted and may carry reflected secrets.
        raise RouteError(f"invalid judge reply: {where} is not a probability in [0, 1] "
                         f"(got a {type(v).__name__})")
    return float(v)


def gate(conf: float | None, stakes: float) -> tuple[str, str]:
    if conf is not None and conf >= ACT_THRESHOLD:
        return "act", f"confidence {conf:.2f} clears {ACT_THRESHOLD}"
    if stakes < STAKES_FLOOR:
        c = "no confidence (heuristic)" if conf is None else f"near-tie ({conf:.2f})"
        return "act", f"{c} but stakes {stakes:.2f} are low; the choice barely matters"
    c = "heuristic route" if conf is None else f"confidence {conf:.2f} below {ACT_THRESHOLD}"
    return "ESCALATE", f"{c} AND stakes {stakes:.2f} at or above {STAKES_FLOOR}"


def heuristic_stakes(attrs: dict) -> float:
    s = 0.5
    if needs_independence(attrs):
        s += 0.25
    ver = str(attrs.get("verifiable_by_code", "")).lower()
    if ver.startswith("fully"):
        s -= 0.3
    elif ver.startswith("no"):
        s += 0.15
    if "high" in str(attrs.get("cost_sensitivity", "")).lower():
        s -= 0.1
    kind = str(attrs.get("output_kind", "")).lower()
    if "critique" in kind or "prose" in kind:
        s += 0.1
    return max(0.0, min(1.0, round(s, 2)))


def heuristic_route(roster: Roster, tasks: dict, allowed: dict) -> dict:
    out = {}
    for tid, attrs in tasks.items():
        stakes = heuristic_stakes(attrs)
        cands = allowed[tid]
        if stakes >= STAKES_FLOOR:
            best = max(roster.tier(c) for c in cands)
        else:
            best = min(roster.tier(c) for c in cands if roster.tier(c) > 0) if any(roster.tier(c) > 0 for c in cands) else 0
        top = [c for c in cands if roster.tier(c) == best]
        decision, why = gate(None, stakes)
        if decision == "ESCALATE" and len(top) == 1:
            decision, why = "act", f"heuristic: one clear {('highest' if stakes >= STAKES_FLOOR else 'cheapest')} tier pick; stakes {stakes:.2f}"
        out[tid] = {"model": top[0], "confidence": None, "stakes": stakes, "decision": decision,
                    "why": why, "judge": "heuristic", "tied_with": top[1:]}
    return out


def build_questions(roster: Roster, tasks: dict, allowed: dict) -> dict:
    questions = {}
    for tid, attrs in tasks.items():
        questions[f"route_{tid}"] = {
            "type": "choice",
            "instructions": {"task_attributes": attrs,
                             "question": f"Given `task_attributes` and `models`, which model should perform `{tid}`? Apply `policy`."},
            "criteria": {c: roster.describe(c) for c in allowed[tid]},
        }
        questions[f"stakes_{tid}"] = {
            "type": "noul",
            "instructions": {"task_attributes": attrs,
                             "question": f"Would a mistake on `{tid}` be EXPENSIVE or HARD TO DETECT, such that it justifies a frontier-tier model rather than a mid-tier one?"},
            "criteria": {"true": "An error propagates into a decision someone acts on, or is hard to catch downstream.",
                         "false": "An error is cheap, obvious, or caught by a later mechanical check."},
        }
    return questions


def route(spec: dict, roster: Roster, jev_settings: dict, enabled: set | None = None,
          warn=None) -> dict:
    tasks = spec.get("tasks") or {}
    if not tasks:
        raise RouteError("spec has no tasks")
    allowed = {}
    for tid, attrs in tasks.items():
        cands, problems = independent_candidates(roster, tid, attrs, enabled)
        if not cands:
            kind = "independence rule" if needs_independence(attrs) else "no usable candidate"
            raise RouteError(f"{kind}: " + "; ".join(problems))
        for p in problems:
            if warn:
                warn(f"dropped: {p}")
        allowed[tid] = cands
    if jev_settings.get("backend") == "off":
        return heuristic_route(roster, tasks, allowed)

    state = {"purpose": spec.get("purpose", ""),
             "policy": spec.get("policy", "Prefer the cheapest model that does the job reliably."),
             "models": {c: roster.describe(c) for cs in allowed.values() for c in cs},
             "tasks": tasks}
    answers = fo_jev.ask(jev_settings, state, build_questions(roster, tasks, allowed))["answers"]
    if not isinstance(answers, dict):
        raise RouteError("invalid judge reply: 'answers' is not an object")
    out = {}
    for tid in tasks:
        choice, noul = answers.get(f"route_{tid}"), answers.get(f"stakes_{tid}")
        if not isinstance(choice, dict) or not isinstance(noul, dict):
            raise RouteError(f"invalid judge reply: missing or malformed answers for task {tid}")
        stakes = probability(noul.get("noul"), f"stakes_{tid}.noul")
        conf = probability(choice.get("confidence"), f"route_{tid}.confidence")
        raw = choice.get("choice")
        pick = roster.canonical(raw) if isinstance(raw, str) else ""
        if pick not in allowed[tid]:
            # Show the pick only when it is a roster id: any other text came from the server.
            shown = pick if pick in roster.models else "(not a roster id)"
            pick = shown
            decision, why = "REJECT", f"the judge picked {shown}, which is not an allowed candidate"
        else:
            decision, why = gate(conf, stakes)
        out[tid] = {"model": pick, "confidence": round(conf, 3), "stakes": round(stakes, 3),
                    "stakes_exact": stakes,  # the unrounded value the gate used (for scoring)
                    "decision": decision, "why": why, "judge": jev_settings.get("model") or "jev"}
    return out


def self_check(spec: dict, roster: Roster) -> list:
    """No-network check of the roster and a spec. Returns a list of problems."""
    problems = [f"roster id without a vendor: {m}" for m, e in roster.models.items() if not e.get("vendor")]
    problems += [f"roster id without a description: {m}" for m, e in roster.models.items() if not e.get("description")]
    seen = {}
    for mid, e in roster.models.items():
        for a in e.get("aliases", []) or []:
            if a.lower() in seen and seen[a.lower()] != mid:
                problems.append(f"alias {a} names both {seen[a.lower()]} and {mid}")
            seen[a.lower()] = mid
    for tid, attrs in (spec.get("tasks") or {}).items():
        for c in attrs.get("candidates") or []:
            if roster.canonical(c) not in roster.models:
                problems.append(f"{tid}: candidate {c} is not in the roster")
        try:
            problems += independent_candidates(roster, tid, attrs)[1]
        except RouteError as e:
            problems.append(f"{tid}: {e}")
    return problems


def render(decisions: dict) -> str:
    width = max(len(t) for t in decisions)
    lines = [f"{'task'.ljust(width)}  {'model':18} {'conf':>5} {'stakes':>7}  decision",
             "-" * (width + 46)]
    for tid, d in decisions.items():
        conf = "  -  " if d["confidence"] is None else f"{d['confidence']:>5.2f}"
        lines.append(f"{tid.ljust(width)}  {d['model']:18} {conf} {d['stakes']:>7.2f}  {d['decision']}")
    lines.append("")
    for tid, d in decisions.items():
        tie = f" (tied with {', '.join(d['tied_with'])})" if d.get("tied_with") else ""
        lines.append(f"  {tid}: {d['why']}{tie}")
    if any(d.get("judge") == "heuristic" for d in decisions.values()):
        lines.append("\nJudge: local heuristic (jev.backend = off). No calibrated confidence; stakes are rule-based.")
    esc = [t for t, d in decisions.items() if d["decision"] == "ESCALATE"]
    rej = [t for t, d in decisions.items() if d["decision"] == "REJECT"]
    if esc:
        lines.append(f"\nESCALATED: {', '.join(esc)}")
        lines.append("Break the tie with HEAD-TO-HEAD Nouls over the same candidates, or run both and reconcile.")
    if rej:
        lines.append(f"\nREJECTED (pick is not an allowed candidate): {', '.join(rej)}")
    return "\n".join(lines)


DEMO = {
    "purpose": "A pipeline producing a defensible engineering estimate a customer will budget against.",
    "policy": "Prefer the cheapest model that does the job reliably. Spend frontier capability only where a mistake is expensive or hard to detect.",
    "tasks": {
        "repo_sweep": {"what": "Read repositories and report, with file:line citations, what the code does today.",
                       "context_volume": "very high", "output_kind": "structured findings",
                       "needs_independence": "no", "verifiable_by_code": "partly: citations can be checked",
                       "cost_sensitivity": "high, many parallel lanes",
                       "candidates": ["opus", "fable", "sonnet"]},
        "estimate_review": {"what": "Attack a finished estimate: find the underestimated item and the unpriced dependency.",
                            "context_volume": "medium", "output_kind": "argued critique",
                            "needs_independence": "YES - must fail differently from the producer",
                            "verifiable_by_code": "partly", "cost_sensitivity": "low, one call",
                            "producer_vendor": "claude", "needs_long_context": "no",
                            "candidates": ["gpt-6-astra", "gpt-6.1-sol", "gpt-5.6-sol", "gemini-4-argon"]},
        "arithmetic_audit": {"what": "Check the numbers add up and nothing is double counted.",
                             "context_volume": "low", "output_kind": "pass/fail",
                             "needs_independence": "no", "verifiable_by_code": "FULLY",
                             "cost_sensitivity": "high",
                             "candidates": ["haiku", "sonnet", "jev"]},
    },
}
