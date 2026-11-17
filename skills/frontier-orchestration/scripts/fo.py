#!/usr/bin/env python3
"""fo: the frontier-orchestration command line (stdlib only; run with python3 -I).

    fo.py install                         interactive installer (decision engine, providers, telemetry)
    fo.py setup        [--jev typesafe|openjev|codiv|custom|off] [--jev-url URL] [--jev-model ID]
                       [--providers claude,codex,gemini] [--ledger PATH] [--ledger-log PATH]
                       [--telemetry yes|no] [--upgrade-clis yes|no]
    fo.py doctor       [--live]           check config, keys, providers, ledger, telemetry
    fo.py providers    [--json]           how to reach each vendor from this host, read-only
    fo.py refresh      [--wait] [--upgrade] [--json] [--ttl-hours N]
    fo.py route        tasks.json | --demo | --check [tasks.json]  [--json]
    fo.py jev ping                        one tiny call to the configured Jev backend
    fo.py eval         [sanity|routing|all] [--backend B] [--repeat N] [--json] [--out PATH]
                       [--min-sanity 0.9] [--min-routing X]
                                          measure the decision engine (exit 0 pass, 1 fail, 2 error)
    fo.py ask          <claude|codex|gemini> [--prompt-file F | --prompt TEXT] [--model M]
                       [--effort E] [--cwd DIR] [--out FILE] [--timeout S] [--json]
                                          one read-only ask through that vendor's own CLI
    fo.py lessons      <init|recall|add|reinforce|contradict|retire|run-new|run-close|lint> ...
    fo.py config       <show|path|get KEY|set KEY VALUE>
    fo.py telemetry    <status|on|off|log>
    fo.py version

Exit codes: 0 ok, 1 failure, 2 a route pick was REJECTED (not an allowed candidate), 3 setup needs answers.
"""

from __future__ import annotations

import json
import os
import secrets
import signal
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:  # -I drops the script dir; add only this plugin's own folder
    sys.path.insert(0, HERE)

import fo_config  # noqa: E402
import fo_jev  # noqa: E402
import fo_lessons  # noqa: E402
import fo_providers  # noqa: E402
import fo_refresh  # noqa: E402
import fo_route  # noqa: E402
import fo_telemetry  # noqa: E402

ENTRY = os.path.abspath(__file__)


def err(msg: str) -> None:
    print(msg, file=sys.stderr)


def flag(args: list, name: str, default=None):
    if name in args:
        i = args.index(name)
        if i + 1 >= len(args):
            raise SystemExit(f"{name} needs a value")
        return args[i + 1]
    return default


def yes(v: str | None):
    if v is None:
        return None
    low = v.strip().lower()
    if low in ("y", "yes", "true", "1", "on"):
        return True
    if low in ("n", "no", "false", "0", "off"):
        return False
    raise SystemExit(f"expected yes or no, got {v!r}")


# ---------- per-command help ----------
# `fo.py <command> -h` (or --help) prints one of these and exits 0 before the command does any
# work: no config read or write, no network, no subprocess, no telemetry event. Keep each text in
# step with the flags its command really reads.

