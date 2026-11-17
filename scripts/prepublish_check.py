#!/usr/bin/env python3
"""Release gate: fail if a git history must not be published.

Run it before you make a repository public or push a release:

    python3 -I scripts/prepublish_check.py [--ref REF] [--repo PATH] [--denylist PATH]
                                           [--allow-email ADDRESS]

It reads every commit reachable from REF (default HEAD), every tag that points into that
history, and every notes ref (refs/notes/*, with its own history), and fails (exit 1) when:

  a. an author, committer or tagger email is not a GitHub noreply address (any local part,
     including the 12345+name form, at users.noreply.github.com) or the Anthropic noreply
     address that the Co-Authored-By trailer uses. This includes the tagger of a tag that a
     merge commit carries (mergetag), and an identity line that cannot be read. The owner may
     publish under one chosen address: pass it explicitly with --allow-email ADDRESS (repeat
     for each address). It is an exact match that ignores ASCII case only: no wildcard, no
     pattern, no other address on the same domain. A value that is not one plain ASCII address
     (one @, no whitespace, no * or ?) is refused (exit 2). It allows the address only as an
     author, committer or tagger email: in a message or in a file it is still an email address
     (rule b, d). Without the option the rule is unchanged;
  b. a commit, tag or notes message has a Claude-Session trailer (in any case), or a message or
     an identity name contains a deny-listed word or a SECRET_PATTERNS match (an allowed identity
     email in a message is not counted). A message is read in its declared encoding, as UTF-8
     and as Latin-1;
  c. the name of a ref the gate walks has a deny-listed word or a SECRET_PATTERNS match: every
     branch, tag, remote-tracking branch or other ref that points into the history (also
     through a tag), and every notes ref;
  d. any path in any reachable commit (both sides of a rename, and deleted files) or the content
     of any file (except the files in ALLOW and the deny list) contains a deny-listed word or a
     SECRET_PATTERNS match, or the private deny list itself was ever committed. Content is
     scanned as bytes: as UTF-8, as Latin-1 (so an ASCII match is never lost to a byte that is
     not UTF-8) and, when it has NUL bytes or a BOM, as UTF-16 and UTF-32. There is no size limit;
  e. a path is a submodule (gitlink) or a regular file that is a Git LFS pointer: their content
     is not in this history, so the gate cannot check it;
  f. a regular file (not a symlink) is an archive (by name: .zip, .gz, .tgz, .tar, .7z, .bz2,
     .xz, .zst, .rar, .jar, .whl and the rest of ARCHIVE_SUFFIXES; or by its first bytes),
     unless you pass its path with --allow-archive after you looked at it: the gate cannot read
     compressed content.

The checker has two layers. blob_facts holds what the bytes alone show and is computed once per
blob. entry_findings holds every rule that reads a path, an extension or a mode (rules d to f,
ALLOW and the review list below) and runs for every tree entry of every commit, so two entries
with the same bytes never share a result. Each line is printed once per path, at the first
commit that has it.

Deny-listed words match after NFKD (fullwidth and other compatibility forms, accents split from
their letters), after every mark (Mn, Mc, Me), format character (Cf) and default-ignorable code
point is removed (zero-width characters, soft hyphens, variation selectors, the combining
grapheme joiner, bidi controls, tag characters, Hangul fillers), without regard to case, and with
common Cyrillic and Greek look-alike letters mapped to Latin.

A file whose content the gate cannot read as text is still scanned as bytes, and the gate also
lists it under "binary files, review by eye" with the reason. It is listed when it has NUL
bytes, or when its first bytes (magic) or its extension say it is one of: PDF, PostScript/EPS,
an image (PNG, JPEG, GIF, WebP, BMP, TIFF, ICO, HEIC/AVIF, JPEG 2000), a font (woff, woff2, ttf,
otf, eot), audio or video, a SQLite database, a pickle, a compiled object (ELF, Mach-O, PE,
WebAssembly, .pyc, .class) or a disk image; the full lists are UNREADABLE_MAGIC and
UNREADABLE_SUFFIXES. A PDF with compressed text has no NUL bytes, so it is found by `%PDF-` or by
`.pdf`. A .svg, .html, .htm, .xhtml, .md or .markdown file is text and
is scanned normally; if it holds a `data:...;base64,` payload of 200 characters or more it is
listed as "embedded base64 data". Office files (docx, xlsx, pptx, odt and others) are zip
archives, so rule f fails them. The list alone does not fail the gate. It cannot tell you what is
inside compressed content: look at each listed file.

It exits 2 when it cannot check: no git repository, an unknown ref, no deny list, a shallow
clone, a partial clone or a grafts file (the history is not complete), or an object it cannot
read. It reads the real objects: replace refs are ignored and missing objects are never fetched.
A release check without the private word list is not a release check, so a missing deny list
is an error unless you pass --no-denylist.

Known limits. It is a check for known patterns (the deny list and SECRET_PATTERNS), not proof
that a history holds nothing private. The content of the files in ALLOW and of a committed deny
list is not scanned (their paths are). Compressed content is not unpacked. Other branches, and
tags that do not point into the history, are not checked, so push REF and its tags by name, never
with --all, --mirror or --tags.

Output names the commit, the kind and the rule. It never prints a matched secret or a
deny-listed word. Standard library only; run it with `python3 -I`.

SECRET_PATTERNS, ALLOW, scan_data and review_reason live here. tests/test_package.py imports them,
so the shipped-file privacy test and this history check use one set of rules.
"""

