"""scripts/prepublish_check.py: the release gate that reads the whole git history.

Each test builds a tiny git repository in a temporary home, with no global or system git
config. Email fixtures are built at runtime so that this file passes the shipped-file privacy
test, and the deny-listed word is made up and lives only in a temporary file.
"""

from __future__ import annotations

import gzip
import io
import os
import re
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile

from helpers import ROOT, clean_env

SCRIPT = os.path.join(ROOT, "scripts", "prepublish_check.py")
AT = "@"
GH = "12345+dev" + AT + "users.noreply.github.com"
GH_PLAIN = "dev" + AT + "users.noreply.github.com"
ANTHROPIC = "noreply" + AT + "anthropic.com"
BAD = "dev" + AT + "corp.test"
WORD = "Zorbluxquint"  # made up; written only to a temporary deny list
WORD2 = "Z\u00f6rbquell"  # made up, with a non-ASCII letter: deny-listed word #2
KEY = "sk-" + "a1b2c3d4" * 3  # matches the API key pattern
# The smallest valid PNG (1x1, grey): it has NUL bytes, so the gate treats it as binary.
PNG = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010800000000"
                    "3a7e9b550000000a49444154789c63600000000200015d0a2db40000000049454e44ae426082")
VERDICT = "this history must not be published; publish the snapshot"


class Repo:
    """A temporary git repository with a deny list next to it (not inside it)."""

    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = self.tmp.name
        self.path = os.path.join(self.home, "repo")
        self.denylist = os.path.join(self.home, "denylist.txt")
        with open(self.denylist, "w", encoding="utf-8") as f:
            f.write("# private words\n\n" + WORD.lower() + "\n" + WORD2 + "\n")
        os.mkdir(self.path)
        self.git("init", "-q", "-b", "main")

    def env(self, author=GH, committer=GH_PLAIN):
        return clean_env(self.home, GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                         GIT_AUTHOR_NAME="Dev", GIT_AUTHOR_EMAIL=author,
                         GIT_COMMITTER_NAME="Dev", GIT_COMMITTER_EMAIL=committer)

    def git(self, *args, **who):
        out = subprocess.run(["git", "-C", self.path, *args], capture_output=True, text=True,
                             env=self.env(**who), timeout=60)
        if out.returncode != 0:
            raise AssertionError(out.stderr)
        return out.stdout

    def commit(self, files: dict, message="Add files", remove=(), **who):
        for rel, text in files.items():
            full = os.path.join(self.path, rel)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "wb") as f:
                f.write(text if isinstance(text, bytes) else text.encode("utf-8"))
            self.git("add", "-f", rel)
        for rel in remove:
            self.git("rm", "-q", rel)
        self.git("commit", "-q", "--allow-empty", "-m", message, **who)

    def raw(self, kind: str, data: bytes) -> str:
        """Write an object exactly as given (git would refuse some of them), return its id."""
        out = subprocess.run(["git", "-C", self.path, "hash-object", "-t", kind, "--literally", "-w", "--stdin"],
                             input=data, capture_output=True, env=self.env(), timeout=60)
        if out.returncode != 0:
            raise AssertionError(out.stderr.decode())
        return out.stdout.decode().strip()

    def raw_commit(self, headers: bytes, message: bytes) -> str:
        """A hand-made commit on top of HEAD, made the new tip of main."""
        tree, parent = self.git("rev-parse", "HEAD^{tree}", "HEAD").split()
        sha = self.raw("commit", b"tree " + tree.encode() + b"\nparent " + parent.encode() + b"\n"
                       + headers + b"\n" + message)
        self.git("update-ref", "refs/heads/main", sha)
        return sha

    def check(self, *args, denylist=True):
        extra = ["--denylist", self.denylist] if denylist else []
        return subprocess.run([sys.executable, "-I", SCRIPT, "--repo", self.path, *extra, *args],
                              capture_output=True, text=True, cwd=self.home, env=self.env(), timeout=60)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.tmp.cleanup()


TRAILER = "\n\nCo-Authored-By: Claude <" + ANTHROPIC + ">\n"
STAMP = b" 1700000000 +0000"
OK_ID = b"Dev <" + GH.encode() + b">" + STAMP


class GateCase(unittest.TestCase):
    def assertFails(self, out, rule):
        self.assertEqual(out.returncode, 1, out.stdout + out.stderr)
        self.assertIn(rule, out.stdout)
        self.assertEqual(out.stdout.splitlines()[-1], VERDICT)
        for secret in (WORD.lower(), WORD2.lower(), KEY):
            self.assertNotIn(secret, (out.stdout + out.stderr).lower())