HELP = {
    "install": """usage: fo.py install
interactive installer: decision engine, providers, telemetry, ledger (needs a terminal)
  -h, --help                show this help and exit""",
    "setup": """usage: fo.py setup [flags]
write the config from flags; prints the questions that still need answers (exit 3)
  --jev B                   typesafe|openjev|codiv|custom|off: the Jev backend
  --jev-url URL             the Jev server base URL
  --jev-model ID            the Jev model id
  --providers LIST          comma list of claude,codex,gemini that this skill may use
  --telemetry yes|no        anonymous usage counts on or off
  --ledger PATH             the learning ledger folder
  --ledger-log PATH         the ledger activity log file
  --upgrade-clis yes|no     let fo.py refresh update the vendor CLIs
  --non-interactive         never ask in the terminal; print the missing questions
  -h, --help                show this help and exit""",
    "doctor": """usage: fo.py doctor [--live]
check config, keys, providers, ledger and telemetry (exit 1 on a problem)
  --live                    also call Jev once and check each provider CLI sign-in
  -h, --help                show this help and exit""",
    "providers": """usage: fo.py providers [--json]
how to reach each vendor from this host, read-only
  --json                    print the result as JSON
  -h, --help                show this help and exit""",
    "ask": """usage: fo.py ask <claude|codex|gemini> [flags]
one read-only ask through that vendor's own CLI (prompt from stdin when no prompt flag)
  --prompt-file F           read the prompt from file F
  --prompt TEXT             the prompt text
  --model M                 the model id
  --effort E                the reasoning effort
  --cwd DIR                 the folder the CLI runs in
  --out FILE                write the answer to FILE (default: a private file in the state folder)
  --timeout S               stop the CLI after S seconds (default 1800)
  --json                    print the result as JSON
  -h, --help                show this help and exit""",
    "route": """usage: fo.py route <tasks.json | --demo | --check [tasks.json]> [--json]
pick a model for each task
exit 1 when the route fails and gives no decision (for example, the independence rule leaves
no candidate); exit 2 when it gives a REJECT decision (the judge picked a model that is not an
allowed candidate); else exit 0
  tasks.json                the task file to route
  --demo                    route the built-in demo tasks
  --check [tasks.json]      check the roster and the task file; no routing
  --json                    print the decisions as JSON
  -h, --help                show this help and exit""",
    "eval": """usage: fo.py eval [sanity|routing|all] [flags]
measure the decision engine (exit 0 pass, 1 fail, 2 error)
  --backend B               off|typesafe|openjev|codiv|custom
  --repeat N                run each case N times (1 to 10)
  --json                    print the report as JSON
  --out PATH                also write the JSON report to PATH
  --min-sanity X            lowest sanity accuracy that passes, 0 to 1 (default 0.9)
  --min-routing X           lowest routing accuracy that passes, 0 to 1
  -h, --help                show this help and exit""",
    "refresh": """usage: fo.py refresh [flags]
print the last model snapshot; start one background refresh when it is stale
  --wait                    refresh now, in the foreground, then print
  --upgrade                 also update the vendor CLIs (default: config refresh.upgrade_clis)
  --no-upgrade              do not update the vendor CLIs
  --json                    print the snapshot as JSON
  --ttl-hours N             the stale limit in hours (default: config refresh.ttl_hours, 24)
  --worker                  internal: the background worker itself
  -h, --help                show this help and exit""",
    "config": """usage: fo.py config <show|path|get KEY|set KEY VALUE>
  show                      print the config in effect and the resolved folders (default)
  path                      print the config file path
  get KEY                   print one value (dotted key, for example jev.backend)
  set KEY VALUE             write one value to the config file
  -h, --help                show this help and exit""",
    "jev": """usage: fo.py jev ping
  ping                      one tiny call to the configured Jev backend
  -h, --help                show this help and exit""",
    "lessons": """usage: fo.py lessons <command> [flags]
  init                      set up the ledger folder
  recall     [--tags a,b] [--kind k] [--all] [--limit N]
  add        --slug s --rule R --why W --kind k --tags a,b --run RUN --evidence E
             [--source PATH] [--apply A] [--force]
  reinforce  ID --run RUN --evidence E [--source PATH] [--unretire]
  contradict ID --run RUN --evidence E [--source PATH] [--unretire]
  retire     ID --reason R [--run RUN]
  run-new    --id YYYY-MM-DD-project-slug-name --project SLUG --artefact KIND --title T
  run-close  RUN
  lint                      check every note in the ledger
  -h, --help                show this help and exit""",
    "telemetry": """usage: fo.py telemetry <status|on|off|log [N]>
  status                    print whether telemetry is on, and why (default)
  on                        opt in to anonymous usage counts
  off                       opt out
  log [N]                   print the last N sent events (default 20)
  -h, --help                show this help and exit""",
    "version": """usage: fo.py version
print the skill version
  -h, --help                show this help and exit""",
}


def wants_help(args: list) -> bool:
    """True when -h or --help comes before a "--" (after "--" every word is data)."""
    head = args[:args.index("--")] if "--" in args else args
    return "-h" in head or "--help" in head


def via_props(resolved: dict) -> dict:
    return {f"via_{v}": (r["via"] or "none") if r["enabled"] else "none" for v, r in resolved.items()}


# ---------- setup ----------