from __future__ import annotations

import argparse
import codecs
import os
import re
import subprocess
import sys
import threading
import unicodedata
from functools import lru_cache
from typing import NamedTuple

SECRET_PATTERNS = [
    (r"/Users/[A-Za-z0-9._-]+/", "a macOS home path"),
    (r"/home/[a-z][a-z0-9_-]+/", "a Linux home path"),
    # The look-behind makes a match start only where a run of address characters starts, so a
    # long run with no "@" costs linear time, not quadratic. The domain may be in any case.
    (r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]+@(?!(?i:example)\.)[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
     "an email address"),
    (r"\bsk-[A-Za-z0-9_-]{16,}", "an API key"),
    (r"\bts_[A-Za-z0-9]{20,}", "a TypeSafe key"),
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "a private key"),
]
# Files whose content is not scanned: they hold the patterns and their test fixtures.
ALLOW = {"tests/test_package.py", "tests/test_telemetry_ledger.py"}
DENYLIST_NAME = ".privacy-denylist"

# Built from parts so that this file does not match its own email pattern.
ANTHROPIC_NOREPLY = "@".join(("noreply", "anthropic.com"))
GITHUB_NOREPLY = re.compile(r"[A-Za-z0-9._%+-]+@users\.noreply\.github\.com")
SESSION_TRAILER = re.compile(r"(?im)^[ \t]*claude-session[ \t]*:")
IDENTITY_LINE = re.compile(rb"^(author|committer|tagger) (.*?) ?<([^<>]*)> \S+ \S+$")
IDENTITY_ROLE = re.compile(rb"^(author|committer|tagger) ")
LFS_POINTER = b"version https://git-lfs.github.com/spec/"
GITLINK, SYMLINK, DELETED = "160000", "120000", "000000"
BINARY_NOTE = "binary files, review by eye (their bytes were scanned; compressed content cannot be):"

# Formats whose content the gate cannot read as text: listed for review by eye, found by magic
# bytes or by extension, with or without NUL bytes. (name, magic at offset 0)
UNREADABLE_MAGIC = (
    ("PDF", b"%PDF-"), ("PostScript", b"%!PS"), ("EPS binary", b"\xc5\xd0\xd3\xc6"),
    ("PNG", b"\x89PNG\r\n\x1a\n"), ("JPEG", b"\xff\xd8\xff"), ("GIF", b"GIF87a"), ("GIF", b"GIF89a"),
    ("TIFF", b"II*\0"), ("TIFF", b"MM\0*"), ("ICO", b"\0\0\1\0"), ("JPEG 2000", b"\0\0\0\x0cjP  "),
    ("WOFF font", b"wOFF"), ("WOFF2 font", b"wOF2"), ("TrueType font", b"\0\1\0\0"),
    ("OpenType font", b"OTTO"), ("TrueType collection", b"ttcf"),
    ("audio", b"ID3"), ("audio", b"OggS"), ("audio", b"fLaC"), ("video", b"\x1a\x45\xdf\xa3"),
    ("SQLite database", b"SQLite format 3\0"), ("compiled object", b"\x7fELF"),
    ("compiled object", b"\xfe\xed\xfa\xce"), ("compiled object", b"\xfe\xed\xfa\xcf"),
    ("compiled object", b"\xce\xfa\xed\xfe"), ("compiled object", b"\xcf\xfa\xed\xfe"),
    ("compiled object", b"\xca\xfe\xba\xbe"), ("compiled object", b"\0asm"),
)
UNREADABLE_SUFFIXES = (
    ".pdf", ".ps", ".eps", ".ai", ".png", ".jpg", ".jpeg", ".jpe", ".jfif", ".gif", ".webp", ".bmp",
    ".dib", ".tif", ".tiff", ".ico", ".icns", ".cur", ".heic", ".heif", ".avif", ".jp2", ".psd",
    ".woff", ".woff2", ".ttf", ".otf", ".eot", ".ttc",
    ".mp3", ".wav", ".flac", ".ogg", ".oga", ".opus", ".m4a", ".aac", ".aif", ".aiff", ".wma",
    ".mp4", ".m4v", ".mov", ".avi", ".mkv", ".webm", ".wmv", ".flv", ".mpg", ".mpeg", ".3gp",
    ".sqlite", ".sqlite3", ".db", ".pkl", ".pickle", ".npy", ".npz", ".pyc", ".pyo", ".class",
    ".o", ".a", ".so", ".dylib", ".dll", ".exe", ".wasm", ".dmg", ".iso", ".bin")
