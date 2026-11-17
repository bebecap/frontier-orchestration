"""Packaging: manifests agree, the skill is self-contained, and no private data ships."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import unittest

from helpers import read_json, read_text, ROOT, SKILL

import fo_config

MANIFESTS = {
    "claude": ".claude-plugin/plugin.json",
    "claude-market": ".claude-plugin/marketplace.json",
    "codex": ".codex-plugin/plugin.json",
    "codex-market": ".agents/plugins/marketplace.json",
    "agy": ".antigravity-plugin/plugin.json",
}


def load(rel: str) -> dict:
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return json.load(f)


def shipped_files() -> list:
    """Files git would ship (tracked or new, not ignored)."""
    out = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"],
                         cwd=ROOT, capture_output=True, text=True)
    if out.returncode != 0:  # not a git checkout (an unpacked release): walk the tree
        found = []
        for d, dirs, files in os.walk(ROOT):
            dirs[:] = [x for x in dirs if x not in (".git", "__pycache__")]
            found += [os.path.relpath(os.path.join(d, f), ROOT) for f in files]
        return found
    return [p for p in out.stdout.splitlines() if p]


class ManifestTests(unittest.TestCase):
    def test_versions_agree(self):
        versions = {
            "fo_config.VERSION": fo_config.VERSION,
            "claude": load(MANIFESTS["claude"])["version"],
            "codex": load(MANIFESTS["codex"])["version"],
            "agy": load(MANIFESTS["agy"])["version"],
            "claude-market entry": load(MANIFESTS["claude-market"])["plugins"][0]["version"],
            "npm": load("npm/package.json")["version"],
        }
        self.assertEqual(len(set(versions.values())), 1, versions)

    def test_names_agree(self):
        names = {load(MANIFESTS[k])["name"] for k in ("claude", "codex", "agy")}
        names.add(load(MANIFESTS["claude-market"])["plugins"][0]["name"])
        names.add(load(MANIFESTS["codex-market"])["plugins"][0]["name"])
        self.assertEqual(names, {"frontier-orchestration"})

    def test_marketplace_sources_point_at_repo_root(self):
        self.assertEqual(load(MANIFESTS["claude-market"])["plugins"][0]["source"], "./")
        self.assertEqual(load(MANIFESTS["codex-market"])["plugins"][0]["source"], {"source": "local", "path": "./"})
        self.assertEqual(load(MANIFESTS["codex"])["skills"], "./skills/")

    def test_skill_frontmatter(self):
        text = read_text(os.path.join(SKILL, "SKILL.md"))
        m = re.match(r"^---\nname: (\S+)\ndescription: >-\n((?:  .*\n)+)---\n", text)
        self.assertIsNotNone(m, "SKILL.md frontmatter must be name + folded description")
        self.assertEqual(m.group(1), "frontier-orchestration")
        self.assertLess(len(" ".join(m.group(2).split())), 1024)

    def test_skill_is_self_contained(self):
        """skills.sh copies only the skill folder: every referenced file must live inside it."""
        text = read_text(os.path.join(SKILL, "SKILL.md"))
        for rel in set(re.findall(r"`((?:scripts|references|data)/[\w./-]+)`", text)):
            self.assertTrue(os.path.exists(os.path.join(SKILL, rel)), f"SKILL.md names missing {rel}")
        for name in os.listdir(os.path.join(SKILL, "scripts")):
            if name.endswith(".py"):
                src = read_text(os.path.join(SKILL, "scripts", name))
                self.assertNotIn("CLAUDE_PLUGIN_ROOT", src, f"{name} must not depend on the Claude plugin root")

    def test_telemetry_collector_file(self):
        c = read_json(os.path.join(SKILL, "data", "telemetry.json"))
        self.assertTrue(c["endpoint"].startswith("https://"))
        self.assertTrue(c["project_key"] == "" or c["project_key"].startswith(("phc_", "fo_pub_")))

    def test_collector_schema_matches_client_allowlist(self):
        """The server re-applies the same allowlist: collector/schema.json must equal the client's."""
        import fo_telemetry as t
        s = read_json(os.path.join(ROOT, "collector", "schema.json"))
        self.assertEqual(s["schema"], t.SCHEMA_VERSION)
        self.assertEqual(s["events"], list(t.EVENTS))
        self.assertEqual(s["enums"], {k: list(v) for k, v in t.ENUMS.items()})
        self.assertEqual((s["ints"], s["bools"], s["via_keys"], s["via"]),
                         (list(t.INTS), list(t.BOOLS), list(t.VENDOR_KEYS), list(t.VIA)))
        self.assertEqual(s["strings"], {k: "^" + v + "$" for k, v in t.STRINGS.items()})
        for bad in ("", "vér", "٣.١", "1" * 21):
            self.assertNotIn("fo_version", t.clean("route", {"fo_version": bad})[1])
        self.assertNotIn("tasks", t.clean("route", {"tasks": 10001})[1])


# One set of rules for shipped files and for the history check: scripts/prepublish_check.py owns them.
sys.path.insert(0, os.path.join(ROOT, "scripts"))
from prepublish_check import ALLOW, SECRET_PATTERNS, review_reason, scan_data  # noqa: E402


def scan_files(root: str, files: list, deny: list) -> tuple:
    """(problems, binary files) for the given files under root. Bytes are scanned, so a file that
    is not UTF-8 is still checked; a symlink is checked by its target, as git ships it; a file
    that cannot be read is a problem, never a skip. Binary files are listed for review by eye."""
    problems, binary = [], []
    for rel in files:
        if rel in ALLOW or rel == ".privacy-denylist":
            continue
        full = os.path.join(root, rel)
        try:
            data = os.fsencode(os.readlink(full)) if os.path.islink(full) else open(full, "rb").read()
        except OSError:
            problems.append(f"{rel}: cannot read")
            continue
        if review_reason(os.fsencode(rel), data):
            binary.append(rel)
        problems += [f"{rel}: {rule}" for rule in scan_data(data, deny)]
    return problems, binary


class PrivacyTests(unittest.TestCase):
    """Generic leak patterns, plus an optional local deny list that is never committed.

    Put one private word per line in .privacy-denylist (git-ignored) to also block names of
    people, projects or customers without publishing that list.
    """

    def test_no_private_data(self):
        deny = []
        local = os.path.join(ROOT, ".privacy-denylist")
        if os.path.exists(local):
            deny = [w.strip() for w in read_text(local).splitlines() if w.strip() and not w.startswith("#")]
        problems, binary = scan_files(ROOT, shipped_files(), deny)
        self.assertEqual(problems, [], "\n".join(problems))
        # Binary files are scanned as bytes too, but compressed content needs a look by eye.
        self.assertEqual([b for b in binary if not b.startswith("assets/") and not b.startswith("docs/assets/")], [],
                         "a new binary file: review it by eye, then allow its folder here")

    def test_denylist_is_ignored(self):
        out = subprocess.run(["git", "check-ignore", "-q", ".privacy-denylist"], cwd=ROOT)
        if out.returncode == 128:
            self.skipTest("not a git checkout")
        self.assertEqual(out.returncode, 0, ".privacy-denylist must be in .gitignore")


if __name__ == "__main__":
    unittest.main()
