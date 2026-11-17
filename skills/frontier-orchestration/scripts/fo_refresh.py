"""refresh: show which models are reachable today, without ever blocking a run.

    fo.py refresh                 instant: print the last snapshot; if it is stale, start one
                                  detached worker and return at once (the NEXT call sees new data)
    fo.py refresh --wait          refresh now, in the foreground, then print
    fo.py refresh --upgrade       also update the vendor CLIs (default: config refresh.upgrade_clis)
    fo.py refresh --json          print the snapshot as JSON
    fo.py refresh --ttl-hours 6   change the stale limit (default: config refresh.ttl_hours, 24)

What a refresh reads: CLI versions (claude, codex, gemini, agy), the Codex model cache and
`agy models`. It writes a snapshot and a DRIFT list: models a CLI offers that the roster does not
list, and roster ids no CLI offers any more.

CLI updates are opt-in. When on: `agy update` (skipped while agy runs), `claude update`,
`codex update` (skipped when an .app bundle owns the binary). Drift is information: this script
never edits the roster.
"""

from __future__ import annotations

import datetime as dt
import fcntl
import json
import os
import re
import subprocess
import sys
import tempfile

import fo_config
import fo_providers
import fo_route

SANDBOX = "/usr/bin/sandbox-exec"
# Run agy with the Keychain tool denied (a sandbox profile), so a refresh cannot change
# the agy sign-in state.
NO_KEYCHAIN = ('(version 1) (allow default) (deny process-exec (literal "/usr/bin/security")) '
               '(deny file-read* (literal "/usr/bin/security"))')
EFFORT_SUFFIX = re.compile(r"-(low|medium|high|xhigh|max)$")
EFFORT_ORDER = ["low", "medium", "high", "xhigh", "max", "ultra"]
TOOLS = ("claude", "codex", "gemini", "agy")


def paths() -> dict:
    st = fo_config.state_dir()
    return {"state": st, "snapshot": os.path.join(st, "snapshot.json"),
            "lock": os.path.join(st, "refresh.lock"), "log": os.path.join(st, "refresh.log")}


def now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def run(cmd: list, timeout: int) -> tuple[int, str]:
    env = dict(os.environ, NO_COLOR="1",
               PATH=os.pathsep.join([os.environ.get("PATH", "")] + fo_providers.path_extra()))
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           env=env, stdin=subprocess.DEVNULL)
        return p.returncode, (p.stdout + p.stderr).strip()
    except subprocess.TimeoutExpired:
        return 124, f"timed out after {timeout}s"
    except OSError as e:
        return 127, str(e)


def agy_cmd(agy: str, *args: str, guarded: bool = True) -> list:
    if guarded and os.path.exists(SANDBOX):
        return [SANDBOX, "-p", NO_KEYCHAIN, agy, *args]
    return [agy, *args]


def version_of(path: str) -> str:
    if not path:
        return ""
    _, out = run([path, "--version"], 30)
    for line in reversed(out.splitlines()):
        m = re.search(r"\d+\.\d+[\w.\-]*", line)
        if m:
            return m.group(0)
    return ""


def busy(name: str) -> bool:
    return run(["pgrep", "-x", name], 10)[0] == 0


def update_tool(name: str, path: str) -> str:
    """Update one CLI. Returns a short result string; never raises."""
    if not path:
        return "not installed"
    if name == "gemini":
        return "not updated here (use its own installer)"
    if name == "codex" and ".app/" in os.path.realpath(path):
        return "managed by its app bundle"
    if name == "agy" and busy("agy"):
        return "skipped: an agy process is running"
    code, out = run(agy_cmd(path, "update") if name == "agy" else [path, "update"], 600)
    tail = out.splitlines()[-1][:160] if out else ""
    return ("ok" if code == 0 else f"failed ({code})") + (f": {tail}" if tail else "")


def gemini_models(agy: str, keychain_fallback: bool = False) -> tuple[dict, str]:
    """(gemini base id -> efforts, error). agy runs with the Keychain denied unless the user opted in."""
    if not agy:
        return {}, "agy is not installed"
    code, out = run(agy_cmd(agy, "models"), 90)
    if "gemini-" not in out and keychain_fallback:  # opt-in: refresh.agy_keychain_fallback
        code, out = run(agy_cmd(agy, "models", guarded=False), 90)
    found: dict = {}
    for line in out.splitlines():
        mid = line.split("\t")[0].strip()
        if not re.fullmatch(r"[a-z0-9][a-z0-9.\-]+", mid):
            continue
        m = EFFORT_SUFFIX.search(mid)
        base, effort = (mid[:m.start()], m.group(1)) if m else (mid, None)
        found.setdefault(base, set())
        if effort:
            found[base].add(effort)
    gem = {k: [e for e in EFFORT_ORDER if e in v] for k, v in found.items() if k.startswith("gemini-")}
    err = "" if gem else (f"`agy models` listed no gemini id with the Keychain denied (exit {code}); agy may "
                          "need its file token. Set refresh.agy_keychain_fallback true to also try the "
                          f"Keychain sign-in. Output: {out[-160:]}")
    return gem, err


def codex_models() -> tuple[list, str]:
    home = os.path.expanduser(os.environ.get("CODEX_HOME", "").strip() or "~/.codex")
    cache = os.path.join(home, "models_cache.json")
    try:
        with open(cache, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError) as e:
        return [], f"cannot read {cache}: {e}"
    ids = [m.get("slug") or m.get("id") for m in d.get("models", [])]
    return [i for i in ids if i], str(d.get("fetched_at", ""))