TEXT_WITH_DATA_SUFFIXES = (".svg", ".html", ".htm", ".xhtml", ".md", ".markdown")
BASE64_PAYLOAD = re.compile(rb"data:[\w/+.\-]*(?:;[\w.\-]+=[^;,\s]*)*;base64,[A-Za-z0-9+/=\s]{200,}")


EXIT_OK, EXIT_FINDINGS, EXIT_CANNOT_CHECK = 0, 1, 2
VERDICT = "this history must not be published; publish the snapshot"
COMPILED = [(re.compile(p), what) for p, what in SECRET_PATTERNS]
# The real objects, never a stand-in: no replace refs, no fetch of a missing object, no prompt.
GIT_ENV = {"GIT_NO_REPLACE_OBJECTS": "1", "GIT_NO_LAZY_FETCH": "1", "GIT_TERMINAL_PROMPT": "0"}

# Common Cyrillic and Greek letters that look like Latin ones, after casefold.
LOOKALIKE = str.maketrans({
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y",
    "х": "x", "і": "i", "ј": "j", "ѕ": "s", "ԁ": "d", "һ": "h",
    "ӏ": "l", "ԛ": "q", "ԝ": "w", "ɡ": "g", "в": "b", "к": "k",
    "м": "m", "н": "h", "т": "t", "ο": "o", "α": "a", "ν": "v",
    "ι": "i", "κ": "k", "ρ": "p", "τ": "t", "υ": "u", "χ": "x",
    "ε": "e", "ı": "i",
})

# Default_Ignorable_Code_Point, from Unicode's DerivedCoreProperties.txt (the standard library has
# no such property): code points that show nothing, so they can hide inside a word.
DEFAULT_IGNORABLE = (
    (0x00AD, 0x00AD), (0x034F, 0x034F), (0x061C, 0x061C), (0x115F, 0x1160), (0x17B4, 0x17B5),
    (0x180B, 0x180F), (0x200B, 0x200F), (0x202A, 0x202E), (0x2060, 0x206F), (0x3164, 0x3164),
    (0xFE00, 0xFE0F), (0xFEFF, 0xFEFF), (0xFFA0, 0xFFA0), (0xFFF0, 0xFFF8), (0x1BCA0, 0x1BCA3),
    (0x1D173, 0x1D17A), (0xE0000, 0xE0FFF),
)
_IGNORABLE = re.compile("[" + "".join(f"\\U{a:08x}-\\U{b:08x}" for a, b in DEFAULT_IGNORABLE) + "]")
MARK_CATEGORIES = frozenset({"Mn", "Mc", "Me", "Cf"})

# Archives: the gate cannot read compressed content, so an archive fails the gate unless its path
# is passed with --allow-archive after a person looked at it. Found by name or by magic bytes.
ARCHIVE_SUFFIXES = (".zip", ".gz", ".tgz", ".tar", ".7z", ".bz2", ".tbz", ".tbz2", ".xz", ".txz",
                    ".zst", ".rar", ".jar", ".whl", ".lz", ".lzma")
ARCHIVE_MAGIC = (b"PK\x03\x04", b"PK\x05\x06", b"\x1f\x8b\x08", b"7z\xbc\xaf\x27\x1c", b"\xfd7zXZ\x00",
                 b"\x28\xb5\x2f\xfd", b"Rar!\x1a\x07")
REF_KINDS = (("refs/heads/", "branch"), ("refs/tags/", "tag"), ("refs/notes/", "notes ref"),
             ("refs/remotes/", "remote-tracking branch"))


class CannotCheck(Exception):
    pass


def allowed_email(email: str) -> bool:
    email = email.strip().lower()
    return email == ANTHROPIC_NOREPLY or GITHUB_NOREPLY.fullmatch(email) is not None


OWNER_EMAIL = re.compile(r"[^@\s*?]+@[^@\s*?]+")


def owner_email(value: str) -> str:
    """An --allow-email value: one plain ASCII address, folded to lower case. No pattern."""
    if not value.isascii() or OWNER_EMAIL.fullmatch(value) is None:
        raise argparse.ArgumentTypeError("not one plain ASCII email address (no wildcard, no pattern)")
    return value.lower()


