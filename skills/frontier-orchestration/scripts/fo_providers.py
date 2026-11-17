"""Providers: which vendors are reachable here, whether their CLI is signed in, and a read-only
runner (`fo.py ask`) that calls each vendor's own CLI. No bridge, no MCP server, no daemon.

    vendor   label         transports (auto order)      read-only mode
    claude   Claude        host (Agent tool), cli       claude -p --permission-mode plan
    codex    Codex         cli                          codex exec --sandbox read-only
    gemini   Antigravity   agy, gemini-cli              agy --mode plan / gemini --approval-mode plan

Commands are built as argument lists and run without a shell. The prompt goes on stdin where
the CLI supports it. The command line cannot be replaced from the config: an override could not
be checked reliably for write flags, so `providers.<vendor>.command` was removed.
"""

from __future__ import annotations

import fcntl
import json
import os
import shutil
import signal
import stat
import subprocess
import time

import fo_config

LABELS = {"claude": "Claude", "codex": "Codex", "gemini": "Antigravity (Gemini)"}
CLI = {"claude": "claude", "codex": "codex", "gemini": "agy"}
ASK_TIMEOUT_S = 1800


def path_extra() -> list:
    """Folders searched after PATH (agent hosts often start tools with a short PATH).
    FO_PATH_EXTRA (colon-separated; empty for none) replaces the default list."""
    env = os.environ.get("FO_PATH_EXTRA")
    if env is not None:
        return [p for p in env.split(os.pathsep) if p]
    return [os.path.join(os.path.expanduser("~"), ".local", "bin"), "/opt/homebrew/bin", "/usr/local/bin"]


def which(name: str) -> str:
    path = os.pathsep.join([os.environ.get("PATH", "")] + path_extra())
    return shutil.which(name, path=path) or ""


def detect_host() -> str:
    forced = os.environ.get("FO_HOST", "").strip().lower()
    if forced:
        return forced
    if os.environ.get("CLAUDECODE") == "1":
        return "claude-code"
    if os.environ.get("CODEX_SANDBOX") or os.environ.get("CODEX_THREAD_ID"):
        return "codex"
    if any(k.startswith("ANTIGRAVITY") for k in os.environ):
        return "agy"
    return "unknown"


def _quiet(cmd: list, timeout: int = 30) -> tuple[int, str]:
    env = dict(os.environ, NO_COLOR="1",
               PATH=os.pathsep.join([os.environ.get("PATH", "")] + path_extra()))
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env,
                           stdin=subprocess.DEVNULL)
        return p.returncode, (p.stdout + p.stderr)
    except subprocess.TimeoutExpired:
        return 124, ""
    except OSError:
        return 127, ""


def session(vendor: str, via: str | None = None) -> dict:
    """{installed, signed_in, how_to_sign_in, detail}. Reads status only; no model call.

    via: the transport that will be used (from resolve()); for Gemini it picks agy or the gemini
    CLI. Without it, the auto order applies (agy first). detail never holds names, emails or tokens."""
    if vendor == "gemini" and via == "gemini-cli":
        exe = which("gemini")
    else:
        exe = which(CLI[vendor])
        if vendor == "gemini" and via != "agy" and not exe and which("gemini"):
            exe = which("gemini")
    out = {"installed": bool(exe), "signed_in": False, "how_to_sign_in": "", "detail": "", "exe": exe}
    if not exe:
        out["how_to_sign_in"] = {"claude": "install Claude Code: https://claude.com/claude-code",
                                 "codex": "install Codex: https://developers.openai.com/codex",
                                 "gemini": "install Antigravity (agy): https://antigravity.google"}[vendor]
        out["detail"] = "not installed"
        return out
    if vendor == "claude":
        code, text = _quiet([exe, "auth", "status"])
        try:  # wrappers may print lines before or after the JSON: read only the first object
            obj, _ = json.JSONDecoder().raw_decode(text[text.index("{"):])
            out["signed_in"] = bool(obj.get("loggedIn"))
        except (ValueError, AttributeError):
            out["signed_in"] = False
        out["how_to_sign_in"] = "claude auth login"
    elif vendor == "codex":
        code, text = _quiet([exe, "login", "status"])
        low = text.lower()
        out["signed_in"] = code == 0 and "logged in" in low and "not logged in" not in low
        out["how_to_sign_in"] = "codex login"
    elif os.path.basename(exe) == "agy":
        code, text = _quiet([exe, "models"], timeout=60)  # lists models only for a signed-in user
        out["signed_in"] = code == 0 and "gemini-" in text
        out["how_to_sign_in"] = "agy   (sign in in the browser, then type /exit)"
    else:  # Google's gemini CLI: no status command; an API key or prior sign-in is needed
        out["signed_in"] = bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
                                or _gemini_oauth_ok())
        out["how_to_sign_in"] = "set GEMINI_API_KEY, or run gemini once to sign in"
    out["detail"] = "signed in" if out["signed_in"] else "not signed in"
    return out


