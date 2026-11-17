"""Detached sender for one telemetry event. Imports nothing from this package.

Run by fo_telemetry.record as: python3 -I fo_telemetry_send.py <endpoint>, with the JSON event
on stdin. It exits 0 whatever happens, after at most a 3 second wait (a wall-clock alarm, not only a
socket timeout). It posts only to an
https:// URL, or to http:// on an exact loopback host (for tests), and never follows a redirect.
A loopback http:// post never goes through a proxy (http_proxy, all_proxy or a system proxy);
an https:// post may use the proxy from the environment, as any https client does.
"""

import os
import signal
import sys
import urllib.error
import urllib.parse
import urllib.request

TIMEOUT_S = 3
LOOPBACK = ("127.0.0.1", "localhost", "::1")


def allowed(endpoint: str) -> bool:
    try:
        u = urllib.parse.urlsplit(endpoint)
    except ValueError:
        return False
    if not u.hostname or u.username or u.password:
        return False
    if u.scheme == "https":
        return True
    return u.scheme == "http" and u.hostname in LOOPBACK


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, "redirect refused", headers, fp)


def main() -> int:
    try:
        endpoint = sys.argv[1]
        body = sys.stdin.buffer.read(65536)
        if not allowed(endpoint):
            return 0
        req = urllib.request.Request(endpoint, data=body, method="POST",
                                     headers={"Content-Type": "application/json",
                                              "User-Agent": "frontier-orchestration-telemetry"})
        handlers = [_NoRedirect]
        u = urllib.parse.urlsplit(endpoint)
        if u.scheme != "https" or u.hostname in LOOPBACK:  # plain http or loopback: never through a proxy
            handlers.append(urllib.request.ProxyHandler({}))
        urllib.request.build_opener(*handlers).open(req, timeout=TIMEOUT_S).close()  # status only
    except Exception:
        pass
    return 0


def arm_deadline() -> None:
    """Hard wall-clock limit for the whole process (a socket timeout alone can be outlasted)."""
    if hasattr(signal, "alarm"):
        signal.signal(signal.SIGALRM, lambda *a: os._exit(0))
        signal.alarm(TIMEOUT_S)


if __name__ == "__main__":
    arm_deadline()
    sys.exit(main())