def identity_email_allowed(email: str, owner_emails: frozenset = frozenset()) -> bool:
    """A noreply address, or exactly one of the owner's chosen addresses (ASCII case ignored).
    The ASCII check comes first: str.lower() maps some non-ASCII letters (the Kelvin sign) to
    ASCII ones, so a look-alike must never be folded into a match."""
    if allowed_email(email):
        return True
    return email.isascii() and email.lower() in owner_emails


def load_denylist(path: str) -> list:
    """Same rules as the shipped-file privacy test: one word per line, skip blanks and # lines."""
    with open(path, encoding="utf-8") as f:
        text = f.read()
    return [w.strip() for w in text.splitlines() if w.strip() and not w.startswith("#")]


def _strip_invisible(text: str) -> str:
    """Remove every mark (Mn, Mc, Me), format character (Cf) and Default_Ignorable_Code_Point."""
    text = _IGNORABLE.sub("", text)
    # No character below U+0300 is a mark or a format character except U+00AD, removed above.
    return "".join(c for c in text if c < "\u0300" or unicodedata.category(c) not in MARK_CATEGORIES)


def fold(text: str) -> str:
    """The form deny-listed words are compared in: NFKD (compatibility forms such as fullwidth
    letters, and accents split from their letters), then every mark, format character and
    default-ignorable code point removed (combining marks of any combining class, variation
    selectors, zero-width characters, soft hyphens, bidi controls, tag characters), then case
    folded and look-alike letters mapped to Latin. Case folding can make new marks (U+0130), so
    the decomposition and removal run once more after it."""
    if text.isascii():
        return text.lower()  # the same result, much faster: ASCII has no marks or format characters
    text = _strip_invisible(unicodedata.normalize("NFKD", text))
    text = _strip_invisible(unicodedata.normalize("NFKD", text.casefold()))
    return text.translate(LOOKALIKE)


@lru_cache(maxsize=None)
def _folded_words(deny: tuple) -> tuple:
    return tuple(fold(w) for w in deny)


def scan_text(text: str, deny: list, skip_allowed_emails: bool = False) -> list:
    """Rule names that `text` breaks. Never the matched text itself."""
    rules = []
    for rx, what in COMPILED:
        for m in rx.finditer(text):
            if skip_allowed_emails and allowed_email(m.group(0)):
                continue
            rules.append(what)
            break
    if deny:
        low = fold(text)
        for i, w in enumerate(_folded_words(tuple(deny)), 1):
            if w and w in low:
                rules.append(f"deny-listed word #{i}")
    return rules


def is_binary(data: bytes) -> bool:
    return b"\0" in data


def text_views(data: bytes, encoding: str = "") -> list:
    """Every way the bytes can be read as text, so no match is lost to the encoding: UTF-8 (bad
    bytes replaced), Latin-1 (every byte is one character, so ASCII always survives), a declared
    encoding, and UTF-16/32 when the bytes have NULs or a byte order mark."""
    views = [data.decode("utf-8", "replace")]
    if not data.isascii():
        views.append(data.decode("latin-1"))
    if encoding:
        try:
            views.append(data.decode(codecs.lookup(encoding).name, "replace"))
        except (LookupError, ValueError):
            pass  # an unknown encoding still has the UTF-8 and Latin-1 views
    if is_binary(data) or data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        for enc in ("utf-16-le", "utf-16-be", "utf-32-le", "utf-32-be"):
            views.append(data.decode(enc, "replace"))
    return views


def scan_data(data: bytes, deny: list, encoding: str = "", skip_allowed_emails: bool = False) -> list:
    """scan_text over every text view of `data`. Each rule at most once, in a stable order."""
    rules = []
    for view in text_views(data, encoding):
        for rule in scan_text(view, deny, skip_allowed_emails):
            if rule not in rules:
                rules.append(rule)
    return rules


def git(repo: str, *args: str, stdin: bytes | None = None) -> bytes:
    out = subprocess.run(["git", "-C", repo, *args], input=stdin, capture_output=True,
                         env={**os.environ, **GIT_ENV})
    if out.returncode != 0:
        raise CannotCheck(f"git {args[0]} failed: {out.stderr.decode('utf-8', 'replace').strip()}")
    return out.stdout


def cat_objects(repo: str, shas: list):
    """Yield (sha, type, content) for each sha, in order, from one `git cat-file --batch`.
    A missing object or a short read is CannotCheck, never a skip."""
    if not shas:
        return
    proc = subprocess.Popen(["git", "-C", repo, "cat-file", "--batch"], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env={**os.environ, **GIT_ENV})

    def feed():
        try:
            for sha in shas:
                proc.stdin.write(sha.encode() + b"\n")
            proc.stdin.close()
        except BrokenPipeError:
            pass

    writer = threading.Thread(target=feed, daemon=True)
    writer.start()
    try:
        for want in shas:
            header = proc.stdout.readline().split()
            if len(header) != 3:
                raise CannotCheck(f"cannot read object {want[:10]} (missing or not readable)")
            sha, kind, size = header[0].decode(), header[1].decode(), int(header[2])
            data = proc.stdout.read(size)
            if len(data) != size or proc.stdout.read(1) != b"\n":
                raise CannotCheck(f"short read of object {want[:10]}")
            yield sha, kind, data
    finally:
        proc.kill()
        writer.join()
        proc.stdout.close()
        proc.wait()


