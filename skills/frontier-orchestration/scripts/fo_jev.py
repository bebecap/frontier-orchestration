"""System One client: TypeSafe Jev, OpenJev (self-hosted or Codiv), or any compatible server.

Every backend speaks one wire API: POST {base_url}/v1/systemone with
{"state", "model", "questions"} and returns {"answers", "usage"}. Criteria shapes:
    choice -> criteria is a MAP of option -> rubric
    noul   -> criteria is {"true": "...", "false": "..."}
    score  -> criteria is an ORDERED ARRAY of level descriptions
"""

from __future__ import annotations

import http.client
import json
import math
import urllib.parse
import os
import stat
import threading
import time
import subprocess
import sys
import urllib.error
import urllib.request

import fo_config

USER_AGENT = "frontier-orchestration"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never follow a redirect: urllib would resend the Authorization header to the new origin."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, f"redirect to another URL refused ({code})",
                                     headers, fp)


LOOPBACK = ("127.0.0.1", "localhost", "::1")


def direct(url: str) -> bool:
    """True when a request to `url` must bypass every proxy.

    Plain http (to this machine, or to a trusted private server with jev.allow_insecure_http)
    never goes through a proxy: http_proxy, all_proxy or a system proxy would otherwise get the
    prompt and the key in plaintext, and a proxy cannot reach this machine's loopback anyway.
    https to another host may use the proxy from the environment as usual: the proxy only sees
    the host name (CONNECT), and TLS runs end to end."""
    try:
        u = urllib.parse.urlsplit(url)
        host = (u.hostname or "").lower()
    except ValueError:
        return True
    return u.scheme != "https" or host in LOOPBACK


def opener(url: str) -> urllib.request.OpenerDirector:
    """An opener for `url` that never follows a redirect, and bypasses proxies as direct() says."""
    if direct(url):
        return urllib.request.build_opener(_NoRedirect, urllib.request.ProxyHandler({}))
    return urllib.request.build_opener(_NoRedirect)  # the default ProxyHandler reads the environment now


# A server's error body is never shown: it is untrusted and may echo the key in any encoding.
# The message is built only from the status code and local text.
STATUS_HINT = {
    400: "the server refused the request shape (check criteria shapes and the model id)",
    401: "the API key was refused: check the key for this backend",
    403: "the API key has no access to this model or endpoint",
    404: "no System One endpoint at this URL, or an unknown model",
    413: "the request is too large for this backend",
    422: "a field has the wrong shape (choice: map, noul: true/false map, score: list)",
    429: "rate limit: wait and retry, or lower the request rate",
    529: "the server is overloaded: retry later",
}


def redact(text: str, key: str) -> str:
    """Remove the API key from text built locally (defence in depth)."""
    return text.replace(key, "<redacted>") if key else text


def http_message(base_url: str, code: int) -> str:
    if 300 <= code < 400:
        hint = "the server answered with a redirect, which is refused so the key never moves"
    else:
        hint = STATUS_HINT.get(code, "server error" if code >= 500 else "request refused")
    return f"HTTP {code} from {base_url}: {hint}"


class JevError(Exception):
    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = status


def api_key(settings: dict, required: bool | None = None) -> str:
    """Key from (1) the env var, (2) the macOS Keychain, (3) a mode-0600 file. Never the config.

    jev.key_source ("env", "keychain" or "file"), set by the installer, uses only that source."""
    required = settings.get("key_required", False) if required is None else required
    source = settings.get("key_source")
    env_name = settings.get("api_key_env") or ""
    if env_name and source in (None, "env") and os.environ.get(env_name, "").strip():
        return os.environ[env_name].strip()
    service = settings.get("keychain_service")
    if service and source in (None, "keychain") and sys.platform == "darwin" and os.path.exists("/usr/bin/security"):
        try:
            out = subprocess.run(["/usr/bin/security", "find-generic-password", "-s", service, "-w"],
                                 capture_output=True, text=True, timeout=15).stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            out = ""
        if out:
            return out
    path = settings.get("api_key_file") if source in (None, "file") else None
    if path:
        path = os.path.expanduser(path)
        try:
            mode = os.stat(path).st_mode
        except OSError as e:
            raise JevError(f"api_key_file {path}: {e}") from e
        if mode & (stat.S_IRWXG | stat.S_IRWXO):
            raise JevError(f"api_key_file {path} is readable by others; run: chmod 600 {path}")
        try:
            with open(path, encoding="utf-8") as f:
                key = f.read().strip()
        except OSError as e:  # for example owned by another user
            raise JevError(f"api_key_file {path} cannot be read: {e.strerror or type(e).__name__}") from None
        if key:
            return key
    if required:
        raise JevError(f"no API key for Jev backend {settings.get('backend')!r}. Store it once in one of "
                       "these ways (never paste it into a chat):\n" + "\n".join("  - " + r for r in key_routes(settings)))
    return ""


def key_routes(settings: dict) -> list:
    """The ways that work on this platform to give fo.py a key, as runnable commands."""
    fo = fo_config.cli()
    env_name = settings.get("api_key_env") or "FO_JEV_API_KEY"
    service = settings.get("keychain_service") or env_name
    mac = sys.platform == "darwin" and os.path.exists("/usr/bin/security")
    routes = []
    if mac:
        routes.append(f'the login Keychain: security add-generic-password -U -a "$USER" -s {service} -w '
                      "(macOS asks for the key, so it stays out of shell history)")
    routes.append(f"{fo} install, then choose \"Add a new key\" "
                  + ("(it goes to the login Keychain)" if mac else "(it writes a key file with mode 600)"))
    routes.append(f"a key file: put only the key in a file, run chmod 600 on it, then run "
                  f"{fo} config set jev.api_key_file /path/to/the/file jev.key_source file")
    routes.append(f"an environment variable: export {env_name}=... in your shell profile")
    source = settings.get("key_source")
    if source:
        routes.append(f"note: jev.key_source is {source}, so only that source is read; change it with "
                      f"{fo} config set jev.key_source env|keychain|file")
    return routes