SETUP_QUESTIONS = [
    {"key": "--jev", "question": "Which Jev backend should route tasks?",
     "options": {"typesafe": "TypeSafe Jev, hosted; needs a TYPESAFE_API_KEY (console.typesafe.ai)",
                 "openjev": "OpenJev, open weights, self-hosted at http://127.0.0.1:8080 (no key)",
                 "codiv": "OpenJev hosted on Codiv; needs a CODIV_API_KEY (codiv.ai)",
                 "off": "No Jev: a local rule-based router, no network and no key"}},
    {"key": "--providers", "question": "Which providers' models may this skill use (Claude, Codex, Antigravity)?",
     "options": {"claude,codex,gemini": "all three (recommended when installed)",
                 "claude,codex": "Claude and Codex only", "claude": "Claude only (no independent review)"}},
    {"key": "--telemetry", "question": "Send anonymous usage counts (command, version, OS, outcome; never prompts, paths or output)?",
     "options": {"yes": f"help improve the skill; turn off any time with `{fo_config.cli()} telemetry off`",
                 "no": "send nothing"}},
]


def key_instructions(js: dict) -> list:
    if js["backend"] in ("off",):
        return []
    env_name = js.get("api_key_env") or "FO_JEV_API_KEY"
    try:
        have = bool(fo_jev.api_key(js, required=False))
    except fo_jev.JevError as e:  # for example a key file that is gone or too open
        return [f"API key for {js['backend']}: {e}"]
    if have:
        return [f"API key for {js['backend']}: found."]
    if not js.get("key_required"):
        return [f"API key for {js['backend']}: not needed (set {env_name} if your server asks for one)."]
    lines = [f"API key for {js['backend']}: NOT FOUND. The user stores it once, in one of these ways; "
             "never paste it into chat:"]
    lines += [f"  - {r}" for r in fo_jev.key_routes(js)]
    if js.get("signup"):
        lines.append(f"  get a key: {js['signup']}")
    return lines