def split_object(data: bytes):
    """Header lines (continuation lines joined to their header) and raw message bytes."""
    head, _, message = data.partition(b"\n\n")
    headers = []
    for line in head.split(b"\n"):
        if line.startswith(b" ") and headers:
            headers[-1] += b"\n" + line[1:]
        else:
            headers.append(line)
    return headers, message


def declared_encoding(headers: list) -> str:
    for h in headers:
        if h.startswith(b"encoding "):
            return h[len(b"encoding "):].decode("ascii", "replace").strip()
    return ""


def check_identities(label: str, kind: str, headers: list, deny: list, findings: list,
                     owner_emails: frozenset = frozenset()):
    for line in headers:
        role = IDENTITY_ROLE.match(line)
        if not role:
            continue
        m = IDENTITY_LINE.match(line)
        if not m:
            findings.append(f"{label} {kind}: {role.group(1).decode()} line cannot be read")
            continue
        role_name, name, email = m.group(1).decode(), m.group(2), m.group(3)
        if not identity_email_allowed(email.decode("utf-8", "replace"), owner_emails):
            findings.append(f"{label} {kind}: {role_name} email is not a noreply address")
        for rule in scan_data(name, deny):
            findings.append(f"{label} {kind}: {role_name} name has {rule}")


def check_message(label: str, kind: str, message: bytes, encoding: str, deny: list, findings: list):
    if any(SESSION_TRAILER.search(v) for v in text_views(message, encoding)):
        findings.append(f"{label} {kind}: Claude-Session trailer")
    for rule in scan_data(message, deny, encoding, skip_allowed_emails=True):
        findings.append(f"{label} {kind}: {rule}")


def check_tag_object(label: str, prefix: str, data: bytes, deny: list, findings: list,
                     owner_emails: frozenset = frozenset()):
    """The identities, name and message of a tag object (also one embedded as a mergetag)."""
    headers, message = split_object(data)
    check_identities(label, prefix + " identity", headers, deny, findings, owner_emails)
    for h in headers:
        if h.startswith(b"tag "):
            for rule in scan_data(h[4:], deny):
                findings.append(f"{label} {prefix} (name withheld): name has {rule}")
    check_message(label, prefix + " message", message, declared_encoding(headers), deny, findings)


def check_commits(repo: str, commits: list, deny: list, findings: list, where: str = "",
                  owner_emails: frozenset = frozenset()):
    for sha, kind, data in cat_objects(repo, commits):
        if kind != "commit":
            raise CannotCheck(f"{sha[:10]} is a {kind}, not a commit")
        headers, message = split_object(data)
        label = where + sha[:10]
        check_identities(label, "identity", headers, deny, findings, owner_emails)
        for h in headers:
            if h.startswith(b"mergetag "):
                check_tag_object(label, "mergetag", h[len(b"mergetag "):], deny, findings, owner_emails)
        check_message(label, "message", message, declared_encoding(headers), deny, findings)


def all_refs(repo: str) -> list:
    """Every ref as (object id, object type, ref name as bytes)."""
    out = git(repo, "for-each-ref", "--format=%(objectname) %(objecttype) %(refname)")
    refs = []
    for ln in out.split(b"\n"):
        if ln:
            sha, kind, name = ln.split(b" ", 2)
            refs.append((sha.decode(), kind.decode(), name))
    return refs


def check_tags(repo: str, refs: list, reachable: set, ref_sha: str, deny: list, findings: list,
               owner_emails: frozenset = frozenset()):
    """Check the tag object of every annotated tag (and tag of a tag) that points into the history,
    under any ref. Returns (the count, a function that says if an object points into it)."""
    pending = [sha for sha, kind, _ in refs if kind == "tag"]
    if ref_sha:
        pending.append(ref_sha)
    tags, seen = {}, set()
    while pending:  # read tag objects, following tags of tags
        batch = [s for s in dict.fromkeys(pending) if s not in seen]
        seen.update(batch)
        pending = []
        for sha, kind, data in cat_objects(repo, batch):
            if kind != "tag":
                continue
            headers, _ = split_object(data)
            target = next((h.split()[1].decode() for h in headers if h.startswith(b"object ")), "")
            tags[sha] = (target, data)
            pending.append(target)

    def points_into(sha: str, depth: int = 0) -> bool:
        if sha in reachable:
            return True
        return sha in tags and depth < 50 and points_into(tags[sha][0], depth + 1)

    checked = 0
    for sha, (target, data) in tags.items():
        if not points_into(target):
            continue
        checked += 1
        check_tag_object(sha[:10], "tag", data, deny, findings, owner_emails)
    return checked, points_into