HOST_SESSION = "host session, assumed signed in"


def sessions(resolved: dict) -> dict:
    """vendor -> session() for every enabled vendor: the one sign-in check that `providers` and
    `doctor --live` share. Claude reached through the host (fo.py runs inside Claude Code) is the
    session that runs this command, so it is assumed signed in and no CLI status is read."""
    out = {}
    for vendor, r in resolved.items():
        if not r["enabled"]:
            continue
        if r["via"] == "host":
            out[vendor] = {"installed": True, "signed_in": True, "how_to_sign_in": "", "detail": HOST_SESSION,
                           "exe": ""}
        else:
            out[vendor] = session(vendor, r["via"] or None)
    return out


def _gemini_oauth_ok() -> bool:
    """The gemini CLI's OAuth cache holds a usable token (a regular, readable JSON file with one)."""
    path = os.path.join(os.path.expanduser("~"), ".gemini", "oauth_creds.json")
    try:
        if not os.path.isfile(path) or os.path.islink(path):
            return False
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and any(isinstance(data.get(k), str) and data.get(k).strip()
                                          for k in ("refresh_token", "access_token"))


def available(vendor: str, host: str) -> dict:
    """transport -> evidence, for the transports present on this machine."""
    out = {}
    if vendor == "claude" and host == "claude-code":
        out["host"] = "running inside Claude Code"
    if vendor in ("claude", "codex") and which(CLI[vendor]):
        out["cli"] = which(CLI[vendor])
    if vendor == "gemini":
        if which("agy"):
            out["agy"] = which("agy")
        if which("gemini"):
            out["gemini-cli"] = which("gemini")
    return out


def resolve(cfg: dict, host: str | None = None) -> dict:
    """vendor -> {enabled, via, available, how, default_model, note}."""
    host = host or detect_host()
    out = {}
    for vendor in fo_config.VENDORS:
        p = (cfg.get("providers") or {}).get(vendor, {})
        enabled = p.get("enabled", True) is not False
        avail = available(vendor, host)
        want = p.get("via", "auto")
        via, note = "", ""
        if not enabled:
            note = "disabled in config"
        elif want != "auto":
            if want in avail:
                via = want
            else:
                note = f"configured via {want}, but it was not found on this machine: not reachable"
        else:
            via = next((t for t in fo_config.TRANSPORTS[vendor] if t in avail), "")
            if not via:
                note = f"no transport found: install the {LABELS[vendor]} CLI, or disable this provider"
        shown = via or (want if want != "auto" else "")
        how = describe(vendor, shown, p.get("model"))
        out[vendor] = {"enabled": enabled, "via": via, "available": avail, "how": how,
                       "default_model": p.get("model") or "the CLI's default", "note": note}
    return out


def enabled_vendors(resolved: dict) -> set:
    """Vendors that are enabled AND have a transport on this machine."""
    return {v for v, r in resolved.items() if r["enabled"] and r["via"]}


def configured_vendors(cfg: dict) -> set:
    """Vendors the user allows (config), whether or not a transport was detected."""
    return {v for v in fo_config.VENDORS if (cfg.get("providers") or {}).get(v, {}).get("enabled", True) is not False}


# ---------- read-only runner ----------

class AskError(Exception):
    pass


def argv(vendor: str, via: str, *, model: str | None, effort: str | None, cwd: str, out_file: str,
         prompt: str) -> tuple[list, str | None, bool]:
    """(argv, stdin text or None, output_is_stdout). Read-only flags are always present: there is no
    way to replace this command line (an override could not be checked reliably, so it is gone)."""
    if vendor == "claude" and via == "cli":
        a = [which("claude") or "claude", "-p", "--permission-mode", "plan"]
        if model:
            a += ["--model", model]
        return a, prompt, True
    if vendor == "codex" and via == "cli":
        a = [which("codex") or "codex", "exec", "--sandbox", "read-only", "--skip-git-repo-check",
             "--ephemeral", "-C", cwd, "-o", out_file]
        if model:
            a += ["-m", model]
        if effort:
            a += ["-c", f"model_reasoning_effort={effort}"]
        return a + ["-"], prompt, False
    if vendor == "gemini" and via == "agy":
        a = [which("agy") or "agy", "--print", prompt, "--output-format", "text", "--mode", "plan",
             "--add-dir", cwd, "--print-timeout", "0s"]
        if model:
            a += ["--model", model]
        if effort:
            a += ["--effort", effort]
        return a, None, True
    if vendor == "gemini" and via == "gemini-cli":
        a = [which("gemini") or "gemini", "--approval-mode", "plan"]
        if model:
            a += ["--model", model]
        return a, prompt, True
    if vendor == "claude" and via == "host":
        raise AskError("you are Claude Code: use the Agent tool for Claude (same vendor, not an independent review)")
    raise AskError(f"no runnable transport for {vendor} ({via or 'none'})")


