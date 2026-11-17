"""Anonymous, opt-in usage telemetry. See PRIVACY.md at the repository root.

See references/privacy.md (the same text as PRIVACY.md at the repository root).

Off unless ALL of these hold:
    - the user opted in (`fo.py telemetry on`, or the consent question in `fo.py setup`)
    - DO_NOT_TRACK is not set to 1, FO_TELEMETRY_DISABLED is not set, FO_TELEMETRY is not 0
    - not running in CI
    - this build has a collector configured (data/telemetry.json)

What is sent: one event per command, with only the keys in PROPS below. Never prompts, task
text, file paths, repository names, model output, error text, hostnames, user names or keys.
There is no install id: each event carries a new random UUID (the PostHog format needs one) that
is never stored or reused, so two events cannot be linked.
Every event is written to a local audit log first: `fo.py telemetry log` shows it.
FO_TELEMETRY_DEBUG=1 prints the event to stderr and sends nothing.
"""

from __future__ import annotations

import datetime as dt
import fcntl
import json
import os
import platform
import re
import subprocess
import sys
import tempfile
import time
import uuid

import fo_config

SCHEMA_VERSION = 4  # consent is recorded per schema version; ANY field change (added or removed) bumps it
LEGACY_FILES = ("telemetry-id.json", "telemetry-period-pending.json", "telemetry-ack-day", "telemetry-ack-month")
LOG_KEEP = 500
AUDIT_LOCK_WAIT_S = 0.5

EVENTS = ("setup", "config", "doctor", "providers", "refresh", "route", "jev_ping", "lessons", "telemetry", "ask")
ENUMS = {
    "os": ("darwin", "linux", "windows", "other"),
    "arch": ("arm64", "x86_64", "other"),
    "host": ("claude-code", "codex", "agy", "unknown", "other"),
    "duration": ("lt1s", "1to10s", "10to60s", "gt60s"),
    "jev_backend": ("typesafe", "openjev", "codiv", "custom", "off"),
    "judge": ("jev", "heuristic", "none"),
    "subcommand": ("recall", "add", "reinforce", "contradict", "retire", "run-new", "run-close", "lint", "init",
                   "show", "set", "get", "path", "on", "off", "status", "log", "none"),
    "mode": ("interactive", "flags", "none"),
    "ask_status": ("ok", "failed", "timeout", "empty", "refused"),
    "ask_vendor": ("claude", "codex", "gemini"),
    "error_kind": ("none", "config", "input", "independence", "jev_auth", "jev_network", "jev_other", "ledger", "other"),
}
# Exactly the collector's patterns (collector/schema.json "strings"): ASCII only, never empty.
STRINGS = {"fo_version": r"[A-Za-z0-9.+-]{1,20}", "python": r"[0-9.]{1,6}"}
INTS = ("tasks", "acted", "escalated", "rejected", "vendors_reachable", "lessons_shown")
BOOLS = ("ok", "independent_review_possible")
VENDOR_KEYS = ("via_claude", "via_codex", "via_gemini")
VIA = ("host", "cli", "gemini-cli", "agy", "none")
PROPS = ("fo_version", "python") + tuple(ENUMS) + INTS + BOOLS + VENDOR_KEYS

CI_VARS = ("CI", "GITHUB_ACTIONS", "GITLAB_CI", "BUILDKITE", "CIRCLECI", "TRAVIS", "TF_BUILD",
           "JENKINS_URL", "TEAMCITY_VERSION", "BITBUCKET_BUILD_NUMBER", "CODEBUILD_BUILD_ID")


def collector() -> dict:
    try:
        with open(os.path.join(fo_config.DATA_DIR, "telemetry.json"), encoding="utf-8") as f:
            c = json.load(f)
    except (OSError, ValueError):
        return {}
    env_url = os.environ.get("FO_TELEMETRY_ENDPOINT", "").strip()
    env_key = os.environ.get("FO_TELEMETRY_KEY", "").strip()
    if env_url:
        c["endpoint"] = env_url
    if env_key:
        c["project_key"] = env_key
    return c


def in_ci() -> bool:
    return any(os.environ.get(v, "").strip() not in ("", "0", "false") for v in CI_VARS)


def status(cfg: dict) -> tuple[bool, str]:
    """(on, reason)."""
    if os.environ.get("DO_NOT_TRACK", "").strip() == "1":
        return False, "DO_NOT_TRACK=1"
    if os.environ.get("FO_TELEMETRY_DISABLED", "").strip() not in ("", "0"):
        return False, "FO_TELEMETRY_DISABLED is set"
    if in_ci():
        return False, "running in CI"
    enabled = (cfg.get("telemetry") or {}).get("enabled")
    if enabled is True and (cfg.get("telemetry") or {}).get("schema") != SCHEMA_VERSION:
        return False, (f"the event fields changed (schema {SCHEMA_VERSION}); read PRIVACY.md, then "
                       f"run {fo_config.cli()} telemetry on to opt in again")
    if enabled is False:
        return False, f"off: the user opted out ({fo_config.cli()} telemetry on to opt in)"
    if enabled is not True:
        return False, f"not opted in ({fo_config.cli()} telemetry on to opt in)"
    c = collector()
    if not c.get("endpoint") or not c.get("project_key"):
        return False, "no collector configured in this build"
    return True, f"on, sending to {c['endpoint']}"