def cmd_setup(args: list) -> int:
    data = fo_config.read_file()
    answers = {"--jev": flag(args, "--jev"), "--providers": flag(args, "--providers"),
               "--telemetry": flag(args, "--telemetry")}
    interactive = sys.stdin.isatty() and sys.stdout.isatty() and "--non-interactive" not in args
    already = {"--jev": (data.get("jev") or {}).get("backend"),
               "--providers": ",".join(v for v, p in (data.get("providers") or {}).items() if p.get("enabled", True)) or None,
               "--telemetry": {True: "yes", False: "no"}.get((data.get("telemetry") or {}).get("enabled"))}
    missing = [q for q in SETUP_QUESTIONS if not answers[q["key"]] and not already[q["key"]]]
    if missing and interactive:
        for q in missing:
            print(q["question"])
            opts = list(q["options"])
            for i, (k, v) in enumerate(q["options"].items(), 1):
                print(f"  {i}. {k:22} {v}")
            pick = input(f"choose 1-{len(opts)} [1]: ").strip() or "1"
            answers[q["key"]] = opts[int(pick) - 1] if pick.isdigit() and 0 < int(pick) <= len(opts) else pick
        missing = []
    if missing:
        print("SETUP NEEDS ANSWERS. Ask the user these questions (one at a time, with these options),")
        print(f"then run {fo_config.cli()} setup again with the flags. Do not answer them for the user.\n")
        for q in missing:
            print(f"{q['key']}: {q['question']}")
            for k, v in q["options"].items():
                print(f"    {k}: {v}")
        print(json.dumps({"needs": [q["key"] for q in missing]}))
        return 3

    if answers["--jev"]:
        # Keep the engine's own settings (URL, key source, model) only when BOTH the stored engine
        # (or the default) and the engine in effect now (file + FO_JEV_BACKEND) are the chosen one.
        # Any mismatch starts clean, so no setting made for one engine can steer another's key.
        stored = (data.get("jev") or {}).get("backend") or fo_config.DEFAULTS["jev"]["backend"]
        effective = fo_config.deep_merge(fo_config.deep_merge(fo_config.DEFAULTS, data), fo_config.env_overrides())
        if stored != answers["--jev"] or effective["jev"].get("backend") != answers["--jev"]:
            # A different engine starts from a clean section: an old base_url, insecure-http
            # permission or key source must never apply to the new engine's key.
            data["jev"] = {"backend": answers["--jev"]}
        else:
            data.setdefault("jev", {})["backend"] = answers["--jev"]  # same engine: keep its settings, record the choice
    if flag(args, "--jev-url"):
        data.setdefault("jev", {})["base_url"] = flag(args, "--jev-url")
    if flag(args, "--jev-model"):
        data.setdefault("jev", {})["model"] = flag(args, "--jev-model")
    if answers["--providers"]:
        want = {v.strip() for v in answers["--providers"].split(",") if v.strip()}
        bad = want - set(fo_config.VENDORS)
        if bad:
            raise SystemExit(f"unknown provider(s): {', '.join(sorted(bad))}")
        provs = data.setdefault("providers", {})
        for v in fo_config.VENDORS:
            provs.setdefault(v, {})["enabled"] = v in want
    if answers["--telemetry"]:
        data.setdefault("telemetry", {})["enabled"] = yes(answers["--telemetry"])
        data["telemetry"]["schema"] = fo_telemetry.SCHEMA_VERSION
    if flag(args, "--ledger"):
        data.setdefault("ledger", {})["path"] = fo_config.clean_path(flag(args, "--ledger"))
    if flag(args, "--ledger-log"):
        data.setdefault("ledger", {})["log"] = fo_config.clean_path(flag(args, "--ledger-log"))
    if flag(args, "--upgrade-clis"):
        data.setdefault("refresh", {})["upgrade_clis"] = yes(flag(args, "--upgrade-clis"))
    data["version"] = fo_config.SCHEMA_VERSION
    fo_config.validate(fo_config.deep_merge(fo_config.DEFAULTS, data))  # the file itself must be valid
    # The config as it will load (with FO_* environment). Set up its ledger BEFORE the
    # config is replaced: a ledger that cannot be used leaves the old config exactly as it was.
    root, log = fo_config.ledger_paths(fo_config.effective(data))
    fo_lessons.configure(root, log)
    try:
        fo_lessons.cmd_init(None)  # sets up and checks the ledger under its folder lock
    except (SystemExit, OSError) as e:
        print(f"FAIL ledger {root} could not be set up: {e}; the config was not changed", file=sys.stderr)
        return 1
    path = fo_config.write_file(data)
    cfg = fo_config.load()
    print(f"config written: {path}")

    js = fo_config.jev_settings(cfg)
    print(f"Jev: {js['label']} -> {js['base_url'] or '(local heuristic)'} model {js['model'] or '-'}")
    for line in key_instructions(js):
        print(line)
    host = fo_providers.detect_host()
    resolved = fo_providers.resolve(cfg, host)
    print(fo_providers.render(resolved, host))
    on, why = fo_telemetry.status(cfg)
    print(f"telemetry: {why}")
    print(f"next: {fo_config.cli()} doctor --live")
    fo_telemetry.record("setup", {"ok": True, "mode": "flags", "jev_backend": js["backend"], "host": host,
                                  **via_props(resolved)}, cfg)
    return 0


# ---------- doctor ----------