def describe(vendor: str, via: str, model: str | None) -> str:
    if vendor == "claude" and via == "host":
        return "Agent tool (model alias fable | opus | sonnet | haiku). Same vendor as you: never the independent review."
    if not via:
        return ""
    m = f" --model {model}" if model else ""
    fo = fo_config.cli()
    return {("claude", "cli"): f"{fo} ask claude   (claude -p --permission-mode plan{m})",
            ("codex", "cli"): f"{fo} ask codex    (codex exec --sandbox read-only{m})",
            ("gemini", "agy"): f"{fo} ask gemini   (agy --print --mode plan{m})",
            ("gemini", "gemini-cli"): f"{fo} ask gemini   (gemini --approval-mode plan{m})"}.get((vendor, via), "")


def run(cfg: dict, vendor: str, prompt: str, *, model: str | None = None, effort: str | None = None,
        cwd: str | None = None, out_file: str, timeout: int = ASK_TIMEOUT_S, host: str | None = None) -> dict:
    """Run one read-only ask. Writes the answer to out_file. Never raises on a CLI failure."""
    res = resolve(cfg, host).get(vendor)
    if not res:
        raise AskError(f"unknown vendor {vendor!r}: use claude, codex or gemini")
    if not res["enabled"]:
        raise AskError(f"{vendor} is disabled in the config")
    via = res["via"]
    p = (cfg.get("providers") or {}).get(vendor, {})
    model = model or p.get("model")
    cwd = os.path.abspath(cwd or os.getcwd())
    out_file = os.path.abspath(out_file)
    cmd, stdin_text, from_stdout = argv(vendor, via, model=model, effort=effort, cwd=cwd,
                                        out_file=out_file, prompt=prompt)
    os.makedirs(os.path.dirname(out_file), mode=0o700, exist_ok=True)
    t0 = time.monotonic()

    def failed(why: str) -> dict:
        return {"vendor": vendor, "via": via, "model": model or "default", "status": "failed",
                "exit_code": None, "seconds": round(time.monotonic() - t0, 1), "out_file": out_file,
                "stderr_tail": why}

    fd, why = _claim(out_file)  # this run owns the answer path until it returns; nobody else may touch it
    if fd is None:
        return failed(why)
    try:
        env = dict(os.environ, PATH=os.pathsep.join([os.environ.get("PATH", "")] + path_extra()))
        # Hold Ctrl-C and SIGTERM while the child is being created, so an interruption cannot land
        # between "child exists" and "cleanup is armed". The child itself starts with them unblocked.
        stops = {signal.SIGINT, signal.SIGTERM}
        old_mask = signal.pthread_sigmask(signal.SIG_BLOCK, stops)
        try:
            proc = subprocess.Popen(cmd, cwd=cwd, env=env,
                                    stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                    start_new_session=True, preexec_fn=_child_signals)
        except BaseException as e:  # missing CLI, bad override, vanished folder, or anything else at start
            try:
                _drop(out_file, fd)  # ours and still empty; never raises
            finally:
                signal.pthread_sigmask(signal.SIG_SETMASK, old_mask)  # always, even if cleanup failed
            if not isinstance(e, OSError):
                raise
            return failed(f"could not start {os.path.basename(cmd[0])}: {e.strerror or type(e).__name__}")
        try:
            try:
                signal.pthread_sigmask(signal.SIG_SETMASK, old_mask)  # a held signal fires here, inside the cleanup
                stdout, stderr = proc.communicate(stdin_text, timeout=timeout)
                status = "ok" if proc.returncode == 0 else "failed"
            except BaseException as e:  # Ctrl-C, SIGTERM or a timeout: the vendor CLI must not outlive us
                if not isinstance(e, subprocess.TimeoutExpired):
                    _kill_group(proc.pid, signal.SIGTERM)
                    _kill_group(proc.pid, signal.SIGKILL)
                    raise

                _kill_group(proc.pid, signal.SIGTERM)
                stdout, stderr = "", ""
                try:
                    stdout, stderr = proc.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
                finally:  # always, even on Ctrl-C here: a child may ignore SIGTERM after the leader exits
                    _kill_group(proc.pid, signal.SIGKILL)
                if proc.poll() is None:
                    stdout, stderr = proc.communicate()
                status = "timeout"
        except BaseException:
            _drop(out_file, fd)  # interrupted before an answer (also during the timeout grace): no empty file left
            raise
        if from_stdout and _is_ours(out_file, fd):
            os.lseek(fd, 0, os.SEEK_SET)
            os.ftruncate(fd, 0)
            data = (stdout or "").encode("utf-8")
            while data:
                data = data[os.write(fd, data):]
        if os.path.isfile(out_file) and not os.path.islink(out_file):
            os.chmod(out_file, 0o600)  # answers can hold private code: owner only (a CLI may have replaced the file)
        answered = os.path.isfile(out_file) and os.path.getsize(out_file) > 0
        if status == "ok" and not answered:
            status = "empty"  # a review with no output has no verdict
        if not answered and os.path.lexists(out_file):
            os.remove(out_file)  # no answer, no file: nothing can be mistaken for a verdict
        return {"vendor": vendor, "via": via, "model": model or "default", "status": status,
                "exit_code": proc.returncode, "seconds": round(time.monotonic() - t0, 1),
                "out_file": out_file, "stderr_tail": (stderr or "")[-400:] if status != "ok" else ""}
    finally:
        os.close(fd)  # releases the claim