def check_ref_names(refs: list, points_into, deny: list, findings: list) -> int:
    """Scan the name of every ref the gate walks: each branch, tag, remote-tracking branch or other
    ref that points into the history (also through a tag), and every notes ref. A ref name is
    published with the ref. The name is never printed. Returns the count."""
    checked = 0
    for sha, _, name in refs:
        if not (name.startswith(b"refs/notes/") or points_into(sha)):
            continue
        checked += 1
        text = name.decode("utf-8", "surrogateescape")
        kind = next((k for prefix, k in REF_KINDS if text.startswith(prefix)), "ref")
        for rule in scan_data(name, deny):
            findings.append(f"{sha[:10]} {kind} (name withheld): name has {rule}")
    return checked


def tree_entries(repo: str, commits_oldest_first: list):
    """Every (commit, mode, blob, path) that `git diff-tree --stdin -r -m --root --no-renames`
    lists: each commit against each parent, so every path of every reachable tree shows up at
    least once, deleted paths and both sides of a rename included. Paths are bytes."""
    out = git(repo, "diff-tree", "--stdin", "-r", "-m", "--root", "--no-renames", "-z",
              stdin="\n".join(commits_oldest_first).encode() + b"\n")
    commit, meta = "", None
    for tok in out.split(b"\0"):
        if meta is not None:  # tok is the path for the previous ":meta" entry
            fields = meta.split()
            meta = None
            new_mode, new_blob = fields[1].decode(), fields[3].decode()
            if new_blob.strip("0"):
                yield commit, new_mode, new_blob, tok
            else:  # a deletion: the path still counts; its blob was listed when it was added
                yield commit, DELETED, "", tok
        elif tok.startswith(b":"):
            meta = tok[1:]
        elif tok:
            commit = tok.split()[0].decode()


@lru_cache(maxsize=None)
def _path_rules(path: bytes, deny: tuple) -> tuple:
    return tuple(scan_data(path, list(deny)))


def show_path(path: bytes, deny: list) -> str:
    """The path, unless the path itself breaks a rule."""
    return "(path withheld)" if _path_rules(path, tuple(deny)) else path.decode("utf-8", "replace")


def is_archive(data: bytes) -> bool:
    return data.startswith(ARCHIVE_MAGIC) or data[257:262] == b"ustar" or (
        data.startswith(b"BZh") and data[4:10] == b"1AY&SY")


# THE INVARIANT. The gate has two layers, and only layer 1 may be keyed by a blob id:
#   1. blob_facts(data, deny): facts about the bytes alone (findings in the content, NUL bytes,
#      format by magic bytes, archive magic, Git LFS pointer shape, embedded base64 payload). They
#      do not depend on a path or a mode, so they are computed once per blob and cached by its id.
#   2. entry_findings(path, mode, facts, ...): every rule that looks at a path, an extension or a
#      mode. It is called for every tree entry of every reachable commit, with no cache, so the
#      first entry that holds a blob never decides for another entry that holds the same blob.
# A new rule that reads a path or a mode goes in layer 2. Nothing else may be keyed by a blob id.

class BlobFacts(NamedTuple):
    content_rules: tuple  # scan_data findings in the bytes (rule names, never the matched text)
    has_nul: bool
    magic_format: str     # the unreadable format that the first bytes show, or ""
    archive_magic: bool
    lfs_pointer: bool
    base64_payload: bool  # a data:...;base64, payload of 200 characters or more


def magic_format(data: bytes) -> str:
    """The format the gate cannot read as text that the first bytes of `data` show, or ""."""
    for kind, magic in UNREADABLE_MAGIC:
        if data.startswith(magic):
            return kind
    if data[4:8] == b"ftyp":
        return "image, audio or video (ISO media)"
    if data[:4] == b"RIFF" and data[8:12] in (b"WEBP", b"WAVE", b"AVI "):
        return "image, audio or video (RIFF)"
    if data[:1] == b"\x80" and data[1:2] in (b"\2", b"\3", b"\4", b"\5"):
        return "pickle"
    if data[:2] == b"BM" and data[6:10] == b"\0\0\0\0":
        return "BMP image"
    return ""


def blob_facts(data: bytes, deny: list) -> BlobFacts:
    """Layer 1: facts about the bytes alone. Never reads a path or a mode."""
    return BlobFacts(content_rules=tuple(scan_data(data, deny)), has_nul=is_binary(data),
                     magic_format=magic_format(data), archive_magic=is_archive(data),
                     lfs_pointer=data.startswith(LFS_POINTER),
                     base64_payload=BASE64_PAYLOAD.search(data) is not None)