def cmd_doctor(args: list) -> int:
    problems, notes = [], []
    try:
        cfg = fo_config.load()
    except fo_config.ConfigError as e:
        print(f"FAIL config: {e}")
        return 1
    for key in fo_config.removed_keys():
        notes.append(f"{key} was removed and is ignored (fo.py ask always runs its read-only command); "
                     "delete it from the config")
    cpath = fo_config.config_path()
    configured = os.path.exists(cpath)
    if configured:
        print(f"ok   config {cpath}")
    else:  # an honest exit 1: nothing is set up yet, so the default engine has no key to check
        problems.append(f"setup has not run yet (no config at {cpath}): run {fo_config.cli()} install, "
                        f"or {fo_config.cli()} setup, first")
    if sys.version_info < (3, 9):
        problems.append(f"python {sys.version.split()[0]} is too old; need 3.9+")
    else:
        print(f"ok   python {sys.version.split()[0]}")

    roster = fo_route.Roster(fo_config.roster(cfg))
    rp = fo_route.self_check(fo_route.DEMO, roster)
    if rp:
        problems += [f"roster: {p}" for p in rp]
    else:
        print(f"ok   roster {len(roster.models)} models, checked {roster.checked}")

    js = fo_config.jev_settings(cfg)
    if js["backend"] == "off":
        print("ok   jev off: routing uses the local heuristic (no calibrated confidence)")
    elif not configured and not os.environ.get("FO_JEV_BACKEND"):
        notes.append(f"jev: no engine chosen yet (the default, {js['backend']}, is checked after setup)")
    else:
        try:
            key = fo_jev.api_key(js)
            print(f"ok   jev {js['backend']} {js['base_url']} model {js['model']}; key {'found' if key else 'not needed'}")
            if "--live" in args:
                r = fo_jev.ping(js)
                print(f"ok   jev live: model {r['model']} answered (p_true {r['p_true']})")
        except fo_jev.JevError as e:
            problems.append(f"jev: {e}")

    host = fo_providers.detect_host()
    resolved = fo_providers.resolve(cfg, host)
    sessions = {}
    if "--live" in args:  # a CLI that is installed but signed out cannot answer: the same check as `providers`
        sessions = fo_providers.sessions(resolved)
        for v, r in resolved.items():
            if r["enabled"] and r["via"] and not sessions[v]["signed_in"]:
                problems.append(f"{v}: not signed in ({sessions[v]['detail']}); "
                                f"sign in: {sessions[v]['how_to_sign_in']}")
    print(fo_providers.render(resolved, host, sessions or None))
    if len(fo_providers.enabled_vendors(resolved)) < 2:
        notes.append("only one vendor is reachable: the different-vendor review rule cannot be met")
    for v, r in resolved.items():
        if r["enabled"] and not r["via"]:
            notes.append(f"{v}: {r['note']}")

    root, log = fo_config.ledger_paths(cfg)
    try:
        os.makedirs(root, exist_ok=True)
        fd, probe = tempfile.mkstemp(dir=root, prefix=".fo-write-test-")  # new file only, never an old one
        os.close(fd)
        os.remove(probe)
        print(f"ok   ledger {root}")
    except OSError as e:
        problems.append(f"ledger {root} is not writable: {e}")

    on, why = fo_telemetry.status(cfg)
    print(f"ok   telemetry: {why}")
    snap = fo_refresh.load()
    if snap:
        print(f"ok   model snapshot {fo_refresh.age_hours(snap):.1f} h old, {len(snap.get('drift', []))} drift item(s)")
    else:
        notes.append(f"no model snapshot yet: run {fo_config.cli()} refresh --wait")
    for n in notes:
        print(f"note {n}")
    for p in problems:
        print(f"FAIL {p}")
    fo_telemetry.record("doctor", {"ok": not problems, "jev_backend": js["backend"], "host": host,
                                   "vendors_reachable": len(fo_providers.enabled_vendors(resolved)),
                                   "independent_review_possible": len(fo_providers.enabled_vendors(resolved)) >= 2,
                                   **via_props(resolved)}, cfg)
    return 1 if problems else 0


# ---------- route ----------

def classify(e: Exception) -> str:
    if isinstance(e, fo_jev.JevError):
        if e.status in (401, 403):
            return "jev_auth"
        if e.status == 0:
            return "jev_network"
        return "jev_other"
    if isinstance(e, fo_route.RouteError):
        return "independence" if "independence" in str(e) else "input"
    if isinstance(e, fo_config.ConfigError):
        return "config"
    return "other"


class InputError(Exception):
    """Bad user input (a file, a flag): one line for the user, exit 1, no traceback."""


def read_spec(path: str) -> dict:
    """A task file as a dict, or InputError("cannot read <path>: <reason>")."""
    def bad(reason: str):
        return InputError(f"cannot read {path}: {reason}")
    try:
        with open(path, "rb") as f:
            raw = f.read(16 * 1024 * 1024 + 1)
    except OSError as e:
        raise bad(e.strerror or type(e).__name__) from None
    if len(raw) > 16 * 1024 * 1024:
        raise bad("larger than 16 MB")
    try:
        spec = json.loads(raw.decode("utf-8"))
    except UnicodeDecodeError:
        raise bad("not UTF-8 text") from None
    except ValueError as e:
        raise bad(f"not valid JSON ({e})") from None
    if not isinstance(spec, dict):
        raise bad(f"the top level must be a JSON object, not a {type(spec).__name__}")
    tasks = spec.get("tasks")
    if tasks is not None and not isinstance(tasks, dict):
        raise bad('"tasks" must be an object of task id -> attributes')
    for tid, attrs in (tasks or {}).items():
        if not isinstance(attrs, dict):
            raise bad(f"task {tid!r} must be an object")
        cands = attrs.get("candidates")
        if cands is not None and (not isinstance(cands, list) or not all(isinstance(c, str) for c in cands)):
            raise bad(f"task {tid!r}: candidates must be a list of model ids")
    return spec