def _claim(path: str):
    """Open the answer path and hold an exclusive lock on it for the whole run.

    Returns (fd, "") or (None, why). A path another run holds is left exactly as it is. A stale
    answer from an earlier run is emptied, so it can never count as this run's verdict."""
    try:
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0), 0o600)
    except OSError as e:
        return None, f"could not open the answer file: {e.strerror or type(e).__name__}; nothing was changed"
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            why = "the answer path is not a regular file; nothing was changed"
        elif st.st_nlink > 1:  # emptying it would also empty every other name for the same file
            why = "the answer file has other hard links; nothing was changed (use another --out)"
        elif not _is_ours(path, fd):  # an earlier run removed it between our open and our lock
            why = "the answer file changed while this run started; nothing was changed (run again)"
        else:
            os.ftruncate(fd, 0)
            os.fchmod(fd, 0o600)  # private before the CLI writes it
            return fd, ""
    except BlockingIOError:
        why = "another run is using this answer file (--out); nothing was changed"
    except OSError as e:
        why = f"could not lock the answer file: {e.strerror or type(e).__name__}; nothing was changed"
    os.close(fd)
    return None, why


def _is_ours(path: str, fd: int) -> bool:
    """The path still names the file this run locked."""
    try:
        cur, mine = os.lstat(path), os.fstat(fd)
    except OSError:
        return False
    return (cur.st_dev, cur.st_ino) == (mine.st_dev, mine.st_ino)


def _drop(path: str, fd: int) -> None:
    """Remove this run's answer file if it is still empty. Never raises."""
    try:
        if _is_ours(path, fd) and os.fstat(fd).st_size == 0:
            os.remove(path)
    except OSError:
        pass


def _child_signals() -> None:
    """In the child, before exec: default handlers, then unblock. A Ctrl-C that arrived in between
    simply stops the child; it never runs Python's handler inside the child."""
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    signal.pthread_sigmask(signal.SIG_UNBLOCK, {signal.SIGINT, signal.SIGTERM})


def _kill_group(pid: int, sig) -> None:
    try:
        os.killpg(pid, sig)
    except (ProcessLookupError, PermissionError):
        pass


def render(resolved: dict, host: str, sessions: dict | None = None) -> str:
    lines = [f"host: {host}"]
    for vendor, r in resolved.items():
        state = "off" if not r["enabled"] else (r["via"] or "UNREACHABLE")
        signed = f"  [{sessions[vendor]['detail']}]" if sessions and vendor in sessions else ""
        lines.append(f"  {LABELS[vendor]:22} {state:11} model: {r['default_model']}{signed}")
        if sessions and vendor in sessions and r["enabled"] and not sessions[vendor]["signed_in"]:
            lines.append(f"      sign in: {sessions[vendor]['how_to_sign_in']}")
        if r["how"]:
            lines.append(f"      how: {r['how']}")
        if r["note"]:
            lines.append(f"      note: {r['note']}")
    reach = sorted(enabled_vendors(resolved))
    lines.append(f"independent review possible: {'yes' if len(reach) >= 2 else 'NO'} "
                 f"(vendors reachable: {', '.join(reach) or 'none'})")
    return "\n".join(lines)