def entry_review(path: bytes, facts: BlobFacts) -> str:
    """Why a person must look at this entry because the gate cannot read it as text, or "".
    Magic bytes, extension and NUL bytes each decide alone, so a PDF with compressed text (no NUL
    bytes) and a text file named .pdf are both listed. Archives are not here: they fail (rule f)."""
    name = path.decode("utf-8", "surrogateescape").lower()
    if facts.magic_format:
        return facts.magic_format
    if name.endswith(UNREADABLE_SUFFIXES):
        return "binary format by extension"
    if facts.has_nul:
        return "NUL bytes"
    if name.endswith(TEXT_WITH_DATA_SUFFIXES) and facts.base64_payload:
        return "embedded base64 data"
    return ""


def review_reason(path: bytes, data: bytes) -> str:
    """entry_review for a file read from disk (the shipped-file privacy test uses it)."""
    return entry_review(path, blob_facts(data, []))


def entry_findings(path: bytes, mode: str, facts: BlobFacts | None, deny: list,
                   allow_archives: frozenset = frozenset()) -> tuple:
    """Layer 2: (findings, review lines) of one tree entry, without the commit label. Called for
    every entry; `facts` is None for a deletion (mode 000000) and for a gitlink (its id names a
    commit of another repository). A deletion has only the path rules: its blob was checked when
    the path was added."""
    shown = show_path(path, deny)
    findings = [f"file (path withheld): path has {rule}" for rule in _path_rules(path, tuple(deny))]
    if mode == DELETED:
        return findings, []
    if os.path.basename(path) == DENYLIST_NAME.encode():
        return findings + [f"file {shown}: the private deny list is committed"], []
    if mode == GITLINK:
        return findings + [f"file {shown}: a submodule (gitlink) the gate cannot check"], []
    name = path.decode("utf-8", "surrogateescape")
    if path.decode("utf-8", "replace") not in ALLOW:
        findings += [f"file {shown}: {rule}" for rule in facts.content_rules]
    if mode != SYMLINK:  # a symlink holds a target path, never LFS or archive content
        if facts.lfs_pointer:
            findings.append(f"file {shown}: a Git LFS pointer; its content is not in the history")
        if (facts.archive_magic or name.lower().endswith(ARCHIVE_SUFFIXES)) and name not in allow_archives:
            findings.append(f"file {shown}: archive, review by eye (the gate cannot read compressed "
                            "content); after you check it, pass --allow-archive with its path")
    reason = entry_review(path, facts)
    return findings, [f"{shown} ({reason})"] if reason else []


def check_files(repo: str, commits_oldest_first: list, deny: list, findings: list, binaries: list,
                where: str = "", allow_archives: frozenset = frozenset()) -> tuple:
    """Every rule for every tree entry of every commit. Returns (blobs, paths) checked.

    Layer 1 (blob_facts) reads and scans each blob once. Layer 2 (entry_findings) runs for every
    entry. Each line is reported once per path, at the first commit that has it."""
    entries = list(tree_entries(repo, commits_oldest_first))
    wanted = list(dict.fromkeys(blob for _, mode, blob, _ in entries if blob and mode != GITLINK))
    facts = {}  # blob id -> BlobFacts: layer 1's cache, the only map keyed by a blob id
    for sha, kind, data in cat_objects(repo, wanted):
        if kind != "blob":
            raise CannotCheck(f"{sha[:10]} is a {kind}, not a blob")
        facts[sha] = blob_facts(data, deny)
    reported, paths = set(), set()
    for commit, mode, blob, path in entries:
        paths.add(path)
        found, review = entry_findings(path, mode, facts.get(blob) if blob else None, deny, allow_archives)
        label = f"{where}{commit[:10]}"
        for line, out in [(ln, findings) for ln in found] + [(ln, binaries) for ln in review]:
            if (path, line) not in reported:
                reported.add((path, line))
                out.append(f"{label} {line}")
    return len(facts), len(paths)


def complete_history(repo: str):
    """CannotCheck unless the repository has its whole history: no shallow clone, no grafts."""
    if git(repo, "rev-parse", "--is-shallow-repository").strip() == b"true":
        raise CannotCheck("a shallow clone: older commits are missing. Fetch the whole history first.")
    grafts = git(repo, "rev-parse", "--git-path", "info/grafts").decode().strip()
    if os.path.exists(grafts if os.path.isabs(grafts) else os.path.join(repo, grafts)):
        raise CannotCheck("info/grafts rewrites the history that git shows. Remove it first.")
    promisor = subprocess.run(["git", "-C", repo, "config", "--get-regexp",
                               r"^(extensions\.partialclone|remote\..*\.promisor)$"],
                              capture_output=True, env={**os.environ, **GIT_ENV})
    values = [ln.split(None, 1)[1:] for ln in promisor.stdout.decode("utf-8", "replace").splitlines()]
    if any(v and v[0].strip().lower() not in ("", "false", "no", "off", "0") for v in values):
        raise CannotCheck("a partial clone: some objects are not here. Clone the whole history first.")