def cmd_route(args: list, cfg: dict) -> int:
    roster = fo_route.Roster(fo_config.roster(cfg))
    rest = [a for a in args if not a.startswith("--")]
    try:
        spec_file = read_spec(rest[0]) if rest and "--demo" not in args else None
    except InputError as e:
        err(f"route failed: {e}")
        return 1
    if "--check" in args:
        spec = spec_file or fo_route.DEMO
        problems = fo_route.self_check(spec, roster)
        for p in problems:
            print(f"problem: {p}")
        vendors = sorted({m.get("vendor") for m in roster.models.values()})
        print(f"roster {len(roster.models)} ids, vendors {', '.join(vendors)}, tasks {len(spec.get('tasks', {}))}: "
              + ("FAIL" if problems else "ok"))
        return 1 if problems else 0
    if "--demo" in args:
        spec = fo_route.DEMO
    elif spec_file is not None:
        spec = spec_file
    else:
        print(fo_route.__doc__)
        return 1
    js = fo_config.jev_settings(cfg)
    host = fo_providers.detect_host()
    enabled = fo_providers.configured_vendors(cfg)
    t0 = time.monotonic()
    props = {"jev_backend": js["backend"], "host": host, "judge": "heuristic" if js["backend"] == "off" else "jev",
             "tasks": len(spec.get("tasks") or {})}
    try:
        decisions = fo_route.route(spec, roster, js, enabled=enabled, warn=err)
    except (fo_jev.JevError, fo_route.RouteError, KeyError, ValueError) as e:
        fo_telemetry.record("route", {**props, "ok": False, "error_kind": classify(e),
                                      "duration": fo_telemetry.duration_bucket(time.monotonic() - t0)}, cfg)
        err(f"route failed: {e}")
        return 1
    counts = {k: sum(1 for d in decisions.values() if d["decision"] == v)
              for k, v in (("acted", "act"), ("escalated", "ESCALATE"), ("rejected", "REJECT"))}
    fo_telemetry.record("route", {**props, **counts, "ok": True, "error_kind": "none",
                                  "duration": fo_telemetry.duration_bucket(time.monotonic() - t0)}, cfg)
    print(json.dumps(decisions, indent=1) if "--json" in args else fo_route.render(decisions))
    return 2 if counts["rejected"] else 0


# ---------- small commands ----------

def cmd_config(args: list) -> int:
    sub = args[0] if args else "show"
    if sub == "path":
        print(fo_config.config_path())
    elif sub == "show":
        cfg = fo_config.load()
        print(json.dumps(cfg, indent=2))
        root, log = fo_config.ledger_paths(cfg)
        print(f"\nresolved: ledger {root}, log {log}, state {fo_config.state_dir()}")
    elif sub == "get" and len(args) == 2:
        print(json.dumps(fo_config.get_dotted(fo_config.load(), args[1])))
    elif sub == "set" and len(args) >= 3 and len(args) % 2 == 1:
        pairs = [(args[i], fo_config.parse_value(args[i + 1])) for i in range(1, len(args), 2)]
        data = fo_config.read_file()
        try:
            fo_config.set_many(data, pairs)  # every pair is set, then the whole file is checked once
        except fo_config.ConfigError as e:
            others = [k for k in fo_config.broken_path_keys(data) if k not in {k for k, _ in pairs}]
            if others:  # a key this call did not change is broken too: say how to fix both at once
                raise fo_config.ConfigError(
                    f"{e}. Fix {', '.join(others)} in the same call, for example: {fo_config.cli()} config set "
                    "ledger.path ~/notes/fo-ledger ledger.log null") from None
            raise
        print(f"written: {fo_config.write_file(data)}")
    else:
        raise SystemExit(f"usage: {fo_config.cli()} config <show|path|get KEY|set KEY VALUE [KEY VALUE ...]>")
    return 0


