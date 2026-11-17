"""Drive `fo.py install` end to end, answering each prompt by its text.

A fixed list of answers breaks as soon as one prompt is skipped or added (for example when
OpenJev is already running and "OpenJev is not answering yet." never appears). This driver
reads the installer's plain-mode output, finds the question in front of each prompt, and
answers with the first rule whose pattern matches it. A prompt that no rule matches stops the
run (exit 3): an e2e test must never answer a question it did not expect.

    python3 -I tests/e2e/drive_installer.py --fo skills/frontier-orchestration/scripts/fo.py \
        --answer 'Which decision engine=2' --answer 'OpenJev is not running here=1' \
        --answer 'Run these steps now=1' --answer 'Start it now=1' \
        --answer "Which providers=1,2" --answer 'Share anonymous=2' --answer 'learning ledger=' \
        --timeout 7200 --transcript /tmp/install.log

Exit code: the installer's own, or 3 (unexpected prompt), or 4 (timeout). The last line of
stdout is a JSON summary: {"exit", "seconds", "prompts": [{"question", "answer"}]}.
Stdlib only; runs on Python 3.9+.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import select
import signal
import subprocess
import sys
import time

PROMPT = re.compile(r"(choose [^\n]*\]: |[^\n]*: )$")
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
QUIET_S = 0.4  # a prompt is waiting when output stops right after it


def parse_rule(text: str):
    if "=" not in text:
        raise SystemExit(f"--answer needs PATTERN=ANSWER, got {text!r}")
    pat, ans = text.split("=", 1)
    return re.compile(pat, re.I), ans


def question_for(lines: list, prompt_line: str) -> str:
    """The question a prompt belongs to: the line before the numbered options, or the prompt itself."""
    if prompt_line.startswith("choose "):
        for i in range(len(lines) - 1, -1, -1):
            if re.match(r"\s+1\. ", lines[i]):
                return lines[i - 1].strip() if i > 0 else ""
        return ""
    return prompt_line.rstrip(": ").strip()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--fo", required=True, help="path to fo.py")
    ap.add_argument("--python", default=sys.executable, help="interpreter that runs fo.py")
    ap.add_argument("--answer", action="append", default=[], help="PATTERN=ANSWER (regex, case-insensitive)")
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--transcript", default="")
    a = ap.parse_args(argv)
    rules = [parse_rule(r) for r in a.answer]
    env = dict(os.environ, FO_TUI="plain", PYTHONUNBUFFERED="1")
    proc = subprocess.Popen([a.python, "-I", a.fo, "install"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, env=env, start_new_session=True)
    out = open(a.transcript, "w", encoding="utf-8") if a.transcript else None
    buf, answered, code, t0 = "", [], None, time.monotonic()
    last_out = time.monotonic()
    fd = proc.stdout.fileno()
    try:
        while True:
            if time.monotonic() - t0 > a.timeout:
                code = 4
                break
            ready, _, _ = select.select([fd], [], [], 0.1)
            if ready:
                chunk = os.read(fd, 4096)
                if not chunk:
                    code = proc.wait()
                    break
                text = ANSI.sub("", chunk.decode("utf-8", "replace"))
                buf += text
                last_out = time.monotonic()
                if out:
                    out.write(text)
                    out.flush()
                continue
            tail = buf.rsplit("\n", 1)[-1]
            if tail and PROMPT.search(tail) and time.monotonic() - last_out >= QUIET_S:
                lines = buf.split("\n")[:-1][-40:]
                q = question_for(lines, tail)
                rule = next((ans for pat, ans in rules if pat.search(q)), None)
                if rule is None:
                    sys.stderr.write(f"unexpected prompt: {q!r} ({tail.strip()!r})\n")
                    code = 3
                    break
                answered.append({"question": q, "answer": rule})
                proc.stdin.write((rule + "\n").encode())
                proc.stdin.flush()
                if out:
                    out.write(rule + "\n")
                buf += "\n"
    finally:
        if code in (3, 4) and proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
                proc.wait(10)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                os.killpg(proc.pid, signal.SIGKILL)
        if out:
            out.close()
    print(json.dumps({"exit": code, "seconds": round(time.monotonic() - t0, 1), "prompts": answered}))
    return code if code is not None else 1


if __name__ == "__main__":
    sys.exit(main())