MAX_REPLY_BYTES = 8 * 1024 * 1024


def check_transport(settings: dict) -> None:
    """HTTPS, or plain HTTP only to this machine, unless the user explicitly allows it."""
    try:
        u = urllib.parse.urlsplit(settings.get("base_url") or "")
        u.port  # noqa: B018 - raises ValueError for a port that is not a number from 0 to 65535
    except ValueError:
        raise JevError("refusing to send the request: the URL cannot be read (check the brackets "
                       "and the port)") from None
    if u.username is not None or u.password is not None:
        raise JevError("refusing to send the request: the URL holds a user name or password "
                       "(put the key in jev.api_key_env, the Keychain or jev.api_key_file instead)")
    if u.scheme == "https" and u.hostname:
        return
    if u.scheme == "http" and u.hostname and (u.hostname in LOOPBACK or settings.get("allow_insecure_http")):
        return
    raise JevError(f"refusing to send the request to {u.scheme or '?'}://{u.hostname or '?'}: use https, "
                   "or http only to 127.0.0.1/localhost, or set jev.allow_insecure_http true for a "
                   "trusted private network (the key and task attributes then travel in plaintext)")


def ask(settings: dict, state, questions: dict, key: str | None = None) -> dict:
    if settings.get("backend") == "off" or not settings.get("base_url"):
        raise JevError("Jev is off (jev.backend = off); use the heuristic router")
    check_transport(settings)
    key = api_key(settings) if key is None else key
    body = json.dumps({"state": state, "model": settings.get("model") or "jev-latest",
                       "questions": questions}).encode()
    headers = {"Content-Type": "application/json", "User-Agent": USER_AGENT}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    if settings.get("long_context"):
        headers["TypeSafe-Danger-Zone-Long-Context"] = "true"
    req = urllib.request.Request(settings["base_url"] + "/v1/systemone", data=body, method="POST",
                                 headers=headers)
    try:
        timeout = float(settings.get("timeout_s") or 180)
        if not 0 < timeout <= 3600:
            raise ValueError
    except (TypeError, ValueError):
        raise JevError("jev.timeout_s must be a number of seconds between 0 and 3600") from None
    result: dict = {}

    def transfer() -> None:
        try:
            with opener(req.full_url).open(req, timeout=timeout) as resp:
                chunks, size = [], 0
                while True:
                    chunk = resp.read1(65536)  # returns what has arrived; read() would wait for 64 KB
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > MAX_REPLY_BYTES:
                        raise JevError(f"the reply from {settings['base_url']} is larger than "
                                       f"{MAX_REPLY_BYTES} bytes")
                    chunks.append(chunk)
            result["body"] = b"".join(chunks)
        except BaseException as e:  # handed to the caller's thread below
            result["error"] = e

    # The whole exchange (connect, headers, chunk framing, body) runs in a daemon thread, and the
    # caller waits at most `timeout`: a server that trickles bytes cannot hold the command.
    worker = threading.Thread(target=transfer, daemon=True)
    worker.start()
    worker.join(timeout)
    try:
        if worker.is_alive():
            raise JevError(f"{settings['base_url']} took longer than {timeout:g} s to answer")
        if "error" in result:
            raise result["error"]
        data = json.loads(result["body"])
    except JevError:
        raise
    except urllib.error.HTTPError as e:
        e.close()  # the body is never read or shown
        raise JevError(redact(http_message(settings["base_url"], e.code), key), e.code) from None
    except (urllib.error.URLError, http.client.HTTPException, OSError, ValueError) as e:
        # Only the error class: its text can contain server bytes (for example a bad status line).
        raise JevError(redact(f"cannot reach {settings['base_url']} or read its reply "
                              f"({type(e).__name__})", key)) from None
    if not isinstance(data, dict) or "answers" not in data:
        raise JevError(f"unexpected reply from {settings['base_url']}: no 'answers' field")
    return data


def ping(settings: dict) -> dict:
    """One tiny Noul. Proves the key, the URL and the model, not the quality."""
    data = ask(settings, "The sky is usually blue on a clear day.",
               {"ok": {"type": "noul", "instructions": "Is the statement in `state` true?"}})
    ans = data["answers"].get("ok") if isinstance(data["answers"], dict) else None
    p = ans.get("noul") if isinstance(ans, dict) else None
    if isinstance(p, bool) or not isinstance(p, (int, float)) or not 0 <= p <= 1 or not math.isfinite(p):
        raise JevError("the backend answered, but not with a probability: check the model id")
    # The model shown is the one configured here. Server text is never printed: a backend could
    # reflect the key in any field, and a character check cannot prove where text came from.
    model = settings.get("model")
    tokens = (data.get("usage") or {}).get("input_tokens") if isinstance(data.get("usage"), dict) else None
    return {"model": model, "p_true": round(float(p), 3),
            "input_tokens": tokens if isinstance(tokens, int) and not isinstance(tokens, bool) else None}
