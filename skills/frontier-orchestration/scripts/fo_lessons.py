"""Learning ledger for frontier-orchestration: one Markdown note per lesson and per run.

The ledger is a plain folder (Obsidian-compatible). Default: ~/.local/share/frontier-orchestration/ledger.
Set ledger.path (and optionally ledger.log) in the config to keep it inside an Obsidian vault.

    <ledger>/
        README.md                     hub (how the loop works)
        reviewer-priors.md            measured hit rates per model x artefact kind
        lessons/fo-L001-<slug>.md     one lesson, with evidence and counter-evidence
        runs/fo-run-<id>.md           one orchestration run (rounds, verdicts, what converged)
        log.md                        newest-first activity log (or ledger.log)

Status rule (computed, never hand-set). A run counts once, and only for its LATEST verdict on a
lesson: reinforcing with a run that had contradicted the lesson moves that run, and the reverse.
    candidate  - 1 distinct confirming run
    confirmed  - >= 2 distinct confirming runs, and fewer distinct runs contradict it
    disputed   - at least as many distinct runs contradict it as confirm it (re-check before use)
    retired    - set by `retire`; kept for history, never recalled; `--unretire` to revive

Usage: fo.py lessons <command> (stdlib only)
    init
    recall     [--tags a,b] [--kind k] [--all] [--limit N]     (default: no limit)
    add        --slug s --rule "..." --why "..." --kind k --tags a,b --run RUN
               --evidence "..." [--source PATH] [--apply "..."] [--force]
    reinforce  ID --run RUN --evidence "..." [--source PATH] [--unretire]
    contradict ID --run RUN --evidence "..." [--source PATH] [--unretire]
    retire     ID --reason "..." [--run RUN]
    run-new    --id YYYY-MM-DD-project-slug-name --project SLUG --artefact KIND --title "..."
    run-close  RUN
    lint

Free text (rule, why, apply, evidence, reason, title) is folded to one line, so it can never
create a Markdown heading or a fake frontmatter key. Run ids are lower-case ASCII and must match
an existing run note by exact name. Every write is atomic (temp file + fsync + rename, file mode
kept) under one folder lock, and adds an entry at the top of the log.
"""

import argparse
import datetime as dt
import fcntl
import json
import os
import re
import sys
import tempfile

ROOT = LESSONS = RUNS = LOG = ""
LOG_INSIDE = True
LOG_HEADER = "# frontier-orchestration ledger log\n\nNewest entries first.\n\n"


def configure(root: str, log: str) -> None:
    """Point the ledger at a folder. Called by fo.py before any command."""
    global ROOT, LESSONS, RUNS, LOG, LOG_INSIDE
    ROOT = os.path.abspath(os.path.expanduser(root))
    LESSONS = os.path.join(ROOT, "lessons")
    RUNS = os.path.join(ROOT, "runs")
    LOG = os.path.abspath(os.path.expanduser(log))
    LOG_INSIDE = os.path.dirname(LOG) == ROOT
KINDS = ["routing", "reviewer-prior", "review-loop", "fix-design", "verification",
         "estimating", "scheduling", "harness", "release"]
ARTEFACTS = ["estimate-raw", "estimate-verified", "code", "schedule", "content", "other"]
ID_RE = re.compile(r"^FO-L(\d{3,})$")
RUN_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}-[a-z0-9]+(?:-[a-z0-9]+)*", re.ASCII)
LIST_KEYS = ("tags", "runs", "contradicted_by")


class NoteError(Exception):
    pass


def today() -> str:
    return dt.date.today().isoformat()


def one_line(s: str) -> str:
    """Fold any text to one line: no newline, no U+2028/2029/0085, no tab runs.

    A line that would start with '#' is escaped, so it can never become a Markdown heading."""
    out = " ".join((s or "").split())
    return "\\" + out if out.startswith("#") else out


# ---------- frontmatter codec (inline lists written; inline AND block lists read) ----------