def drift(gem: dict, codex: list, roster: fo_route.Roster) -> list:
    out = []
    roster_ids = set(roster.models)
    for mid, efforts in gem.items():
        if mid not in roster_ids:
            out.append(f"roster: agy offers {mid} (effort {', '.join(efforts) or 'n/a'}) but the roster does not list it")
    for mid in codex:
        if mid.startswith("gpt-") and mid not in roster_ids:
            out.append(f"roster: the Codex model cache lists {mid} but the roster does not")
    for mid, m in roster.models.items():
        v = m.get("vendor")
        if v == "gemini" and gem and mid not in gem:
            out.append(f"roster: {mid} is in the roster but agy does not offer it")
        if v == "codex" and codex and mid not in codex:
            out.append(f"roster: {mid} is in the roster but the Codex model cache does not list it")
    return out


def build_snapshot(cfg: dict, upgrade: bool) -> dict:
    tool_paths = {n: fo_providers.which(n) for n in TOOLS}
    before = {n: version_of(p) for n, p in tool_paths.items()}
    results = {n: (update_tool(n, p) if upgrade else "not run (upgrade off)") for n, p in tool_paths.items()}
    after = {n: version_of(p) for n, p in tool_paths.items()} if upgrade else before
    gem, gem_err = gemini_models(tool_paths["agy"], bool(cfg["refresh"].get("agy_keychain_fallback")))
    codex, codex_note = codex_models()
    roster = fo_route.Roster(fo_config.roster(cfg))
    return {
        "checked_at": now().isoformat(timespec="seconds"),
        "upgraded": upgrade,
        "roster_checked": roster.checked,
        "tools": {n: {"path": tool_paths[n], "version": after[n], "was": before[n], "update": results[n]}
                  for n in tool_paths},
        "gemini": gem, "gemini_error": gem_err,
        "codex": codex, "codex_cache_fetched_at": codex_note,
        "drift": drift(gem, codex, roster),
    }


def write_snapshot(snap: dict) -> None:
    p = paths()
    os.makedirs(p["state"], exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=p["state"], prefix=".snapshot-")
    with os.fdopen(fd, "w") as f:
        json.dump(snap, f, indent=1)
    os.replace(tmp, p["snapshot"])


def refresh(cfg: dict, upgrade: bool) -> bool:
    """Run one refresh under the lock. False when another refresh holds it."""
    p = paths()
    os.makedirs(p["state"], exist_ok=True)
    with open(p["lock"], "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return False
        write_snapshot(build_snapshot(cfg, upgrade))
    return True


def running() -> bool:
    p = paths()
    os.makedirs(p["state"], exist_ok=True)
    try:
        with open(p["lock"], "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return False
    except OSError:
        return True


def spawn_worker(entry: str, upgrade: bool) -> None:
    p = paths()
    os.makedirs(p["state"], exist_ok=True)
    args = [sys.executable, "-I", entry, "refresh", "--worker"] + (["--upgrade"] if upgrade else ["--no-upgrade"])
    with open(p["log"], "a") as log:
        log.write(f"\n== {now().isoformat(timespec='seconds')} worker start\n")
        log.flush()
        subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                         start_new_session=True, close_fds=True)


def load() -> dict:
    try:
        with open(paths()["snapshot"]) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def age_hours(snap: dict) -> float:
    try:
        return (now() - dt.datetime.fromisoformat(snap["checked_at"])).total_seconds() / 3600
    except (KeyError, ValueError, TypeError):
        return float("inf")


def show(snap: dict, note: str) -> str:
    if not snap:
        return f"no snapshot yet. {note}"
    lines = [f"snapshot {snap['checked_at']} ({age_hours(snap):.1f} h old). {note}",
             f"roster checked {snap.get('roster_checked', '?')}"]
    for name, t in snap["tools"].items():
        moved = f" (was {t['was']})" if t["was"] and t["was"] != t["version"] else ""
        lines.append(f"  {name:13} {t['version'] or '-':22}{moved} update: {t['update']}")
    for mid, efforts in snap["gemini"].items():
        lines.append(f"  gemini  {mid:22} effort {', '.join(efforts) or 'n/a'}")
    if snap.get("gemini_error"):
        lines.append(f"  gemini  {snap['gemini_error']}")
    for mid in snap["codex"]:
        if mid.startswith("gpt-"):
            lines.append(f"  codex   {mid}")
    lines.append("drift: none" if not snap["drift"] else "drift:")
    lines += [f"  - {d}" for d in snap["drift"]]
    return "\n".join(lines)


def main(cfg: dict, args: list, entry: str) -> int:
    upgrade = bool(cfg["refresh"].get("upgrade_clis"))
    if "--upgrade" in args:
        upgrade = True
    if "--no-upgrade" in args:
        upgrade = False
    ttl = float(cfg["refresh"].get("ttl_hours") or 24)
    if "--ttl-hours" in args:
        i = args.index("--ttl-hours")
        try:
            ttl = float(args[i + 1])
            if not 0 < ttl < float("inf"):
                raise ValueError
        except (IndexError, ValueError):
            raise SystemExit("--ttl-hours needs a number of hours above 0") from None
    if "--worker" in args:
        ok = refresh(cfg, upgrade)
        print(f"{now().isoformat(timespec='seconds')} worker " + ("done" if ok else "skipped: another refresh holds the lock"))
        return 0
    if "--wait" in args:
        note = "refreshed now." if refresh(cfg, upgrade) else "another refresh is running; this is the last snapshot."
    else:
        snap = load()
        if running():
            note = "a background refresh is running; call again for new data."
        elif age_hours(snap) > ttl:
            spawn_worker(entry, upgrade)
            note = f"stale (limit {ttl:g} h): started a background refresh; call again for new data."
        else:
            note = "fresh."
    snap = load()
    print(json.dumps(snap, indent=1) if "--json" in args else show(snap, note))
    return 0