def run(repo: str, ref: str, deny: list, allow_archives: frozenset = frozenset(),
        owner_emails: frozenset = frozenset()) -> tuple:
    repo = os.path.abspath(repo)
    top = subprocess.run(["git", "-C", repo, "rev-parse", "--git-dir"], capture_output=True)
    if top.returncode != 0:
        raise CannotCheck(f"not a git repository: {repo}")
    complete_history(repo)
    try:
        tip = git(repo, "rev-parse", "--verify", "--quiet", ref + "^{commit}").decode().strip()
    except CannotCheck:
        raise CannotCheck(f"unknown ref: {ref}") from None
    ref_obj = git(repo, "rev-parse", "--verify", "--quiet", ref).decode().strip()
    commits = git(repo, "rev-list", "--reverse", tip).decode().split()
    findings, binaries = [], []
    refs = all_refs(repo)
    check_commits(repo, commits, deny, findings, owner_emails=owner_emails)
    tags, points_into = check_tags(repo, refs, set(commits), ref_obj if ref_obj != tip else "", deny, findings,
                                   owner_emails)
    check_ref_names(refs, points_into, deny, findings)
    blobs, paths = check_files(repo, commits, deny, findings, binaries, allow_archives=allow_archives)
    # Notes refs are their own histories of commits and blobs; a mirror push publishes them.
    notes = [name for _, _, name in refs if name.startswith(b"refs/notes/")]
    for i, name in enumerate(notes, 1):
        shown = name.decode("utf-8", "replace") if not scan_data(name, deny) else f"ref #{i} (name withheld)"
        where = f"notes {shown} "
        ncommits = git(repo, "rev-list", "--reverse", name.decode("utf-8", "surrogateescape")).decode().split()
        check_commits(repo, ncommits, deny, findings, where, owner_emails)
        b, p = check_files(repo, ncommits, deny, findings, binaries, where, allow_archives)
        blobs, paths = blobs + b, paths + p
    return findings, len(commits), tags, blobs, paths, len(notes), binaries


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Fail if a git history must not be published.")
    p.add_argument("--ref", default="HEAD", help="the ref to check (default HEAD)")
    p.add_argument("--repo", default=".", help="the repository (default: the current folder)")
    p.add_argument("--denylist", help=f"the private word list (default <repo>/{DENYLIST_NAME})")
    p.add_argument("--no-denylist", action="store_true",
                   help="check without a private word list (not a full release check)")
    p.add_argument("--allow-archive", action="append", default=[], metavar="PATH",
                   help="an archive path you checked by eye; the gate cannot read compressed content "
                        "(repeat for each path)")
    p.add_argument("--allow-email", action="append", default=[], metavar="ADDRESS", type=owner_email,
                   help="the owner's chosen address, allowed as an author, committer or tagger email; "
                        "an exact match, ASCII case ignored, no wildcard (repeat for each address)")
    a = p.parse_args(argv)
    deny = []
    if not a.no_denylist:
        path = a.denylist or os.path.join(a.repo, DENYLIST_NAME)
        if not os.path.isfile(path):
            print(f"no deny list at {path}. Pass --denylist PATH: a release check without the private "
                  "word list is not a release check. Use --no-denylist only if you mean it.", file=sys.stderr)
            return EXIT_CANNOT_CHECK
        try:
            deny = load_denylist(path)
        except (OSError, UnicodeDecodeError) as e:
            print(f"cannot read the deny list: {type(e).__name__}", file=sys.stderr)
            return EXIT_CANNOT_CHECK
    try:
        findings, commits, tags, blobs, paths, notes, binaries = run(a.repo, a.ref, deny,
                                                                     frozenset(a.allow_archive),
                                                                     frozenset(a.allow_email))
    except CannotCheck as e:
        print(f"cannot check: {e}", file=sys.stderr)
        return EXIT_CANNOT_CHECK
    if not findings:
        print(f"OK: {commits} commits, {tags} annotated tags, {notes} notes refs, {blobs} blobs and "
              f"{paths} paths checked; {len(deny)} deny-listed words")
    for line in findings:
        print(line)
    if binaries:
        print(BINARY_NOTE)
        for line in binaries:
            print("  " + line)
    if findings:
        print(VERDICT)
        return EXIT_FINDINGS
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