def cmd_telemetry(args: list, cfg: dict) -> int:
    sub = args[0] if args else "status"
    if sub in ("on", "off"):
        data = fo_config.read_file()
        data.setdefault("telemetry", {})["enabled"] = sub == "on"
        data["telemetry"]["schema"] = fo_telemetry.SCHEMA_VERSION  # consent covers this field set
        fo_config.write_file(data)
        fo_telemetry.remove_legacy_files()
        cfg = fo_config.load()
    elif sub == "log":
        try:
            n = int(args[1]) if len(args) > 1 else 20
            if n < 0:
                raise ValueError
        except ValueError:
            raise InputError(f"telemetry log N: N must be a whole number of events, not {args[1]!r}") from None
        for e in fo_telemetry.read_log(n):
            print(json.dumps(e, sort_keys=True))
        return 0
    elif sub != "status":
        raise SystemExit("usage: fo.py telemetry <status|on|off|log>")
    on, why = fo_telemetry.status(cfg)
    print(f"telemetry: {why}")
    print(f"details: {os.path.join(fo_config.SKILL_DIR, 'references', 'privacy.md')}; every sent event is in `{fo_config.cli()} telemetry log`")
    return 0


def cmd_lessons(args: list, cfg: dict) -> int:
    root, log = fo_config.ledger_paths(cfg)
    fo_lessons.configure(root, log)
    sub = args[0] if args else "none"
    code = 0
    ok = True
    try:
        code = fo_lessons.main(args) or 0
    except SystemExit as e:
        ok = e.code in (0, None)
        fo_telemetry.record("lessons", {"subcommand": sub if sub in fo_telemetry.ENUMS["subcommand"] else "none",
                                        "ok": ok, "error_kind": "none" if ok else "ledger"}, cfg)
        raise
    fo_telemetry.record("lessons", {"subcommand": sub if sub in fo_telemetry.ENUMS["subcommand"] else "none",
                                    "ok": code == 0}, cfg)
    return code


def cmd_ask(args: list, cfg: dict) -> int:
    if not args or args[0] not in fo_config.VENDORS:
        raise SystemExit("usage: fo.py ask <claude|codex|gemini> [--prompt-file F | --prompt TEXT] ...")
    vendor = args[0]
    cwd = flag(args, "--cwd")
    if cwd is not None and not os.path.isdir(cwd):  # before the vendor CLI runs: it would get the blame
        err(f"ask refused: --cwd {cwd} is not an existing folder")
        return 1
    try:
        timeout = int(flag(args, "--timeout", fo_providers.ASK_TIMEOUT_S))
        if timeout <= 0:
            raise ValueError
    except ValueError:
        err("ask refused: --timeout must be a whole number of seconds above 0")
        return 1
    if flag(args, "--prompt-file"):
        try:
            with open(flag(args, "--prompt-file"), encoding="utf-8") as f:
                prompt = f.read()
        except (OSError, UnicodeDecodeError) as e:
            reason = "not UTF-8 text" if isinstance(e, UnicodeDecodeError) else (e.strerror or type(e).__name__)
            err(f"ask refused: cannot read --prompt-file {flag(args, '--prompt-file')}: {reason}")
            return 1
    elif flag(args, "--prompt"):
        prompt = flag(args, "--prompt")
    else:
        prompt = sys.stdin.read()
    if not prompt.strip():
        raise SystemExit("empty prompt")
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = flag(args, "--out")
    if not out:
        asks = os.path.join(fo_config.state_dir(), "asks")
        os.makedirs(asks, mode=0o700, exist_ok=True)
        os.chmod(asks, 0o700)  # an older build may have made it 0755
        out = os.path.join(asks, f"{stamp}-{vendor}-{os.getpid()}-{secrets.token_hex(4)}.md")
    host = fo_providers.detect_host()
    t0 = time.monotonic()

    def _stop(signum, frame):  # a supervisor's SIGTERM must reach the runner's cleanup too
        raise KeyboardInterrupt()

    signal.signal(signal.SIGTERM, _stop)
    try:
        r = fo_providers.run(cfg, vendor, prompt, model=flag(args, "--model"), effort=flag(args, "--effort"),
                             cwd=cwd, out_file=out, timeout=timeout, host=host)
    except fo_providers.AskError as e:
        fo_telemetry.record("ask", {"ask_vendor": vendor, "ask_status": "refused", "ok": False, "host": host}, cfg)
        err(f"ask refused: {e}")
        return 1
    fo_telemetry.record("ask", {"ask_vendor": vendor, "ask_status": r["status"], "ok": r["status"] == "ok",
                                "host": host, f"via_{vendor}": r["via"] or "none",
                                "duration": fo_telemetry.duration_bucket(time.monotonic() - t0)}, cfg)
    if "--json" in args:
        print(json.dumps(r, indent=1))
    else:
        print(f"{r['vendor']} via {r['via']} ({r['model']}): {r['status']} in {r['seconds']} s -> {r['out_file']}")
        if r["stderr_tail"]:
            err(r["stderr_tail"])
    return 0 if r["status"] == "ok" else 1