def dump_value(v):
    # ensure_ascii keeps U+2028/2029/0085 escaped, so a value is always exactly one line
    if isinstance(v, list):
        return "[" + ", ".join(json.dumps(x, ensure_ascii=True) for x in v) + "]"
    if isinstance(v, int):
        return str(v)
    return json.dumps(v, ensure_ascii=True)


def load_scalar(raw: str):
    raw = raw.strip()
    if raw.startswith("[") and raw.endswith("]"):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            inner = raw[1:-1].strip()
            return [x.strip().strip('"').strip("'") for x in inner.split(",")] if inner else []
    if re.fullmatch(r"-?\d+", raw):
        return int(raw)
    if raw.startswith('"'):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw.strip('"')
    return raw.strip("'")


def read_note(path: str):
    text = open(path, encoding="utf-8-sig").read()
    m = re.match(r"^---\n(.*?)\n---(?:\n|\Z)(.*)$", text, re.S)
    if not m:
        raise NoteError(f"{path}: no frontmatter block")
    meta, key = {}, None
    for line in m.group(1).split("\n"):
        if not line.strip():
            continue
        item = re.match(r"^\s*-\s+(.*)$", line)
        if item and key is not None and isinstance(meta.get(key), list):
            meta[key].append(load_scalar(item.group(1)))
            continue
        kv = re.match(r"^([A-Za-z_][\w-]*):(.*)$", line)
        if not kv:
            raise NoteError(f"{path}: frontmatter line not understood: {line[:60]!r}")
        key, raw = kv.group(1), kv.group(2)
        meta[key] = [] if raw.strip() == "" else load_scalar(raw)
    for k in LIST_KEYS:
        if k in meta and isinstance(meta[k], str):
            meta[k] = [meta[k]] if meta[k] else []
    return meta, m.group(2)


def render(meta: dict, body: str) -> str:
    head = "\n".join(f"{k}: {dump_value(v)}" for k, v in meta.items())
    return f"---\n{head}\n---\n{body}"


def atomic_write(path: str, text: str) -> None:
    path = os.path.realpath(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    mode = os.stat(path).st_mode & 0o7777 if os.path.exists(path) else 0o644
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".fo-")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def require_vault():
    """Make the ledger folder on first use and check that every part of it can be written.

    An explicit log OUTSIDE the ledger must already exist. Raises SystemExit with the reason."""
    if not ROOT:
        raise SystemExit("ledger not configured (internal error: call configure first)")
    for d in (ROOT, LESSONS, RUNS):
        os.makedirs(d, exist_ok=True)
    folders = [ROOT, LESSONS, RUNS]
    log_on = not os.environ.get("FO_NO_LOG")
    if log_on:
        if os.path.lexists(LOG) and not os.path.isfile(LOG):
            raise SystemExit(f"{LOG} is not a regular file; the ledger log must be a file")
        if not os.path.exists(LOG) and not LOG_INSIDE:
            raise SystemExit(f"missing {LOG}; refusing to start a new log in the wrong place "
                             "(create it, or unset ledger.log)")
        if os.path.exists(LOG) and not os.access(LOG, os.R_OK | os.W_OK):
            raise SystemExit(f"{LOG} cannot be read and written by you")
        folders.append(os.path.dirname(os.path.realpath(LOG)))  # its lock file and new versions go here
    for d in folders:
        try:  # a new file only, never an old one
            fd, probe = tempfile.mkstemp(dir=d, prefix=".fo-write-test-")
            os.close(fd)
            os.remove(probe)
        except OSError as e:
            raise SystemExit(f"{d} is not writable: {e.strerror or type(e).__name__}")
    if log_on:
        try:  # every log write takes this lock: it must open now, not after a run note is written
            open(_log_lock_path(), "a").close()
        except OSError as e:
            raise SystemExit(f"{_log_lock_path()} cannot be opened: {e.strerror or type(e).__name__}")
    if log_on and not os.path.exists(LOG):  # inside the ledger (checked above): start it here
        _create_log()


def _log_lock_path() -> str:
    """The log can be shared by several ledgers (ledger.log): its lock sits beside the real file."""
    real = os.path.realpath(LOG)  # one lock per real file, even through a symlink
    return os.path.join(os.path.dirname(real), "." + os.path.basename(real) + ".fo-lock")