class PrepublishCheckTests(GateCase):

    def test_clean_single_commit_passes(self):
        with Repo() as r:
            r.commit({"README.md": "Hello\n", "bin.dat": "x"}, "Add readme" + TRAILER)
            out = r.check()
            self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
            self.assertTrue(out.stdout.startswith("OK: 1 commits"), out.stdout)

    def test_co_authored_by_anthropic_noreply_passes(self):
        with Repo() as r:
            r.commit({"a.txt": "a\n"}, "One" + TRAILER, author=ANTHROPIC, committer=GH)
            r.commit({"b.txt": "b\n"}, "Two" + TRAILER)
            out = r.check()
            self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_bad_author_email_fails(self):
        with Repo() as r:
            r.commit({"a.txt": "a\n"}, author=BAD)
            out = r.check()
            self.assertFails(out, "identity: author email is not a noreply address")
            self.assertNotIn("committer", out.stdout)
            self.assertNotIn(BAD, out.stdout)

    def test_bad_committer_email_fails(self):
        with Repo() as r:
            r.commit({"a.txt": "a\n"})
            r.commit({"b.txt": "b\n"}, committer=BAD)
            out = r.check()
            self.assertFails(out, "identity: committer email is not a noreply address")
            self.assertNotIn("author", out.stdout)

    def test_bad_tagger_email_fails(self):
        with Repo() as r:
            r.commit({"a.txt": "a\n"})
            r.git("tag", "-a", "v1", "-m", "Release", committer=BAD)
            self.assertFails(r.check(), "tag identity: tagger email is not a noreply address")

    def test_claude_session_trailer_fails(self):
        with Repo() as r:
            r.commit({"a.txt": "a\n"}, "Fix\n\nClaude-Session: 0123abcd" + TRAILER)
            self.assertFails(r.check(), "message: Claude-Session trailer")

    def test_denylisted_word_in_message_fails(self):
        with Repo() as r:
            r.commit({"a.txt": "a\n"}, "Fix the " + WORD + " report")
            self.assertFails(r.check(), "message: deny-listed word #1")

    def test_email_in_message_fails(self):
        with Repo() as r:
            r.commit({"a.txt": "a\n"}, "Fix\n\nCo-Authored-By: Dev <" + BAD + ">\n")
            self.assertFails(r.check(), "message: an email address")

    def test_denylisted_word_in_old_file_fails(self):
        with Repo() as r:
            r.commit({"notes.md": "Call " + WORD.upper() + " on Monday\n"})
            r.commit({}, "Remove notes", remove=["notes.md"])
            out = r.check()
            self.assertFails(out, "file notes.md: deny-listed word #1")
            first = r.git("rev-list", "--max-parents=0", "HEAD").strip()
            self.assertTrue(out.stdout.startswith(first[:10]), out.stdout)

    def test_secret_pattern_in_file_fails(self):
        with Repo() as r:
            r.commit({"src/key.txt": "token = sk-" + "a1b2c3d4" * 3 + "\n"})
            self.assertFails(r.check(), "file src/key.txt: an API key")

    def test_allowed_files_are_not_scanned(self):
        with Repo() as r:
            r.commit({"tests/test_package.py": "x = '" + BAD + "'\n"})
            self.assertEqual(r.check().returncode, 0)

    def test_denylisted_word_in_path_is_withheld(self):
        with Repo() as r:
            r.commit({WORD + ".md": "hello\n"})
            self.assertFails(r.check(), "file (path withheld): path has deny-listed word #1")

    def test_committed_denylist_fails(self):
        with Repo() as r:
            r.commit({".privacy-denylist": "harmless\n"})
            r.commit({}, "Remove it", remove=[".privacy-denylist"])
            self.assertFails(r.check(), "file .privacy-denylist: the private deny list is committed")

    def test_committed_denylist_content_is_not_scanned(self):
        with Repo() as r:
            r.commit({".privacy-denylist": WORD + "\n" + KEY + "\n"})
            out = r.check()
            self.assertFails(out, "file .privacy-denylist: the private deny list is committed")
            self.assertEqual(len(out.stdout.splitlines()), 2, out.stdout)

    def test_missing_denylist_fails_closed(self):
        with Repo() as r:
            r.commit({"a.txt": "a\n"})
            out = r.check(denylist=False)
            self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
            self.assertIn("Pass --denylist PATH", out.stderr)
            out = r.check("--no-denylist", denylist=False)
            self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_unknown_ref_cannot_check(self):
        with Repo() as r:
            r.commit({"a.txt": "a\n"})
            out = r.check("--ref", "no-such-ref")
            self.assertEqual(out.returncode, 2, out.stdout + out.stderr)

    def test_one_source_for_patterns(self):
        """The shipped-file privacy test uses the script's rules, not a copy."""
        sys.path.insert(0, os.path.join(ROOT, "scripts"))
        import prepublish_check
        import test_package
        self.assertIs(test_package.SECRET_PATTERNS, prepublish_check.SECRET_PATTERNS)
        self.assertIs(test_package.ALLOW, prepublish_check.ALLOW)



class EveryPathTests(GateCase):
    """Finding 1: every path of every reachable commit is scanned, even when its content is a
    blob that another path already holds. Only the reads of blob content are de-duplicated."""

    def test_identical_content_pair_checks_both_paths(self):
        with Repo() as r:
            r.commit({"a.txt": "same\n", "z-" + WORD + ".txt": "same\n"})
            self.assertFails(r.check(), "file (path withheld): path has deny-listed word #1")

    def test_rename_checks_both_sides(self):
        with Repo() as r:
            r.commit({"a.txt": "same\n"})
            r.git("mv", "a.txt", "z-" + WORD + ".txt")
            r.commit({}, "Rename")
            out = r.check()
            self.assertFails(out, "file (path withheld): path has deny-listed word #1")
            self.assertTrue(out.stdout.startswith(r.git("rev-parse", "HEAD")[:10]), out.stdout)

    def test_deleted_file_path_is_checked(self):
        with Repo() as r:
            r.commit({"a.txt": "same\n", "old/" + WORD + ".txt": "same\n"})
            r.commit({}, "Remove it", remove=["old/" + WORD + ".txt"])
            self.assertFails(r.check(), "file (path withheld): path has deny-listed word #1")

    def test_secret_in_a_path_fails(self):
        with Repo() as r:
            r.commit({"a.txt": "same\n", "keys/" + KEY + ".txt": "same\n"})
            self.assertFails(r.check(), "file (path withheld): path has an API key")