# ---------- legacy files ----------

def remove_legacy_files() -> None:
    """Earlier builds kept an install id and flag files. This build reads none of them; delete them."""
    for name in LEGACY_FILES:
        try:
            os.remove(os.path.join(fo_config.state_dir(), name))
        except OSError:
            pass


# ---------- payload ----------

def _os() -> str:
    s = sys.platform
    return "darwin" if s == "darwin" else "linux" if s.startswith("linux") else "windows" if s.startswith("win") else "other"


def _arch() -> str:
    m = platform.machine().lower()
    return "arm64" if m in ("arm64", "aarch64") else "x86_64" if m in ("x86_64", "amd64") else "other"


def duration_bucket(seconds: float) -> str:
    return "lt1s" if seconds < 1 else "1to10s" if seconds < 10 else "10to60s" if seconds < 60 else "gt60s"


def clean(event: str, props: dict) -> tuple[str, dict]:
    """Drop every key and value that is not on the allowlist. Never raises."""
    if event not in EVENTS:
        event = "telemetry"
    out = {}
    for k, v in (props or {}).items():
        if k in ENUMS:
            if v in ENUMS[k]:
                out[k] = v
        elif k in INTS:
            if isinstance(v, int) and not isinstance(v, bool):
                if 0 <= v <= 10000:  # same rule as the collector: out of range is dropped
                    out[k] = v
        elif k in BOOLS:
            if isinstance(v, bool):
                out[k] = v
        elif k in VENDOR_KEYS:
            if v in VIA:
                out[k] = v
        elif k in STRINGS:
            if isinstance(v, str) and re.fullmatch(STRINGS[k], v):
                out[k] = v
    return event, out


def build(event: str, props: dict, cfg: dict | None = None) -> dict:
    base = {"fo_version": fo_config.VERSION, "python": f"{sys.version_info[0]}.{sys.version_info[1]}",
            "os": _os(), "arch": _arch()}
    base.update(props or {})
    event, safe = clean(event, base)
    c = collector()
    safe.update({"$process_person_profile": False, "$geoip_disable": True,
                 "$lib": "frontier-orchestration", "schema": SCHEMA_VERSION})
    return {"api_key": c.get("project_key", ""), "event": f"fo_{event}", "distinct_id": str(uuid.uuid4()),  # new per event, never stored or reused: events cannot be linked
           
            "properties": safe, "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}


# ---------- audit log ----------

def _log_path() -> str:
    return os.path.join(fo_config.state_dir(), "telemetry-log.jsonl")


def audit(payload) -> dict:
    """Append one event to the local audit log under the audit lock and return it."""
    path = _log_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".lock", "w") as lock:  # one writer at a time: no lost entries
        deadline = time.monotonic() + AUDIT_LOCK_WAIT_S
        while True:  # bounded wait: telemetry must never stall a command
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("telemetry audit log is busy")
                time.sleep(0.02)
        shown = dict(payload, api_key="<public project key>")
        try:
            with open(path, encoding="utf-8") as f:
                lines = f.readlines()[-(LOG_KEEP - 1):]
        except OSError:
            lines = []
        lines.append(json.dumps(shown, sort_keys=True) + "\n")
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tlog-")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.writelines(lines)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    return payload


def read_log(n: int = 20) -> list:
    try:
        with open(_log_path(), encoding="utf-8") as f:
            return [json.loads(x) for x in f.readlines()[-n:] if x.strip()]
    except (OSError, ValueError):
        return []


# ---------- send ----------

def record(event: str, props: dict, cfg: dict) -> str:
    """Send one event if telemetry is on. Never raises, never blocks. Returns what happened."""
    try:
        if os.environ.get("FO_TELEMETRY_DEBUG", "").strip() not in ("", "0"):
            payload = build(event, props, cfg)
            print("telemetry debug (not sent): " + json.dumps(dict(payload, api_key="<key>"), sort_keys=True),
                  file=sys.stderr)
            return "debug"
        remove_legacy_files()
        on, _ = status(cfg)
        if not on:
            return "off"
        payload = audit(build(event, props, cfg))  # raises when the log stays busy: then nothing is sent
        endpoint = collector()["endpoint"]
        sender = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fo_telemetry_send.py")
        child = subprocess.Popen([sys.executable, "-I", sender, endpoint],
                                 stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 start_new_session=True, close_fds=True)
        child.stdin.write(json.dumps(payload).encode())
        child.stdin.close()
        return "sent"
    except Exception:  # telemetry must never break a command
        return "error"
