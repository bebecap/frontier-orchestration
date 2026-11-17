"""A small Clack-style terminal UI, standard library only.

    ┌  frontier-orchestration
    │
    ◆  Which decision engine should route your tasks?
    │  ● TypeSafe Jev (hosted, API key)
    │  ○ OpenJev on this machine
    └

Arrow keys or j/k move, space toggles in a multi-select, enter confirms, Ctrl-C cancels.
Without a terminal (pipes, CI, an agent's shell) every prompt falls back to plain numbered
questions on stdin, so the same flow can be scripted. NO_COLOR turns colors off.
"""

from __future__ import annotations

import getpass
import os
import sys
import threading
import time

S_BAR, S_START, S_END = "│", "┌", "└"
S_ACTIVE, S_DONE, S_CANCEL, S_WARN, S_ERR = "◆", "◇", "■", "▲", "■"
S_RADIO_ON, S_RADIO_OFF = "●", "○"
S_CHECK_ON, S_CHECK_OFF = "◼", "◻"
SPIN = "◒◐◓◑"


class Cancelled(Exception):
    """The user pressed Ctrl-C or Escape."""


def _color() -> bool:
    return not os.environ.get("NO_COLOR") and sys.stdout.isatty()


def _c(code: str, s: str) -> str:
    return f"\033[{code}m{s}\033[0m" if _color() else s


def dim(s): return _c("2", s)
def bold(s): return _c("1", s)
def cyan(s): return _c("36", s)
def green(s): return _c("32", s)
def yellow(s): return _c("33", s)
def red(s): return _c("31", s)
def inverse(s): return _c("7", s)


def interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty() and os.environ.get("FO_TUI", "") != "plain"


# ---------- keys ----------

class _Raw:
    def __enter__(self):
        import termios
        import tty
        self.fd = sys.stdin.fileno()
        self.old = termios.tcgetattr(self.fd)
        tty.setraw(self.fd)
        sys.stdout.write("\033[?25l")  # hide cursor
        sys.stdout.flush()
        return self

    def __exit__(self, *exc):
        import termios
        termios.tcsetattr(self.fd, termios.TCSADRAIN, self.old)
        sys.stdout.write("\033[?25h")
        sys.stdout.flush()


def _read_tty(n: int) -> str:
    return os.read(sys.stdin.fileno(), n).decode("utf-8", "replace")


def _more_input(wait: float) -> bool:
    import select as _select
    return bool(_select.select([sys.stdin], [], [], wait)[0])


def _key(read=None) -> str:
    read = read or _read_tty
    ch = read(1)
    if ch == "\x1b":
        if read is _read_tty and not _more_input(0.05):
            return "esc"  # a lone Escape: do not wait for a second key
        nxt = read(2)
        return {"[A": "up", "[B": "down", "[C": "right", "[D": "left"}.get(nxt, "esc")
    if ch in ("\r", "\n"):
        return "enter"
    if ch == "\x03":
        return "ctrl-c"
    if ch == " ":
        return "space"
    if ch in ("\x7f", "\x08"):
        return "backspace"
    return ch


def _render(lines: list, prev: int) -> int:
    out = []
    if prev:
        out.append(f"\033[{prev}A")
    for line in lines:
        out.append("\r\033[2K" + line + "\r\n")
    sys.stdout.write("".join(out))
    sys.stdout.flush()
    return len(lines)


def _clear(prev: int) -> None:
    if prev:
        sys.stdout.write(f"\033[{prev}A" + "\r\033[2K\r\n" * prev + f"\033[{prev}A")
        sys.stdout.flush()


def _print(line: str = "") -> None:
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


# ---------- frame ----------

def intro(title: str) -> None:
    _print(dim(S_START) + "  " + inverse(f" {title} "))
    _print(dim(S_BAR))


def outro(message: str) -> None:
    _print(dim(S_BAR))
    _print(dim(S_END) + "  " + message)
    _print()


def cancel(message: str = "Cancelled. The config was not saved; anything already installed or stored stays.") -> None:
    _print(dim(S_END) + "  " + red(message))
    _print()


def _cols() -> int:
    import shutil
    return max(40, shutil.get_terminal_size((90, 24)).columns)