class BytesTests(GateCase):
    """Finding 2: content that is not UTF-8 is still scanned; nothing is skipped silently."""

    def test_latin1_file_with_a_secret_fails(self):
        with Repo() as r:
            r.commit({"notes.txt": b"caf\xe9: token = " + KEY.encode() + b"\n"})
            self.assertFails(r.check(), "file notes.txt: an API key")

    def test_invalid_byte_next_to_a_denylisted_word_fails(self):
        with Repo() as r:
            r.commit({"notes.txt": b"\xff" + WORD.encode() + b"\xff\n"})
            self.assertFails(r.check(), "file notes.txt: deny-listed word #1")

    def test_latin1_denylisted_word_with_a_non_ascii_letter_fails(self):
        with Repo() as r:
            r.commit({"notes.txt": b"Call " + WORD2.encode("latin-1") + b"\n"})
            self.assertFails(r.check(), "file notes.txt: deny-listed word #2")

    def test_utf16_file_with_a_secret_fails(self):
        with Repo() as r:
            r.commit({"notes.txt": ("token = " + KEY + "\n").encode("utf-16")})
            out = r.check()
            self.assertFails(out, "file notes.txt: an API key")
            self.assertIn("review by eye", out.stdout)

    def test_small_png_passes_and_is_listed(self):
        with Repo() as r:
            r.commit({"assets/icon.png": PNG, "README.md": "Hi\n"})
            out = r.check()
            self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
            self.assertTrue(out.stdout.startswith("OK: 1 commits"), out.stdout)
            self.assertIn("binary files, review by eye", out.stdout)
            self.assertIn("assets/icon.png", out.stdout)
            self.assertNotIn("README.md", out.stdout)

    def test_secret_inside_a_binary_file_fails(self):
        with Repo() as r:
            r.commit({"data.bin": PNG + b"\0\0" + KEY.encode() + b"\0"})
            out = r.check()
            self.assertFails(out, "file data.bin: an API key")
            self.assertIn("binary files, review by eye", out.stdout)

    def test_large_blob_is_scanned_to_the_end(self):
        """No size cap: a secret after 3 MB of text is found."""
        with Repo() as r:
            r.commit({"big.txt": "x" * (3 << 20) + "\n" + KEY + "\n"})
            self.assertFails(r.check(), "file big.txt: an API key")

    def test_symlink_target_is_scanned(self):
        with Repo() as r:
            os.symlink("/Users" + "/somebody/notes", os.path.join(r.path, "link"))
            r.git("add", "link")
            r.commit({}, "Add link")
            self.assertFails(r.check(), "file link: a macOS home path")


class HistoryHardeningTests(GateCase):
    """Other places a history can carry private data, or hide it from the gate."""

    def test_tag_message_with_a_denylisted_word_fails(self):
        with Repo() as r:
            r.commit({"a.txt": "a\n"})
            r.git("tag", "-a", "v1", "-m", "Release for " + WORD)
            self.assertFails(r.check(), "tag message: deny-listed word #1")

    def test_tag_name_with_a_denylisted_word_fails(self):
        with Repo() as r:
            r.commit({"a.txt": "a\n"})
            r.git("tag", "for-" + WORD)  # lightweight: no tag object at all
            self.assertFails(r.check(), "tag (name withheld): name has deny-listed word #1")

    def test_notes_are_scanned(self):
        with Repo() as r:
            r.commit({"a.txt": "a\n"})
            r.git("notes", "add", "-m", "Ask " + WORD)
            self.assertFails(r.check(), "notes refs/notes/commits")

    def test_notes_commit_identity_is_checked(self):
        with Repo() as r:
            r.commit({"a.txt": "a\n"})
            r.git("notes", "add", "-m", "fine", committer=BAD)
            self.assertFails(r.check(), "identity: committer email is not a noreply address")

    def test_commit_encoding_header_is_used(self):
        with Repo() as r:
            r.commit({"a.txt": "a\n"})
            r.raw_commit(b"author " + OK_ID + b"\ncommitter " + OK_ID + b"\nencoding ISO-8859-1\n",
                         b"Thanks " + WORD2.encode("latin-1") + b"\n")
            self.assertFails(r.check(), "message: deny-listed word #2")

    def test_upper_case_email_in_signed_off_by_fails(self):
        with Repo() as r:
            r.commit({"a.txt": "a\n"}, "Fix\n\nSigned-off-by: Dev <" + BAD.upper() + ">\n")
            self.assertFails(r.check(), "message: an email address")

    def test_denylisted_word_case_and_unicode_variants_fail(self):
        fullwidth = "".join(chr(ord(c) + 0xFEE0) for c in WORD)
        variants = {
            "upper.txt": WORD.upper(),
            "fullwidth.txt": fullwidth,
            "zero-width.txt": WORD[:3] + "\u200d" + WORD[3:],
            "combining.txt": WORD[:2] + "\u0301" + WORD[2:],
            "cyrillic.txt": WORD.replace("o", "\u043e"),
            "greek.txt": WORD.replace("o", "\u03bf"),
            "decomposed.txt": "Zo\u0308rbquell",  # word #2 with a combining diaeresis
        }
        with Repo() as r:
            r.commit({name: "x " + text + " y\n" for name, text in variants.items()})
            out = r.check()
            self.assertFails(out, "deny-listed word")
            for name in variants:
                self.assertIn("file " + name + ": deny-listed word", out.stdout, name)

    def test_unparsable_identity_line_fails(self):
        with Repo() as r:
            r.commit({"a.txt": "a\n"})
            r.raw_commit(b"author Dev " + BAD.encode() + STAMP + b"\ncommitter " + OK_ID + b"\n", b"Odd\n")
            self.assertFails(r.check(), "identity: author line cannot be read")

    def test_mergetag_tagger_is_checked(self):
        with Repo() as r:
            r.commit({"a.txt": "a\n"})
            target = r.git("rev-parse", "HEAD").strip()
            tag = (b"object " + target.encode() + b"\ntype commit\ntag v1\ntagger Dev <"
                   + BAD.encode() + b">" + STAMP + b"\n\nRelease\n")
            r.raw_commit(b"author " + OK_ID + b"\ncommitter " + OK_ID + b"\nmergetag "
                         + tag.rstrip(b"\n").replace(b"\n", b"\n ") + b"\n", b"Merge v1\n")
            self.assertFails(r.check(), "mergetag identity: tagger email is not a noreply address")

    def test_submodule_fails_closed(self):
        with Repo() as r:
            r.commit({"a.txt": "a\n"})
            r.git("update-index", "--add", "--cacheinfo", "160000," + "1" * 40 + ",vendor/lib")
            r.commit({}, "Add a submodule")
            self.assertFails(r.check(), "file vendor/lib: a submodule (gitlink) the gate cannot check")

    def test_lfs_pointer_fails_closed(self):
        with Repo() as r:
            r.commit({"model.bin": "version https://git-lfs.github.com/spec/v1\noid sha256:"
                      + "0" * 64 + "\nsize 12\n"})
            self.assertFails(r.check(), "file model.bin: a Git LFS pointer; its content is not in the history")

    def test_replace_ref_cannot_hide_a_commit(self):
        with Repo() as r:
            r.commit({"notes.md": "Call " + WORD + "\n"})
            dirty = r.git("rev-parse", "HEAD").strip()
            r.git("checkout", "-q", "--orphan", "clean")
            r.git("rm", "-q", "-rf", ".")
            r.commit({"notes.md": "Call nobody\n"})
            r.git("replace", dirty, r.git("rev-parse", "HEAD").strip())
            self.assertFails(r.check("--ref", "main"), "file notes.md: deny-listed word #1")

    def test_grafts_cannot_check(self):
        with Repo() as r:
            r.commit({"notes.md": "Call " + WORD + "\n"})
            r.commit({"a.txt": "a\n"})
            with open(os.path.join(r.path, ".git", "info", "grafts"), "w") as f:
                f.write(r.git("rev-parse", "HEAD").strip() + "\n")
            out = r.check()
            self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
            self.assertIn("grafts", out.stderr)

    def test_shallow_clone_cannot_check(self):
        with Repo() as r:
            r.commit({"notes.md": "Call " + WORD + "\n"})
            r.commit({"a.txt": "a\n"})
            shallow = os.path.join(r.home, "shallow")
            r.git("clone", "-q", "--depth", "1", "file://" + r.path, shallow)
            out = subprocess.run([sys.executable, "-I", SCRIPT, "--repo", shallow, "--denylist", r.denylist],
                                 capture_output=True, text=True, cwd=r.home, env=r.env(), timeout=60)
            self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
            self.assertIn("shallow", out.stderr)

    def test_missing_blob_cannot_check(self):
        with Repo() as r:
            r.commit({"a.txt": "a\n"})
            blob = r.git("rev-parse", "HEAD:a.txt").strip()
            os.remove(os.path.join(r.path, ".git", "objects", blob[:2], blob[2:]))
            out = r.check()
            self.assertEqual(out.returncode, 2, out.stdout + out.stderr)