def main(argv: list) -> int:
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    cmd, args = argv[0], argv[1:]
    if cmd == "lessons" and args and args[0] in fo_lessons.SUBCOMMANDS and wants_help(args[1:]):
        print(fo_lessons.help_text(args[0]))  # that subcommand's own flags, not the generic list
        return 0
    if cmd in HELP and wants_help(args):  # before any work: no config, network, subprocess or telemetry
        print(HELP[cmd])
        return 0
    if cmd == "version":
        print(fo_config.VERSION)
        return 0
    if cmd == "install":
        import fo_install
        return fo_install.run_installer()
    if cmd == "setup":
        return cmd_setup(args)
    if cmd == "doctor":
        return cmd_doctor(args)
    if cmd == "config":
        return cmd_config(args)
    if cmd == "eval":  # reads the config itself (for --backend); sends no telemetry
        import fo_eval
        return fo_eval.main(args)
    cfg = fo_config.load()
    if cmd == "providers":
        host = fo_providers.detect_host()
        resolved = fo_providers.resolve(cfg, host)
        sessions = fo_providers.sessions(resolved)
        if "--json" in args:
            print(json.dumps({v: {**r, "signed_in": sessions.get(v, {}).get("signed_in")} for v, r in resolved.items()},
                             indent=1))
        else:
            print(fo_providers.render(resolved, host, sessions))
        fo_telemetry.record("providers", {"ok": True, "host": host, **via_props(resolved),
                                          "vendors_reachable": len(fo_providers.enabled_vendors(resolved))}, cfg)
        return 0
    if cmd == "refresh":
        code = fo_refresh.main(cfg, args, ENTRY)
        if "--worker" not in args:
            fo_telemetry.record("refresh", {"ok": code == 0}, cfg)
        return code
    if cmd == "route":
        return cmd_route(args, cfg)
    if cmd == "jev":
        if args[:1] != ["ping"]:
            raise SystemExit("usage: fo.py jev ping")
        js = fo_config.jev_settings(cfg)
        try:
            r = fo_jev.ping(js)
        except fo_jev.JevError as e:
            fo_telemetry.record("jev_ping", {"ok": False, "jev_backend": js["backend"], "error_kind": classify(e)}, cfg)
            err(f"jev ping failed: {e}")
            return 1
        fo_telemetry.record("jev_ping", {"ok": True, "jev_backend": js["backend"]}, cfg)
        print(json.dumps(r))
        return 0
    if cmd == "lessons":
        return cmd_lessons(args, cfg)
    if cmd == "ask":
        return cmd_ask(args, cfg)
    if cmd == "telemetry":
        return cmd_telemetry(args, cfg)
    raise SystemExit(f"unknown command {cmd!r}; run {fo_config.cli()} --help")


def run(argv: list) -> int:
    """main() with one guard: a user never sees a traceback. FO_DEBUG=1 shows it."""
    try:
        return main(argv)
    except fo_config.ConfigError as e:
        err(f"config error: {e}")
        return 1
    except InputError as e:
        err(f"fo.py: {e}")
        return 1
    except KeyboardInterrupt:
        err("stopped")
        return 130
    except BrokenPipeError:  # for example `fo.py ... | head`
        return 1
    except Exception as e:  # noqa: BLE001 - the last line of defence
        if os.environ.get("FO_DEBUG") == "1":
            raise
        text = str(e).splitlines()[0][:300] if str(e) else ""
        err(f"fo.py failed: {type(e).__name__}{': ' + text if text else ''} "
            "(set FO_DEBUG=1 to see the traceback; please report it)")
        return 1


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