def fit(s: str, width: int) -> str:
    """Shorten to width with an ellipsis in the middle (keeps the end of paths readable)."""
    if len(s) <= width:
        return s
    keep = max(4, width - 1)
    return s[: keep // 3] + "…" + s[-(keep - keep // 3):]


def short_path(path: str) -> str:
    home = os.path.expanduser("~")
    return "~" + path[len(home):] if path.startswith(home + os.sep) or path == home else path


def note(title: str, lines: list) -> None:
    room = _cols() - 7
    lines = [fit(x, room) for x in lines]
    width = min(room, max([len(title) + 2] + [len(x) for x in lines])) + 2
    _print(green(S_DONE) + "  " + title + " " + dim("─" * max(1, width - len(title) - 1) + "╮"))
    for x in lines:
        _print(dim(S_BAR) + "  " + x + " " * (width - len(x)) + dim("│"))
    _print(dim("├" + "─" * (width + 2) + "╯"))
    _print(dim(S_BAR))


def info(message: str) -> None:
    _print(cyan("●") + "  " + message)
    _print(dim(S_BAR))


def warn(message: str) -> None:
    _print(yellow(S_WARN) + "  " + message)
    _print(dim(S_BAR))


def error(message: str) -> None:
    _print(red(S_ERR) + "  " + message)
    _print(dim(S_BAR))


def _done(message: str, answer: str) -> None:
    _print(green(S_DONE) + "  " + message)
    _print(dim(S_BAR) + "  " + dim(answer))
    _print(dim(S_BAR))


# ---------- prompts ----------

def select(message: str, options: list, initial: int = 0, read=None) -> str:
    """options: [(value, label, hint)]. Returns the chosen value."""
    if not interactive() and read is None:
        return _plain_select(message, options, initial)
    i, prev = max(0, min(initial, len(options) - 1)), 0
    with _Raw() if read is None else _Null():
        while True:
            lines = [cyan(S_ACTIVE) + "  " + bold(message)]
            for n, (_, label, hint) in enumerate(options):
                on = n == i
                mark = green(S_RADIO_ON) if on else dim(S_RADIO_OFF)
                text = label if on else dim(label)
                lines.append(cyan(S_BAR) + "  " + mark + " " + text + (dim(f"  ({hint})") if hint and on else ""))
            lines.append(cyan(S_END))
            prev = _render(lines, prev) if read is None else len(lines)
            k = _key(read)
            if k in ("up", "k"):
                i = (i - 1) % len(options)
            elif k in ("down", "j"):
                i = (i + 1) % len(options)
            elif k == "enter":
                break
            elif k in ("ctrl-c", "esc"):
                if read is None:
                    _clear(prev)
                raise Cancelled()
    if read is None:
        _clear(prev)
    _done(message, options[i][1])
    return options[i][0]


def multiselect(message: str, options: list, initial: set | None = None, required: bool = True,
                read=None) -> list:
    """options: [(value, label, hint)]. Returns the chosen values, in option order."""
    if not interactive() and read is None:
        return _plain_multiselect(message, options, initial or set(), required)
    chosen = set(initial or set())
    i, prev, warning = 0, 0, ""
    with _Raw() if read is None else _Null():
        while True:
            lines = [cyan(S_ACTIVE) + "  " + bold(message) + dim("  space to toggle, enter to confirm")]
            for n, (value, label, hint) in enumerate(options):
                on = value in chosen
                mark = green(S_CHECK_ON) if on else dim(S_CHECK_OFF)
                text = label if n == i else dim(label)
                lines.append(cyan(S_BAR) + "  " + mark + " " + text + (dim(f"  ({hint})") if hint else ""))
            lines.append(yellow(S_END + "  " + warning) if warning else cyan(S_END))
            prev = _render(lines, prev) if read is None else len(lines)
            k = _key(read)
            warning = ""
            if k in ("up", "k"):
                i = (i - 1) % len(options)
            elif k in ("down", "j"):
                i = (i + 1) % len(options)
            elif k == "space":
                v = options[i][0]
                chosen.symmetric_difference_update({v})
            elif k == "a":
                chosen = set() if len(chosen) == len(options) else {o[0] for o in options}
            elif k == "enter":
                if required and not chosen:
                    warning = "Choose at least one."
                    continue
                break
            elif k in ("ctrl-c", "esc"):
                if read is None:
                    _clear(prev)
                raise Cancelled()
    if read is None:
        _clear(prev)
    picked = [o for o in options if o[0] in chosen]
    _done(message, ", ".join(o[1] for o in picked) or "none")
    return [o[0] for o in picked]


def confirm(message: str, default: bool = False, read=None) -> bool:
    opts = [(True, "Yes", ""), (False, "No", "")]
    return select(message, opts, 0 if default else 1, read=read)


def text(message: str, default: str = "", placeholder: str = "", validate=None) -> str:
    while True:
        if interactive():
            _print(cyan(S_ACTIVE) + "  " + bold(message))
            hint = fit(default or placeholder, _cols() - 10)
            raw = input(cyan(S_BAR) + "  " + (dim(f"({hint}) ") if hint else ""))
        else:
            hint = f" [{default}]" if default else ""
            raw = _readline(f"{message}{hint}: ")
        value = raw.strip() or default
        problem = validate(value) if validate else None
        if problem:
            (warn if interactive() else _print)(f"{problem}")
            continue
        if interactive():
            sys.stdout.write("\033[2A\r\033[2K")
            _done(message, fit(value, _cols() - 6) or "(empty)")
        return value


def password(message: str) -> str:
    """Hidden input. The value is never echoed or logged."""
    if interactive():
        _print(cyan(S_ACTIVE) + "  " + bold(message))
        value = getpass.getpass(cyan(S_BAR) + "  ")
        sys.stdout.write("\033[2A\r\033[2K")
        _done(message, "•" * min(len(value), 12) if value else "(empty)")
        return value.strip()
    return getpass.getpass(f"{message}: ").strip()


class spinner:
    """with spinner("Checking the key") as s: ...; s.stop("Key works") or s.fail("...")."""

    def __init__(self, message: str):
        self.message, self.final = message, None
        self._stop = threading.Event()
        self._thread = None

    def __enter__(self):
        if interactive():
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
        return self

    def _run(self):
        n = 0
        while not self._stop.is_set():
            sys.stdout.write("\r\033[2K" + cyan(SPIN[n % len(SPIN)]) + "  " + self.message + dim(" …"))
            sys.stdout.flush()
            n += 1
            time.sleep(0.09)

    def update(self, message: str) -> None:
        self.message = message

    def stop(self, message: str | None = None) -> None:
        self.final = ("ok", message or self.message)

    def fail(self, message: str) -> None:
        self.final = ("fail", message)

    def __exit__(self, exc_type, *rest):
        self._stop.set()
        if self._thread:
            self._thread.join()
            sys.stdout.write("\r\033[2K")
        kind, msg = self.final or (("fail", self.message) if exc_type else ("ok", self.message))
        mark = green(S_DONE) if kind == "ok" else red(S_ERR)
        _print(mark + "  " + msg)
        _print(dim(S_BAR))
        return False


class _Null:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


# ---------- plain fallback (no terminal) ----------

def _readline(prompt: str) -> str:
    sys.stdout.write(prompt)
    sys.stdout.flush()
    line = sys.stdin.readline()
    if not line:
        raise Cancelled()
    return line.rstrip("\n")


def _plain_select(message: str, options: list, initial: int) -> str:
    _print(message)
    for n, (_, label, hint) in enumerate(options, 1):
        _print(f"  {n}. {label}" + (f"  ({hint})" if hint else ""))
    while True:
        raw = _readline(f"choose 1-{len(options)} [{initial + 1}]: ").strip() or str(initial + 1)
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return options[int(raw) - 1][0]
        _print("Type one of the numbers.")


def _plain_multiselect(message: str, options: list, initial: set, required: bool) -> list:
    _print(message)
    for n, (value, label, hint) in enumerate(options, 1):
        _print(f"  {n}. {label}" + (f"  ({hint})" if hint else ""))
    default = ",".join(str(n) for n, o in enumerate(options, 1) if o[0] in initial)
    while True:
        raw = _readline(f"choose numbers, comma separated [{default}]: ").strip() or default
        try:
            picks = sorted({int(x) for x in raw.split(",") if x.strip()})
        except ValueError:
            picks = []
        if all(1 <= p <= len(options) for p in picks) and (picks or not required):
            return [options[p - 1][0] for p in picks]
        _print("Type one or more of the numbers, for example 1,3.")