LFS_TEXT = b"version https://git-lfs.github.com/spec/v1\noid sha256:" + b"0" * 64 + b"\nsize 12\n"


def zip_bytes(name: str, data: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(name, data)
    return buf.getvalue()


def tar_bytes(name: str, data: bytes) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as t:
        info = tarfile.TarInfo(name)
        info.size = len(data)
        t.addfile(info, io.BytesIO(data))
    return buf.getvalue()


class PerEntryRuleTests(GateCase):
    """Round 3 finding: a rule that depends on the tree entry (mode or path) is checked for every
    entry. Only the read of a blob's content is shared between entries that hold the same blob."""

    def test_lfs_pointer_behind_a_symlink_with_the_same_bytes_still_fails(self):
        with Repo() as r:
            r.commit({"a.txt": "a\n"})
            blob = r.raw("blob", LFS_TEXT)
            r.git("update-index", "--add", "--cacheinfo", "120000," + blob + ",a-link")
            r.git("update-index", "--add", "--cacheinfo", "100644," + blob + ",z.bin")
            r.commit({}, "Same bytes as a symlink and as a file")
            out = r.check()
            self.assertFails(out, "file z.bin: a Git LFS pointer; its content is not in the history")
            self.assertNotIn("file a-link: a Git LFS pointer", out.stdout)

    def test_archive_under_an_allowed_path_does_not_clear_the_same_bytes_elsewhere(self):
        with Repo() as r:
            data = zip_bytes("inner.txt", b"hello\n")
            r.commit({"vendor/ok.zip": data, "z/copy.dat": data})
            out = r.check("--allow-archive", "vendor/ok.zip")
            self.assertFails(out, "file z/copy.dat: archive, review by eye")
            self.assertNotIn("file vendor/ok.zip: archive", out.stdout)


def b64_page(ext: str, size: int = 400) -> bytes:
    """A text file of the given kind with a `data:...;base64,` payload of `size` characters."""
    payload = "A" * size
    if ext == "md":
        return ("![x](data:image/png;base64," + payload + ")\n").encode()
    return ('<svg xmlns="http://www.w3.org/2000/svg"><image href="data:image/png;base64,'
            + payload + '"/></svg>\n').encode()


def entry_lines(out, shown: str) -> list:
    """The findings and review lines of one tree entry, with the commit id removed."""
    lines = []
    for ln in out.stdout.splitlines():
        sha, _, rest = ln.strip().partition(" ")
        if not re.fullmatch(r"[0-9a-f]{10}", sha):
            continue
        if rest.startswith("file " + shown + ":"):
            lines.append(rest)
        elif ln.startswith("  ") and rest.startswith(shown + " ("):
            lines.append("review " + rest)
    return sorted(lines)


class SharedBlobTests(GateCase):
    """Round 5 finding: the review reason was computed for the first path of a blob only, so
    fixtures/example.txt hid an identical preview.html with embedded data. Every rule that looks
    at a path or a mode now runs for every tree entry; only blob facts are shared."""

    def test_text_file_does_not_hide_embedded_data_in_the_same_bytes(self):
        for ext in ("html", "svg", "md"):
            for first, second in (("fixtures/example.txt", "preview." + ext),
                                  ("a/preview." + ext, "z/example.txt")):
                with self.subTest(ext=ext, first=first), Repo() as r:
                    data = b64_page(ext)
                    r.commit({first: data, second: data})
                    page = first if first.endswith(ext) else second
                    text = second if page == first else first
                    out = r.check()
                    self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
                    self.assertEqual(entry_lines(out, page), ["review " + page + " (embedded base64 data)"],
                                     out.stdout)
                    self.assertEqual(entry_lines(out, text), [], out.stdout)

    def test_each_line_is_printed_once_per_path_at_its_first_commit(self):
        with Repo() as r:
            r.commit({"a.txt": "token = " + KEY + "\n"})
            first = r.git("rev-parse", "HEAD").strip()
            r.commit({"a.txt": "token = " + KEY + " again\n", "b.txt": "token = " + KEY + "\n"})
            out = r.check()
            self.assertFails(out, "file a.txt: an API key")
            self.assertEqual([ln[:10] for ln in out.stdout.splitlines() if "file a.txt:" in ln], [first[:10]])
            self.assertEqual(len([ln for ln in out.stdout.splitlines() if "file b.txt:" in ln]), 1)

    # (name, entries as (path, mode), shared bytes, extra gate args). The table holds every rule of
    # entry_findings in scripts/prepublish_check.py that reads a path, an extension or a mode:
    # review list by extension, embedded data by extension, archive by name, archive skipped for a
    # symlink, --allow-archive by path, Git LFS pointer only for a regular file, the deny list file
    # name, a deny-listed word or a secret in a path, ALLOW (content not scanned) by path, a gitlink
    # by mode, and content rules reported at each path. Magic bytes is a content-only control.
    # A mode of "160000" points at the blob of the other entry.
    TABLE = (
        ("review by extension", (("a.txt", "100644"), ("z.pdf", "100644")), b"just text\n", ()),
        ("embedded data in html", (("a.txt", "100644"), ("z.html", "100644")), b64_page("html"), ()),
        ("embedded data in md", (("a.txt", "100644"), ("z.md", "100644")), b64_page("md"), ()),
        ("embedded data in svg, svg first", (("a.svg", "100644"), ("z.txt", "100644")), b64_page("svg"), ()),
        ("archive by name", (("a.txt", "100644"), ("z.zip", "100644")), b"hello\n", ()),
        ("archive by name, symlink first", (("a.zip", "120000"), ("z.zip", "100644")), b"target\n", ()),
        ("archive by magic, symlink first", (("a-link", "120000"), ("z.dat", "100644")),
         gzip.compress(b"hi\n"), ()),
        ("archive by magic, file first", (("a.dat", "100644"), ("z-link", "120000")),
         gzip.compress(b"hi\n"), ()),
        ("--allow-archive on the first path", (("a.zip", "100644"), ("z.zip", "100644")),
         zip_bytes("i.txt", b"hello\n"), ("--allow-archive", "a.zip")),
        ("--allow-archive on the second path", (("a.zip", "100644"), ("z.zip", "100644")),
         zip_bytes("i.txt", b"hello\n"), ("--allow-archive", "z.zip")),
        ("LFS pointer, symlink first", (("a-link", "120000"), ("z.bin", "100644")), LFS_TEXT, ()),
        ("LFS pointer, file first", (("a.bin", "100644"), ("z-link", "120000")), LFS_TEXT, ()),
        ("deny list file name second", (("a.txt", "100644"), ("z/.privacy-denylist", "100644")),
         b"harmless\n", ()),
        ("deny list file name first", ((".privacy-denylist", "100644"), ("z.txt", "100644")),
         ("token = " + KEY + "\n").encode(), ()),
        ("deny-listed word in path", (("a.txt", "100644"), ("z-" + WORD + ".txt", "100644")), b"same\n", ()),
        ("secret in path", (("a.txt", "100644"), ("keys/" + KEY + ".txt", "100644")), b"same\n", ()),
        ("ALLOW path second", (("a.txt", "100644"), ("tests/test_package.py", "100644")),
         ("token = " + KEY + "\n").encode(), ()),
        ("ALLOW path first", (("tests/test_package.py", "100644"), ("z.txt", "100644")),
         ("token = " + KEY + "\n").encode(), ()),
        ("content rule at each path", (("a.txt", "100644"), ("z.txt", "100644")),
         ("token = " + KEY + "\n").encode(), ()),
        ("symlink and file with a secret", (("a-link", "120000"), ("z.txt", "100644")),
         ("/Users" + "/somebody/x").encode(), ()),
        ("gitlink with the id of a blob", (("a-sub", "160000"), ("z.txt", "100644")),
         ("token = " + KEY + "\n").encode(), ()),
        ("magic bytes (control)", (("a.txt", "100644"), ("z.dat", "100644")), PNG, ()),
    )

    @staticmethod
    def build(r, entries, data):
        blob = r.raw("blob", data)
        for path, mode in entries:
            r.git("update-index", "--add", "--cacheinfo", mode + "," + blob + "," + path)
        r.commit({}, "Entries")

    def test_each_entry_gets_the_result_it_gets_alone(self):
        """For every path or mode rule: an entry that shares its blob with another entry, before or
        after it, gets exactly the findings it gets when it is the only entry in the commit."""
        sys.path.insert(0, os.path.join(ROOT, "scripts"))
        import prepublish_check as gate
        for name, entries, data, args in self.TABLE:
            with self.subTest(name), Repo() as both:
                self.build(both, entries, data)
                together = both.check(*args)
                self.assertIn(together.returncode, (0, 1), together.stdout + together.stderr)
                seen = []
                for entry in entries:
                    with Repo() as alone:
                        self.build(alone, [entry], data)
                        single = alone.check(*args)
                    shown = gate.show_path(entry[0].encode(), [WORD.lower(), WORD2])
                    expected = entry_lines(single, shown)
                    self.assertEqual(entry_lines(together, shown), expected,
                                     f"{entry[0]}: together\n{together.stdout}\nalone\n{single.stdout}")
                    seen += expected
                self.assertTrue(seen, f"{name}: the row checks nothing")


class FormatListTests(GateCase):
    """Every format the docstring and CONTRIBUTING.md name is listed or fails, as they say."""

    def test_every_magic_and_extension_is_listed(self):
        sys.path.insert(0, os.path.join(ROOT, "scripts"))
        import prepublish_check as gate
        files = {}
        for i, (kind, magic) in enumerate(gate.UNREADABLE_MAGIC):
            files["m%02d.dat" % i] = (magic + b" rest\n", kind)
        for data, kind in ((b"\0\0\0\x18ftypheic", "ISO media"), (b"RIFF\0\0\0\0WEBPVP8 ", "RIFF"),
                           (b"RIFF\0\0\0\0WAVEfmt ", "RIFF"), (b"RIFF\0\0\0\0AVI LIST", "RIFF"),
                           (b"\x80\x04\x95", "pickle"), (b"BM\x3a\0\0\0\0\0\0\0", "BMP image"),
                           (b"text\0with a NUL\n", "NUL bytes")):
            files["x-%s.dat" % len(files)] = (data, kind)
        for i, suffix in enumerate(gate.UNREADABLE_SUFFIXES):
            files["s%02d%s" % (i, suffix)] = (b"plain text\n", "binary format by extension")
        for suffix in gate.TEXT_WITH_DATA_SUFFIXES:
            ext = "md" if suffix in (".md", ".markdown") else "html"
            files["d200" + suffix] = (b64_page(ext, 200), "embedded base64 data")
            files["d199" + suffix] = (b64_page(ext, 199), "")
        with Repo() as r:
            r.commit({name: data for name, (data, _) in files.items()})
            out = r.check()
            self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
            for name, (_, kind) in files.items():
                got = entry_lines(out, name)
                if kind:
                    self.assertEqual(len(got), 1, name + "\n" + out.stdout)
                    self.assertIn(kind, got[0], name)
                else:
                    self.assertEqual(got, [], name)

    def test_every_archive_name_and_magic_fails(self):
        sys.path.insert(0, os.path.join(ROOT, "scripts"))
        import prepublish_check as gate
        files = {"n%02d%s" % (i, suffix): b"plain text\n" for i, suffix in enumerate(gate.ARCHIVE_SUFFIXES)}
        for i, magic in enumerate(gate.ARCHIVE_MAGIC):
            files["g%02d.dat" % i] = magic + b"rest\n"
        files["tar.dat"] = tar_bytes("a.txt", b"hello\n")
        files["bz2.dat"] = b"BZh91AY&SYrest"
        with Repo() as r:
            r.commit(files)
            out = r.check()
            for name in files:
                self.assertFails(out, "file " + name + ": archive, review by eye")


class RefNameTests(GateCase):
    """Round 3 finding: every ref name the gate walks is scanned and reported, text withheld."""

    def test_notes_ref_name_is_checked(self):
        with Repo() as r:
            r.commit({"a.txt": "a\n"})
            r.git("notes", "--ref", "for-" + WORD, "add", "-m", "fine")
            out = r.check()
            self.assertFails(out, "notes ref (name withheld): name has deny-listed word #1")

    def test_tag_name_with_an_email_fails(self):
        with Repo() as r:
            r.commit({"a.txt": "a\n"})
            r.git("tag", "-a", "rel-" + BAD, "-m", "Release")
            out = r.check()
            self.assertFails(out, "tag (name withheld): name has an email address")
            self.assertNotIn(BAD, out.stdout)

    def test_branch_name_is_checked(self):
        with Repo() as r:
            r.commit({"a.txt": "a\n"})
            r.git("branch", "fix-" + KEY)
            out = r.check()
            self.assertFails(out, "branch (name withheld): name has an API key")

    def test_remote_tracking_branch_name_is_checked(self):
        with Repo() as r:
            r.commit({"a.txt": "a\n"})
            r.git("update-ref", "refs/remotes/origin/fix-" + WORD, "HEAD")
            out = r.check()
            self.assertFails(out, "remote-tracking branch (name withheld): name has deny-listed word #1")

    def test_other_ref_through_a_tag_is_checked(self):
        with Repo() as r:
            r.commit({"a.txt": "a\n"})
            r.git("tag", "-a", "v1", "-m", "Release")
            r.git("update-ref", "refs/keep/" + WORD, r.git("rev-parse", "v1").strip())
            out = r.check()
            self.assertFails(out, "ref (name withheld): name has deny-listed word #1")

    def test_branch_outside_the_history_is_not_walked(self):
        with Repo() as r:
            r.commit({"a.txt": "a\n"})
            r.git("checkout", "-q", "--orphan", "other-" + WORD)
            r.commit({"b.txt": "b\n"})
            out = r.check("--ref", "main")
            self.assertEqual(out.returncode, 0, out.stdout + out.stderr)


class InvisibleCharacterTests(GateCase):
    """Round 3 finding: marks of combining class zero, variation selectors, format characters and
    every default-ignorable code point inside a deny-listed word do not hide it."""

    def test_invisible_and_mark_characters_inside_a_word_fail(self):
        hidden = {
            "cgj": "\u034f", "vs16": "\ufe0f", "zwsp": "\u200b", "wj": "\u2060", "shy": "\u00ad",
            "mvs": "\u180e", "vs-supplement": "\U000e0100", "tags": "\U000e0061\U000e0062\U000e007f",
            "rlo": "\u202e", "lri-pdi": "\u2066\u2069", "lrm": "\u200e", "hangul-filler": "\u3164",
            "spacing-mark": "\u0903", "enclosing-mark": "\u20dd", "bom": "\ufeff",
        }
        with Repo() as r:
            r.commit({name + ".txt": "x " + WORD[:4] + ch + WORD[4:] + " y\n" for name, ch in hidden.items()})
            out = r.check()
            self.assertFails(out, "deny-listed word #1")
            for name in hidden:
                self.assertIn("file " + name + ".txt: deny-listed word #1", out.stdout, name)


class UnreadableFormatTests(GateCase):
    """A file the gate cannot read as text is listed for review by eye, with or without NUL bytes."""

    HEAD = "binary files, review by eye"

    def listed(self, out, name, reason=""):
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn(self.HEAD, out.stdout)
        line = [ln for ln in out.stdout.splitlines() if name in ln and ln.startswith("  ")]
        self.assertEqual(len(line), 1, out.stdout)
        self.assertIn(reason, line[0])

    def test_nul_free_pdf_with_ascii85_text_is_listed(self):
        import base64
        body = base64.a85encode(b"BT /F1 12 Tf (hello) Tj ET") + b"~>"
        pdf = (b"%PDF-1.4\n1 0 obj\n<< /Filter /ASCII85Decode /Length " + str(len(body)).encode()
               + b" >>\nstream\n" + body + b"\nendstream\nendobj\n%%EOF\n")
        self.assertNotIn(b"\0", pdf)
        with Repo() as r:
            r.commit({"doc/paper.dat": pdf, "README.md": "Hi\n"})
            out = r.check()
            self.listed(out, "doc/paper.dat", "PDF")
            self.assertNotIn("README.md", out.stdout)

    def test_pdf_named_text_file_is_listed(self):
        with Repo() as r:
            r.commit({"notes.pdf": "just text\n"})
            self.listed(r.check(), "notes.pdf", "extension")

    def test_svg_with_a_large_base64_payload_is_listed(self):
        import base64
        payload = base64.b64encode(os.urandom(600)).decode()
        svg = '<svg xmlns="http://www.w3.org/2000/svg"><image href="data:image/png;base64,' + payload + '"/></svg>\n'
        with Repo() as r:
            r.commit({"logo.svg": svg, "plain.svg": '<svg xmlns="http://www.w3.org/2000/svg"/>\n',
                      "tiny.md": "![x](data:image/png;base64,iVBORw0KGgo=)\n"})
            out = r.check()
            self.listed(out, "logo.svg", "embedded base64 data")
            self.assertNotIn("plain.svg", out.stdout)
            self.assertNotIn("tiny.md", out.stdout)

    def test_docx_fails_closed_as_an_archive(self):
        with Repo() as r:
            r.commit({"report.docx": zip_bytes("word/document.xml", b"<w:document/>")})
            self.assertFails(r.check(), "file report.docx: archive, review by eye")

    def test_plain_text_file_is_not_listed(self):
        with Repo() as r:
            r.commit({"README.md": "Hi\n", "src/app.py": "print('x')\n"})
            out = r.check()
            self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
            self.assertNotIn(self.HEAD, out.stdout)


class ArchiveTests(GateCase):
    """Compressed content cannot be read, so an archive fails closed unless a person allowed it."""

    def test_archives_fail_closed(self):
        with Repo() as r:
            r.commit({"pack.zip": zip_bytes("a.txt", b"hello\n"), "data.gz": gzip.compress(b"hello\n"),
                      "bundle.tar": tar_bytes("a.txt", b"hello\n"), "hidden.dat": gzip.compress(b"hi\n"),
                      "empty.7z": b"", "README.md": "Hi\n"})
            out = r.check()
            for name in ("pack.zip", "data.gz", "bundle.tar", "hidden.dat", "empty.7z"):
                self.assertFails(out, "file " + name + ": archive, review by eye")
            self.assertNotIn("README.md", out.stdout)

    def test_allowed_archive_passes(self):
        with Repo() as r:
            r.commit({"pack.zip": zip_bytes("a.txt", b"hello\n")})
            self.assertEqual(r.check().returncode, 1)
            out = r.check("--allow-archive", "pack.zip")
            self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_symlink_named_like_an_archive_is_not_one(self):
        with Repo() as r:
            os.symlink("target.txt", os.path.join(r.path, "link.zip"))
            r.git("add", "link.zip")
            r.commit({"target.txt": "t\n"})
            out = r.check()
            self.assertEqual(out.returncode, 0, out.stdout + out.stderr)


OWNER = "kit" + AT + "owner.test"  # the owner's chosen address (made up); has a "k" for the Kelvin test


class AllowEmailTests(GateCase):
    """--allow-email ADDRESS: the owner may publish under one chosen address, passed explicitly."""

    def test_owner_address_passes_only_with_the_option(self):
        with Repo() as r:
            r.commit({"a.txt": "a\n"}, "One" + TRAILER, author=OWNER, committer=OWNER)
            out = r.check()
            self.assertFails(out, "identity: author email is not a noreply address")
            self.assertIn("identity: committer email is not a noreply address", out.stdout)
            self.assertNotIn(OWNER, out.stdout + out.stderr)
            out = r.check("--allow-email", OWNER)
            self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
            self.assertTrue(out.stdout.startswith("OK: 1 commits"), out.stdout)

    def test_match_ignores_case(self):
        with Repo() as r:
            r.commit({"a.txt": "a\n"}, author=OWNER.upper(), committer=OWNER)
            out = r.check("--allow-email", "Kit" + AT + "Owner.TEST")
            self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_option_covers_the_tagger(self):
        with Repo() as r:
            r.commit({"a.txt": "a\n"}, author=OWNER, committer=OWNER)
            r.git("tag", "-a", "v1", "-m", "Release", committer=OWNER)
            self.assertFails(r.check(), "tag identity: tagger email is not a noreply address")
            out = r.check("--allow-email", OWNER)
            self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_option_is_repeatable_and_keeps_noreply_allowed(self):
        with Repo() as r:
            other = "ann" + AT + "owner.test"
            r.commit({"a.txt": "a\n"}, author=OWNER, committer=GH)
            r.commit({"b.txt": "b\n"}, author=other, committer=ANTHROPIC)
            self.assertFails(r.check("--allow-email", OWNER), "author email is not a noreply address")
            out = r.check("--allow-email", OWNER, "--allow-email", other)
            self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_a_different_address_still_fails(self):
        with Repo() as r:
            r.commit({"a.txt": "a\n"}, author=BAD, committer=OWNER)
            out = r.check("--allow-email", OWNER)
            self.assertFails(out, "identity: author email is not a noreply address")
            self.assertNotIn("committer email", out.stdout)

    def test_near_misses_still_fail(self):
        near = {
            "prefix": "x" + OWNER,
            "suffix on the domain": OWNER + ".evil",
            "suffix on the local part": "kit2" + AT + "owner.test",
            "subdomain": "kit" + AT + "mail.owner.test",
            "look-alike domain": "kit" + AT + "owner.tst",
            "Cyrillic look-alike": "kit" + AT + "оwner.test",
            "Kelvin sign that lower() maps to k": "Kit" + AT + "owner.test",
            "wildcard-like text": "*" + AT + "owner.test",
        }
        for why, email in near.items():
            with self.subTest(why), Repo() as r:
                r.commit({"a.txt": "a\n"})
                r.raw_commit(b"author Dev <" + email.encode() + b">" + STAMP + b"\n"
                             b"committer Dev <" + GH.encode() + b">" + STAMP + b"\n", b"Near miss\n")
                self.assertFails(r.check("--allow-email", OWNER),
                                 "identity: author email is not a noreply address")

    def test_option_does_not_allow_the_address_in_a_message_or_a_file(self):
        with Repo() as r:
            r.commit({"a.txt": "Mail " + OWNER + "\n"}, "Fix\n\nSigned-off-by: Kit <" + OWNER + ">\n",
                     author=OWNER, committer=OWNER)
            out = r.check("--allow-email", OWNER)
            self.assertFails(out, "message: an email address")
            self.assertIn("file a.txt: an email address", out.stdout)
            self.assertNotIn("identity", out.stdout)

    def test_a_pattern_or_a_value_that_is_not_one_address_is_refused(self):
        with Repo() as r:
            r.commit({"a.txt": "a\n"}, author=OWNER, committer=OWNER)
            for value in ("*" + AT + "owner.test", "kit" + AT + "*.test", "kit?" + AT + "owner.test",
                          "owner.test", "kit" + AT + "owner.test kit" + AT + "owner.test",
                          "kit" + AT + AT + "owner.test", "Kit" + AT + "owner.test", ""):
                with self.subTest(value=value):
                    out = r.check("--allow-email", value)
                    self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
                    self.assertEqual(out.stdout, "")


class ClaimTests(GateCase):
    """Claims in the docstring and CONTRIBUTING.md that had no test of their own."""

    def test_identity_name_with_a_denylisted_word_fails(self):
        with Repo() as r:
            r.commit({"a.txt": "a\n"})
            r.raw_commit(b"author " + WORD.encode() + b" <" + GH.encode() + b">" + STAMP
                         + b"\ncommitter " + OK_ID + b"\n", b"Fine\n")
            self.assertFails(r.check(), "identity: author name has deny-listed word #1")

    def test_declared_encoding_that_is_not_latin1_is_used(self):
        """cp437 puts the o with two dots at 0x94; only the declared encoding reads it."""
        with Repo() as r:
            r.commit({"a.txt": "a\n"})
            r.raw_commit(b"author " + OK_ID + b"\ncommitter " + OK_ID + b"\nencoding cp437\n",
                         b"Thanks " + WORD2.encode("cp437") + b"\n")
            self.assertFails(r.check(), "message: deny-listed word #2")

    def test_utf32_file_with_a_secret_fails(self):
        with Repo() as r:
            r.commit({"notes.txt": ("token = " + KEY + "\n").encode("utf-32")})
            self.assertFails(r.check(), "file notes.txt: an API key")

    def test_session_trailer_in_any_case_in_a_tag_message_fails(self):
        with Repo() as r:
            r.commit({"a.txt": "a\n"})
            r.git("tag", "-a", "v1", "-m", "Release\n\n  claude-SESSION: 0123")
            self.assertFails(r.check(), "tag message: Claude-Session trailer")

    def test_not_a_git_repository_cannot_check(self):
        with Repo() as r:
            plain = os.path.join(r.home, "plain")
            os.mkdir(plain)
            out = subprocess.run([sys.executable, "-I", SCRIPT, "--repo", plain, "--denylist", r.denylist],
                                 capture_output=True, text=True, cwd=r.home,
                                 env={**r.env(), "GIT_CEILING_DIRECTORIES": r.home}, timeout=60)
            self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
            self.assertIn("not a git repository", out.stderr)

    def test_partial_clone_cannot_check_and_fetches_nothing(self):
        with Repo() as r:
            r.commit({"notes.md": "Call " + WORD + "\n"})
            r.git("config", "uploadpack.allowFilter", "true")
            blob = r.git("rev-parse", "HEAD:notes.md").strip()
            part = os.path.join(r.home, "partial")
            r.git("clone", "-q", "--no-checkout", "--filter=blob:none", "file://" + r.path, part)
            out = subprocess.run([sys.executable, "-I", SCRIPT, "--repo", part, "--denylist", r.denylist],
                                 capture_output=True, text=True, cwd=r.home, env=r.env(), timeout=60)
            self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
            self.assertIn("partial clone", out.stderr)
            have = subprocess.run(["git", "-C", part, "cat-file", "-e", blob], capture_output=True,
                                  env={**r.env(), "GIT_NO_LAZY_FETCH": "1"})
            self.assertNotEqual(have.returncode, 0, "the gate fetched the missing blob")


class PackageScannerTests(unittest.TestCase):
    """The shipped-file privacy test uses the same byte scanner."""

    def test_package_scanner_reads_bytes(self):
        import test_package
        with tempfile.TemporaryDirectory() as root:
            files = {"latin1.txt": b"caf\xe9 " + KEY.encode() + b"\n",
                     "word.txt": b"\xff" + WORD.encode() + b"\n",
                     "icon.png": PNG, "ok.md": b"fine\n"}
            for name, data in files.items():
                with open(os.path.join(root, name), "wb") as f:
                    f.write(data)
            problems, binary = test_package.scan_files(root, [*files, "gone.txt"], [WORD.lower()])
        text = "\n".join(problems)
        self.assertIn("latin1.txt: an API key", text)
        self.assertIn("word.txt: deny-listed word #1", text)
        self.assertIn("gone.txt: cannot read", text)
        self.assertNotIn("icon.png", text)
        self.assertNotIn("ok.md", text)
        self.assertEqual(binary, ["icon.png"])


if __name__ == "__main__":
    unittest.main()