def _create_log() -> None:
    """Start the log, but never over one that another process made in the meantime.

    Runs under the same lock as log_entry(), so no writer sees a half-made log. O_EXCL (not a
    hard link or rename) works on every file system, and an existing log is never replaced."""
    with open(_log_lock_path(), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            fd = os.open(LOG, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        except FileExistsError:
            return
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(LOG_HEADER)
            f.flush()
            os.fsync(f.fileno())


class FolderLock:
    def __enter__(self):
        if not ROOT:
            raise SystemExit("ledger not configured (internal error: call configure first)")
        os.makedirs(ROOT, exist_ok=True)
        self.f = open(os.path.join(ROOT, ".fo-lock"), "w")
        fcntl.flock(self.f, fcntl.LOCK_EX)
        try:
            require_vault()  # first-use set-up runs under the lock: two writers cannot race it
        except BaseException:
            self.__exit__()
            raise
        return self

    def __exit__(self, *exc):
        fcntl.flock(self.f, fcntl.LOCK_UN)
        self.f.close()


def log_entry(title: str, lines: list) -> None:
    if os.environ.get("FO_NO_LOG"):  # bulk imports write one summary entry themselves
        return
    entry = f"## [{today()}] learn | {one_line(title)}\n" + "".join(f"- {one_line(l)}\n" for l in lines) + "\n"
    # The log can be shared by several ledgers (ledger.log): lock the log itself, not only the folder.
    with open(_log_lock_path(), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        with open(LOG, encoding="utf-8") as f:
            old = f.read()
        if old.startswith(LOG_HEADER):
            atomic_write(LOG, LOG_HEADER + entry + old[len(LOG_HEADER):])
        else:
            atomic_write(LOG, entry + old)


# ---------- lessons ----------

def lesson_files():
    if not os.path.isdir(LESSONS):
        return []
    return sorted(os.path.join(LESSONS, f) for f in os.listdir(LESSONS)
                  if f.startswith("fo-L") and f.endswith(".md"))


def all_lessons(errors=None):
    out = []
    for p in lesson_files():
        try:
            meta, body = read_note(p)
        except NoteError as e:
            if errors is None:
                print(f"warning: skipped {e}", file=sys.stderr)
            else:
                errors.append(str(e))
            continue
        meta["id"] = str(meta.get("id", ""))
        out.append((p, meta, body))
    return out


def find(lesson_id: str):
    lesson_id = lesson_id.upper()
    hits = [(p, m, b) for p, m, b in all_lessons() if m["id"] == lesson_id]
    if not hits:
        raise SystemExit(f"unknown lesson {lesson_id}")
    if len(hits) > 1:
        raise SystemExit(f"{lesson_id} is in {len(hits)} files (sync conflict copy?): "
                         + ", ".join(os.path.basename(h[0]) for h in hits) + ". Fix that first.")
    return hits[0]


def confirming(meta):
    return set(meta.get("runs", [])) - set(meta.get("contradicted_by", []))


def status_of(meta: dict) -> str:
    if meta.get("status") == "retired":
        return "retired"
    runs = len(confirming(meta))
    contra = len(set(meta.get("contradicted_by", [])))
    if contra and contra >= runs:
        return "disputed"
    return "confirmed" if runs >= 2 else "candidate"


def run_link(run: str) -> str:
    return f"[[fo-run-{run}]]"


def evidence_line(run: str, text: str, source) -> str:
    src = f" (source: `{one_line(source)}`)" if source else ""
    return f"- {today()} {run_link(run)} — {one_line(text)}{src}\n"


def insert_under(body: str, heading: str, line: str) -> str:
    m = re.search(rf"(?m)^## {re.escape(heading)}[ \t]*\n", body)
    if not m:
        return body.rstrip("\n") + f"\n\n## {heading}\n{line}"
    head, tail = body[:m.end()], body[m.end():]
    nxt = re.search(r"(?m)^## ", tail)
    section = tail[:nxt.start()] if nxt else tail
    rest = tail[nxt.start():] if nxt else ""
    kept = section.rstrip("\n")
    return head + (kept + "\n" if kept else "") + line + ("\n" + rest if rest else "")


def ensure_run(run: str) -> None:
    if not RUN_RE.fullmatch(run):
        raise SystemExit(f"bad run id {run!r}: use lower-case YYYY-MM-DD-project-slug-name")
    name = f"fo-run-{run}.md"
    if not os.path.isdir(RUNS) or name not in os.listdir(RUNS):
        raise SystemExit(f"run note {name} does not exist (exact name); create it first with run-new")


def make_slug(s: str) -> str:
    slug = re.sub(r"[^a-z0-9-]+", "-", s.lower()).strip("-")[:60]
    return slug or "lesson"


def cmd_add(a):
    if a.kind not in KINDS:
        raise SystemExit(f"--kind must be one of {KINDS}")
    rule = one_line(a.rule)
    if not rule:
        raise SystemExit("--rule is empty")
    slug = make_slug(a.slug) if make_slug(a.slug) != "lesson" else make_slug(rule)
    with FolderLock():
        ensure_run(a.run)
        dup = similar(rule)
        if dup and not a.force:
            raise SystemExit(f"rule looks like {dup[0]} ({dup[1]:.2f} overlap): reinforce it, or pass --force")
        nums = [int(ID_RE.match(m["id"]).group(1)) for _, m, _ in all_lessons() if ID_RE.match(m["id"])]
        lid = f"FO-L{(max(nums) + 1 if nums else 1):03d}"
        tags = [t.strip() for t in a.tags.split(",") if t.strip()]
        meta = {"type": "lesson", "id": lid, "title": rule, "status": "candidate", "kind": a.kind,
                "tags": ["frontier-orchestration", "lesson", *tags], "rule": rule, "runs": [a.run],
                "contradicted_by": [], "created": today(), "updated": today()}
        body = (f"\n# {lid} — {rule}\n\n## Rule\n{rule}\n\n## Why\n{one_line(a.why)}\n\n"
                f"## How to apply\n{one_line(a.apply) or 'State the concrete trigger and action when this lesson is reinforced.'}\n\n"
                f"## Evidence\n{evidence_line(a.run, a.evidence, a.source)}\n## Counter-evidence\n")
        path = os.path.join(LESSONS, f"fo-{lid[3:]}-{slug}.md")
        atomic_write(path, render(meta, body))
        link_run(a.run, lid, "new")
        log_entry(f"frontier-orchestration lesson {lid} added (candidate)",
                  [f"Rule: {rule}", f"Run: {run_link(a.run)}", f"Location: {os.path.relpath(path, ROOT)}"])
    print(lid, path)


def bump(a, contra: bool):
    with FolderLock():
        ensure_run(a.run)
        p, meta, body = find(a.id)
        if meta.get("status") == "retired":
            if not a.unretire:
                raise SystemExit(f"{meta['id']} is retired; pass --unretire to revive it with this evidence")
            meta["status"] = "candidate"
        before = status_of(meta)
        runs, contra_runs = list(meta.get("runs", [])), list(meta.get("contradicted_by", []))
        note = ""
        if contra:
            if a.run in runs:
                runs.remove(a.run)
                note = " (this run had confirmed it; its verdict is now: contradicts)"
            if a.run not in contra_runs:
                contra_runs.append(a.run)
            body = insert_under(body, "Counter-evidence", evidence_line(a.run, a.evidence + note, a.source))
        else:
            if a.run in contra_runs:
                contra_runs.remove(a.run)
                note = " (this run had contradicted it; its verdict is now: confirms)"
            if a.run not in runs:
                runs.append(a.run)
            body = insert_under(body, "Evidence", evidence_line(a.run, a.evidence + note, a.source))
        meta["runs"], meta["contradicted_by"] = runs, contra_runs
        meta["status"] = status_of(meta)
        meta["updated"] = today()
        atomic_write(p, render(meta, body))
        verb = "contradicted" if contra else "reinforced"
        link_run(a.run, meta["id"], verb)
        after = meta["status"]
        log_entry(f"frontier-orchestration lesson {meta['id']} {verb} ({before} → {after})",
                  [f"Rule: {meta.get('rule')}", f"Run: {run_link(a.run)}", f"Evidence: {a.evidence}"])
    print(meta["id"], before, "->", after)


def cmd_retire(a):
    with FolderLock():
        if a.run:
            ensure_run(a.run)
        p, meta, body = find(a.id)
        meta["status"] = "retired"
        meta["updated"] = today()
        body = insert_under(body, "Counter-evidence", f"- {today()} RETIRED — {one_line(a.reason)}"
                            + (f" ({run_link(a.run)})" if a.run else "") + "\n")
        atomic_write(p, render(meta, body))
        log_entry(f"frontier-orchestration lesson {meta['id']} retired", [f"Reason: {a.reason}"])
    print(meta["id"], "retired")


def cmd_recall(a):
    want = {t.strip() for t in (a.tags or "").split(",") if t.strip()}
    rows, seen = [], {}
    for p, meta, _ in all_lessons():
        if meta["id"] in seen:
            print(f"warning: {meta['id']} in two files ({os.path.basename(seen[meta['id']])}, {os.path.basename(p)})",
                  file=sys.stderr)
        seen[meta["id"]] = p
        st = status_of(meta)
        if st == "retired":
            continue
        if st == "candidate" and not (a.all or want):
            continue
        tags = set(meta.get("tags", []))
        if want and not (want & tags) and meta.get("kind") not in want:
            continue
        if a.kind and meta.get("kind") != a.kind:
            continue
        rank = {"confirmed": 0, "disputed": 1, "candidate": 2}[st]
        rows.append((rank, -len(confirming(meta)), meta["id"], st, p, meta))
    rows.sort(key=lambda r: r[:5])
    shown = rows if not a.limit else rows[: a.limit]
    for _, negn, lid, st, _, meta in shown:
        flag = " ⚠ re-check" if st == "disputed" else ""
        print(f"{lid} [{st} ×{-negn}] ({meta.get('kind')}) {one_line(str(meta.get('rule')))}{flag}")
    if len(shown) < len(rows):
        print(f"({len(rows) - len(shown)} more; use --limit 0 for all)")
    if not rows:
        print("(no lessons match; run with --all, or widen --tags)")
    pri = os.path.join(ROOT, "reviewer-priors.md")
    if os.path.exists(pri):
        print(f"\nReviewer priors: {pri}")


# ---------- runs ----------

def link_run(run: str, lid: str, verb: str) -> None:
    p = os.path.join(RUNS, f"fo-run-{run}.md")
    meta, body = read_note(p)
    body = insert_under(body, "Lessons", f"- [[{lesson_file_stem(lid)}]] {verb}\n")
    meta["updated"] = today()
    atomic_write(p, render(meta, body))


def lesson_file_stem(lid: str) -> str:
    for p, meta, _ in all_lessons():
        if meta["id"] == lid:
            return os.path.basename(p)[:-3]
    return lid


def cmd_run_new(a):
    if not RUN_RE.fullmatch(a.id):
        raise SystemExit("--id must look like YYYY-MM-DD-project-slug-name (lower-case ASCII)")
    if a.artefact not in ARTEFACTS:
        raise SystemExit(f"--artefact must be one of {ARTEFACTS}")
    p = os.path.join(RUNS, f"fo-run-{a.id}.md")
    with FolderLock():
        if os.path.exists(p):
            raise SystemExit(f"exists: {p}")
        title, project = one_line(a.title), make_slug(a.project)
        meta = {"type": "run", "id": a.id, "title": title, "project": project, "artefact": a.artefact,
                "status": "open", "tags": ["frontier-orchestration", "run", project],
                "created": today(), "updated": today()}
        body = (f"\n# {title}\n\nProject: [[{project}]] · artefact: {a.artefact}\n\n"
                "## Routing\n| Role | Model (exact id reached) | Why |\n|---|---|---|\n\n"
                "## Rounds\n| Round | Reviewer | Verdict | Findings | Held | Refuted | Introduced / pre-existing | Fix design given |\n"
                "|---|---|---|---|---|---|---|---|\n\n"
                "## What converged it\n\n## What wasted rounds\n\n## What slipped past every reviewer\n\n"
                "## Priors that were wrong\n\n## Lessons\n")
        atomic_write(p, render(meta, body))
        log_entry(f"frontier-orchestration run {a.id} opened", [f"Title: {title}", f"Location: {os.path.relpath(p, ROOT)}"])
    print(p)


def cmd_run_close(a):
    with FolderLock():
        ensure_run(a.id)
        p = os.path.join(RUNS, f"fo-run-{a.id}.md")
        meta, body = read_note(p)
        meta["status"], meta["updated"] = "closed", today()
        atomic_write(p, render(meta, body))
        log_entry(f"frontier-orchestration run {a.id} closed", [f"Run: {run_link(a.id)}"])
    print(a.id, "closed")


# ---------- init ----------

HUB = """---
type: hub
title: frontier-orchestration ledger
tags: ["frontier-orchestration"]
---

# frontier-orchestration ledger

Lessons earned by orchestrating models, one note per lesson (`lessons/`) and per run (`runs/`).
Write notes only through `fo.py lessons`; it computes each lesson's status from its runs.

- Before a run: `fo.py lessons recall --tags <kind>,<project>` and read `reviewer-priors.md`.
- During a run: `fo.py lessons run-new ...`, then fill the Routing and Rounds tables as you go.
- After a run: answer the retro questions, then `reinforce`, `contradict` or `add`, `run-close`, `lint`.
"""

PRIORS = """---
type: priors
title: reviewer priors
tags: ["frontier-orchestration"]
---

# Reviewer priors

Measured hit rates per model x artefact kind. One row per run. Count a finding as held only after
you opened the cited file. Use the exact model id that answered.

| Date | Run | Reviewer (exact id) | Artefact kind | Findings | Held | Refuted | Notes |
|---|---|---|---|---|---|---|---|
"""


def cmd_init(_a):
    with FolderLock():
        made = []
        for name, text in (("README.md", HUB), ("reviewer-priors.md", PRIORS)):
            p = os.path.join(ROOT, name)
            if not os.path.exists(p):
                atomic_write(p, text)
                made.append(name)
    print(f"ledger: {ROOT}")
    print(f"log:    {LOG}")
    print("created: " + (", ".join(made) if made else "nothing (already there)"))


# ---------- lint ----------

def tokens(s: str) -> set:
    return {w for w in re.findall(r"\w+", s.lower()) if len(w) > 3}


def overlap(r1: str, r2: str) -> float:
    t, u = tokens(r1), tokens(r2)
    return len(t & u) / len(t | u) if t and u else 0.0


def similar(rule: str):
    best = None
    for _, meta, _ in all_lessons():
        if meta.get("status") == "retired":
            continue
        j = overlap(rule, str(meta.get("rule", "")))
        if j >= 0.6 and (best is None or j > best[1]):
            best = (meta["id"], j)
    return best


def cmd_lint(_a):
    errors = []
    ls = all_lessons(errors)
    problems = len(errors)
    for e in errors:
        print(e)
    seen = {}
    run_files = set(os.listdir(RUNS)) if os.path.isdir(RUNS) else set()
    for p, meta, body in ls:
        lid = meta["id"] or "?"
        if not ID_RE.match(lid):
            print(f"{p}: bad id {lid!r}"); problems += 1
        if lid in seen:
            print(f"{p}: duplicate id {lid} (also {seen[lid]})"); problems += 1
        seen[lid] = p
        if not meta.get("rule"):
            print(f"{lid}: no rule"); problems += 1
        if meta.get("kind") not in KINDS:
            print(f"{lid}: bad kind {meta.get('kind')}"); problems += 1
        for k in LIST_KEYS:
            if k in meta and not isinstance(meta[k], list):
                print(f"{lid}: {k} is not a list"); problems += 1
        if not re.search(r"(?m)^## Evidence[ \t]*\n- ", body):
            print(f"{lid}: no evidence line"); problems += 1
        both = set(meta.get("runs", [])) & set(meta.get("contradicted_by", []))
        if both:
            print(f"{lid}: runs both confirm and contradict: {sorted(both)}"); problems += 1
        for r in list(meta.get("runs", [])) + list(meta.get("contradicted_by", [])):
            if not RUN_RE.fullmatch(str(r)):
                print(f"{lid}: bad run id {r!r}"); problems += 1
            elif f"fo-run-{r}.md" not in run_files:
                print(f"{lid}: run note missing for {r}"); problems += 1
        if meta.get("status") != "retired" and meta.get("status") != status_of(meta):
            print(f"{lid}: status {meta.get('status')} but counts say {status_of(meta)}"); problems += 1
    live = [m for _, m, _ in ls if m.get("status") != "retired"]
    for i, m1 in enumerate(live):
        for m2 in live[i + 1:]:
            if overlap(str(m1.get("rule", "")), str(m2.get("rule", ""))) >= 0.6:
                print(f"near-duplicate rules: {m1['id']} / {m2['id']}"); problems += 1
    print(f"{len(ls)} lessons, {problems} problem(s)")
    return 1 if problems else 0


SUBCOMMANDS = {
    "recall": "list the lessons that apply: confirmed first, then disputed, then candidates",
    "add": "add a new lesson, with the run and the evidence behind it",
    "reinforce": "record a run that confirms a lesson",
    "contradict": "record a run that contradicts a lesson",
    "retire": "retire a lesson, with the reason",
    "run-new": "start a run note",
    "run-close": "close a run note",
    "lint": "check every note in the ledger",
    "init": "set up the ledger folder",
}


def parser() -> tuple:
    """(the lessons parser, {subcommand: its parser})."""
    ap = argparse.ArgumentParser(prog="fo.py lessons", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    subs = {name: sub.add_parser(name, help=text, description=text) for name, text in SUBCOMMANDS.items()}
    r = subs["recall"]; r.add_argument("--tags", help="comma list of tags or kinds; any match counts")
    r.add_argument("--kind", help="only lessons of this kind")
    r.add_argument("--all", action="store_true", help="also show candidate lessons (retired ones never show)")
    r.add_argument("--limit", type=lambda s: max(0, int(s)), default=0, help="0 = no limit (default)")
    ad = subs["add"]
    for k in ("slug", "rule", "why", "kind", "tags", "run", "evidence"):
        ad.add_argument(f"--{k}", required=True)
    ad.add_argument("--source"); ad.add_argument("--apply"); ad.add_argument("--force", action="store_true")
    for name in ("reinforce", "contradict"):
        b = subs[name]; b.add_argument("id"); b.add_argument("--run", required=True)
        b.add_argument("--evidence", required=True); b.add_argument("--source")
        b.add_argument("--unretire", action="store_true")
    rt = subs["retire"]; rt.add_argument("id"); rt.add_argument("--reason", required=True); rt.add_argument("--run")
    rn = subs["run-new"]
    for k in ("id", "project", "artefact", "title"):
        rn.add_argument(f"--{k}", required=True)
    subs["run-close"].add_argument("id")
    return ap, subs


def help_text(name: str) -> str:
    """The help of one subcommand. Builds a parser only: no ledger, file or network access."""
    return parser()[1][name].format_help().rstrip()


def main(argv=None) -> int:
    ap, _ = parser()
    a = ap.parse_args(argv)
    try:
        if a.cmd == "recall":
            cmd_recall(a)
        elif a.cmd == "add":
            cmd_add(a)
        elif a.cmd == "reinforce":
            bump(a, contra=False)
        elif a.cmd == "contradict":
            bump(a, contra=True)
        elif a.cmd == "retire":
            cmd_retire(a)
        elif a.cmd == "run-new":
            cmd_run_new(a)
        elif a.cmd == "run-close":
            cmd_run_close(a)
        elif a.cmd == "lint":
            return cmd_lint(a)
        elif a.cmd == "init":
            cmd_init(a)
    except NoteError as e:
        raise SystemExit(f"refused: {e}. Fix the note by hand (inline or block lists only), then retry.")
    return 0
